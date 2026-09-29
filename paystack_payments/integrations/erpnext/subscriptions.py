"""
integrations/erpnext/subscriptions.py
Auto-charges outstanding ERPNext Subscription invoices using saved Paystack cards.

Only runs when ERPNext is installed and the gateway has
auto_charge_subscription_invoices = 1.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, today

from paystack_payments.gateway.client import get_client_for_gateway
from paystack_payments.integrations.erpnext.adapter import require_erpnext


def collect_subscription_invoices() -> None:
    """
    Called by the daily scheduler job (tasks.py).
    Iterates all enabled gateways with auto-charge enabled.
    """
    require_erpnext()

    # Guard: Sales Invoice and Subscription Invoice are ERPNext-only.
    if not (
        frappe.db.table_exists("tabSales Invoice")
        and frappe.db.table_exists("tabSubscription Invoice")
    ):
        return

    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1, "auto_charge_subscription_invoices": 1},
        fields=["name", "currency"],
    )

    for gw in gateways:
        try:
            _collect_for_gateway(gw)
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack subscription collection error (gateway {gw['name']})",
                message=frappe.get_traceback(),
            )


def _collect_for_gateway(gw: dict) -> None:
    cutoff = add_days(today(), -30)

    gw_doc = frappe.get_doc("Paystack Gateway Setting", gw["name"])

    invoices = frappe.db.sql(
        """
        SELECT si.name, si.customer, si.grand_total, si.currency
        FROM `tabSales Invoice` si
        INNER JOIN `tabSubscription Invoice` ssi ON ssi.invoice = si.name
        WHERE
            si.company = %(company)s
            AND si.docstatus = 1
            AND si.status NOT IN ('Paid', 'Cancelled')
            AND si.outstanding_amount > 0
            AND si.posting_date >= %(cutoff)s
            AND NOT EXISTS (
                SELECT 1 FROM `tabPaystack Payment Log` pl
                WHERE pl.reference_doctype = 'Sales Invoice'
                  AND pl.reference_docname = si.name
                  AND pl.status IN ('Pending', 'Processed', 'Completed')
            )
        LIMIT 50
        """,
        {"company": gw_doc.company, "cutoff": cutoff},
        as_dict=True,
    )

    client = get_client_for_gateway(gw["name"])

    for inv in invoices:
        auth = frappe.db.get_value(
            "Paystack Customer Authorization",
            {
                "customer": inv.customer,
                "is_active": 1,
                "is_reusable": 1,
            },
            ["name", "email"],
            as_dict=True,
            order_by="creation desc",
        )
        if not auth:
            continue

        auth_doc = frappe.get_doc("Paystack Customer Authorization", auth.name)
        if not auth_doc.is_usable():
            continue

        # Create the Payment Log first (its name = Paystack reference).
        log = frappe.get_doc(
            {
                "doctype": "Paystack Payment Log",
                "gateway_setting": gw["name"],
                "currency": inv.currency,
                "amount": inv.grand_total,
                "payer_email": auth.email or "",
                "reference_doctype": "Sales Invoice",
                "reference_docname": inv.name,
                "status": "Pending",
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()

        try:
            resp = client.charge_authorization(
                authorization_code=auth_doc.get_password("authorization_code"),
                email=auth.email or "",
                amount_kobo=round(inv.grand_total * 100),
                reference=log.name,
            )
            if resp.get("data", {}).get("status") == "success":
                frappe.logger("paystack").info(
                    "Auto-charged subscription invoice %s via Paystack", inv.name
                )
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack: subscription auto-charge failed for invoice {inv.name}",
                message=frappe.get_traceback(),
            )
