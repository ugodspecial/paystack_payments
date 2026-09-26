"""
tasks.py
Scheduled background jobs registered in hooks.py > scheduler_events.
No ERPNext imports.
"""

import frappe
from frappe.utils import add_days, now_datetime, today


def retry_pending_captures():
    """
    Every 10 minutes: re-drive Processed logs that have no Payment Entry.
    Uses exponential back-off (stored on the log).
    """
    now = now_datetime()
    logs = frappe.get_all(
        "Paystack Payment Log",
        filters={
            "status": ["in", ["Processed", "Needs Attention"]],
            "payment_entry": ["is", "not set"],
            "next_retry_at": ["<=", now],
        },
        pluck="name",
        limit=200,
    )
    for name in logs:
        frappe.enqueue(
            "paystack_payments.tasks._settle_one",
            queue="default",
            name=name,
            now=frappe.flags.in_test,
        )


def _settle_one(name: str):
    log = frappe.get_doc("Paystack Payment Log", name)
    try:
        log.settle_payment_request()
    except Exception as exc:  # noqa: BLE001
        log.errors = str(exc)
        log.schedule_retry()


def retry_pending_settlements():
    """
    Hourly: re-drive Paystack Settlements that have no Journal Entry.
    Retries for up to 30 days.
    """
    cutoff = add_days(today(), -30)
    settlements = frappe.get_all(
        "Paystack Settlement",
        filters={
            "status": "Failed",
            "creation": [">", cutoff],
        },
        pluck="name",
        limit=50,
    )
    for name in settlements:
        frappe.enqueue(
            "paystack_payments.tasks._book_settlement",
            queue="default",
            name=name,
            now=frappe.flags.in_test,
        )


def _book_settlement(name: str):
    settlement = frappe.get_doc("Paystack Settlement", name)
    settlement.book_journal_entry()


def daily_reconciliation():
    """
    Daily: reconcile the last two days of payment logs across all enabled gateways.
    """
    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1},
        pluck="name",
    )
    for gw_name in gateways:
        try:
            from paystack_payments.api import run_reconciliation

            run_reconciliation(gateway_setting=gw_name)
        except Exception as exc:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack daily reconciliation error ({gw_name})",
                message=str(exc),
            )


def collect_subscription_invoices():
    """
    Daily: charge subscription invoices via saved card for gateways
    that have auto_charge_subscription_invoices enabled.
    MAX 50 invoices per company per run.
    """
    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1, "auto_charge_subscription_invoices": 1},
        fields=["name", "company", "currency"],
    )

    for gw in gateways:
        _collect_for_gateway(gw)


def _collect_for_gateway(gw: dict):
    from frappe.utils import add_days

    # Sales Invoice and Subscription Invoice are ERPNext-only doctypes.
    # Skip silently on non-ERPNext sites so the scheduler job doesn't crash.
    if not (
        frappe.db.table_exists("tabSales Invoice")
        and frappe.db.table_exists("tabSubscription Invoice")
    ):
        return

    cutoff = add_days(today(), -30)

    invoices = frappe.db.sql(
        """
        SELECT si.name, si.customer, si.grand_total, si.currency
        FROM `tabSales Invoice` si
        INNER JOIN `tabSubscription Invoice` ssi ON ssi.invoice = si.name
        WHERE
            si.company = %(company)s
            AND si.docstatus = 1
            AND si.status != 'Paid'
            AND si.posting_date >= %(cutoff)s
            AND NOT EXISTS (
                SELECT 1 FROM `tabPaystack Payment Log` pl
                WHERE pl.reference_doctype = 'Sales Invoice'
                  AND pl.reference_name = si.name
                  AND pl.status IN ('Pending', 'Processed', 'Completed')
            )
        LIMIT 50
        """,
        {"company": gw["company"], "cutoff": cutoff},
        as_dict=True,
    )

    charged = 0
    for inv in invoices:
        if charged >= 50:
            break

        auth = frappe.db.sql(
            """
            SELECT name, authorization_code, email
            FROM `tabPaystack Customer Authorization`
            WHERE customer = %(customer)s
              AND is_active = 1
              AND is_reusable = 1
            ORDER BY creation DESC
            LIMIT 1
            """,
            {"customer": inv.customer},
            as_dict=True,
        )
        if not auth:
            continue

        auth = auth[0]
        auth_doc = frappe.get_doc("Paystack Customer Authorization", auth.name)
        if not auth_doc.is_usable():
            continue

        try:
            from paystack_payments.utils.paystack_client import PaystackClient

            gw_doc = frappe.get_doc("Paystack Gateway Setting", gw["name"])

            # insert() returns the document itself (not None); .name is safe
            log_doc = frappe.get_doc(
                {
                    "doctype": "Paystack Payment Log",
                    "gateway_setting": gw["name"],
                    "company": gw["company"],
                    "currency": inv.currency,
                    "amount": inv.grand_total,
                    "reference_doctype": "Sales Invoice",
                    "reference_name": inv.name,
                    "payer_email": auth.email,
                    "status": "Pending",
                }
            ).insert(ignore_permissions=True)
            frappe.db.commit()

            client = PaystackClient(gw_doc.get_password("secret_key"), gw_doc.test_mode)
            resp = client.charge_authorization(
                authorization_code=auth_doc.get_password("authorization_code"),
                email=auth.email,
                amount_kobo=int(inv.grand_total * 100),
                reference=log_doc.name,
            )
            data = resp.get("data", {})
            if data.get("status") == "success":
                charged += 1
        except Exception as exc:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack subscription charge error (inv {inv.name})",
                message=str(exc),
            )
