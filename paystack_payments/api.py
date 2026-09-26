"""
api.py
All public API endpoints for paystack_payments.
Webhook URL: /api/method/paystack_payments.api.paystack_webhook
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

# ── Webhook (guest) ───────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def paystack_webhook():
    """
    Receives webhook events from Paystack.
    Responds 200 immediately after signature check (Paystack requirement).
    """

    request = frappe.request
    raw_body = request.get_data()
    signature = request.headers.get("x-paystack-signature", "")
    ip = request.remote_addr or ""

    from paystack_payments.utils.webhook import handle_webhook

    handle_webhook(raw_body, signature, ip)
    frappe.local.response.update({"http_status_code": 200})
    return {"status": "ok"}


# ── Checkout page ─────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def get_payment_log(reference: str) -> dict:
    """
    Used by the /paystack-checkout page to load log details.
    Rate-limited to 30/IP/min by hooks.
    """
    log = frappe.get_doc("Paystack Payment Log", reference)
    gw = frappe.get_doc("Paystack Gateway Setting", log.gateway_setting)

    # Reject expired or settled links
    if log.link_expires_at and log.link_expires_at < now_datetime():
        frappe.throw(_("This payment link has expired."))
    if log.status in ("Completed", "Refunded", "Failed"):
        frappe.throw(_("This payment link can no longer be paid."))

    return {
        "name": log.name,
        "amount": log.amount,
        "currency": log.currency,
        "status": log.status,
        "payer_name": log.payer_name,
        "description": log.reference_name or "",
        "public_key": gw.public_key,
        "checkout_mode": gw.checkout_mode,
        "hosted_url": log.hosted_url,
    }


# ── Payment operations ────────────────────────────────────────────────────────

@frappe.whitelist()
def complete_payment(log_name: str) -> str:
    """
    Manually complete a Processed/Needs Attention log that has no Payment Entry.
    Verifies the capture against Paystack first.
    """
    frappe.only_for(["System Manager", "Accounts Manager"])

    log = frappe.get_doc("Paystack Payment Log", log_name)
    if log.payment_entry:
        return _("Payment Entry {0} already exists.").format(log.payment_entry)
    if not log.paystack_txn_id:
        frappe.throw(_("No Paystack Transaction ID on this log."))

    gw = frappe.get_doc("Paystack Gateway Setting", log.gateway_setting)
    from paystack_payments.utils.paystack_client import PaystackClient

    client = PaystackClient(gw.get_password("secret_key"), gw.test_mode)
    resp = client.verify_transaction(log.name)  # reference = log name

    data = resp.get("data", {})
    if data.get("status") != "success":
        frappe.throw(_("Paystack reports status: {0}").format(data.get("status")))

    ps_amount = (data.get("amount") or 0) / 100
    if abs(ps_amount - (log.amount_paid or log.amount)) > 0.01:
        frappe.throw(
            _(
                "Amount mismatch: Paystack {0} vs local {1}."
            ).format(ps_amount, log.amount_paid or log.amount)
        )

    log.settle_payment_request()
    return _("Payment Entry {0} created.").format(log.payment_entry)


@frappe.whitelist()
def refund_payment(log_name: str, amount: float, reason: str = "") -> str:
    """Create and submit a Paystack refund."""
    frappe.only_for(["System Manager", "Accounts Manager"])

    log = frappe.get_doc("Paystack Payment Log", log_name)
    if log.status not in ("Completed", "Partially Refunded"):
        frappe.throw(_("Cannot refund a log in status {0}.").format(log.status))

    gw = frappe.get_doc("Paystack Gateway Setting", log.gateway_setting)
    from paystack_payments.utils.paystack_client import PaystackClient

    client = PaystackClient(gw.get_password("secret_key"), gw.test_mode)
    resp = client.refund(
        transaction=log.paystack_txn_id,
        amount_kobo=int(float(amount) * 100),
        reason=reason,
    )

    if not resp.get("status"):
        frappe.throw(_("Paystack refund failed: {0}").format(resp.get("message")))

    # Create a pending Refund Log; will be completed on refund.processed webhook
    rl = frappe.get_doc(
        {
            "doctype": "Paystack Refund Log",
            "payment_log": log_name,
            "amount": amount,
            "currency": log.currency,
            "reason": reason,
            "status": "Pending",
        }
    )
    rl.insert(ignore_permissions=True)
    frappe.db.commit()
    return _("Refund of {0} {1} initiated.").format(amount, log.currency)


@frappe.whitelist()
def verify_transaction(log_name: str) -> dict:
    """Return the raw Paystack transaction data for a log."""
    log = frappe.get_doc("Paystack Payment Log", log_name)
    frappe.has_permission("Paystack Payment Log", "read", doc=log, throw=True)

    gw = frappe.get_doc("Paystack Gateway Setting", log.gateway_setting)
    from paystack_payments.utils.paystack_client import PaystackClient

    client = PaystackClient(gw.get_password("secret_key"), gw.test_mode)
    return client.verify_transaction(log.name).get("data", {})


@frappe.whitelist()
def charge_saved_card(log_name: str, authorization_name: str) -> str:
    """Charge a customer's saved Paystack card."""
    frappe.only_for(["System Manager", "Accounts Manager"])

    log = frappe.get_doc("Paystack Payment Log", log_name)
    auth = frappe.get_doc("Paystack Customer Authorization", authorization_name)

    if not auth.is_usable():
        frappe.throw(_("This card is not usable (expired, inactive, or non-reusable)."))

    gw = frappe.get_doc("Paystack Gateway Setting", log.gateway_setting)
    from paystack_payments.utils.paystack_client import PaystackClient

    client = PaystackClient(gw.get_password("secret_key"), gw.test_mode)
    resp = client.charge_authorization(
        authorization_code=auth.get_password("authorization_code"),
        email=auth.email,
        amount_kobo=int((log.amount or 0) * 100),
        reference=log.name,
    )

    data = resp.get("data", {})
    if data.get("status") != "success":
        frappe.throw(
            _("Charge failed: {0}").format(data.get("gateway_response", "Unknown error"))
        )

    return _("Card charged successfully.")


# ── Reconciliation ────────────────────────────────────────────────────────────

@frappe.whitelist()
def run_reconciliation(gateway_setting: str) -> str:
    """
    Compare the last two days of Payment Logs against Paystack.
    Creates/updates Paystack Reconciliation Log records.
    """
    frappe.only_for(["System Manager", "Accounts Manager"])

    from frappe.utils import add_days, today

    gw = frappe.get_doc("Paystack Gateway Setting", gateway_setting)
    from paystack_payments.utils.paystack_client import PaystackClient

    client = PaystackClient(gw.get_password("secret_key"), gw.test_mode)

    logs = frappe.get_all(
        "Paystack Payment Log",
        filters={
            "gateway_setting": gateway_setting,
            "status": ["in", ["Processed", "Completed", "Needs Attention"]],
            "creation": [">", add_days(today(), -2)],
        },
        fields=["name", "paystack_txn_id", "amount_paid"],
    )

    reconciled = mismatch = pending = 0

    for row in logs:
        if not row.paystack_txn_id:
            pending += 1
            continue
        try:
            txn = client.verify_transaction(row.name).get("data", {})
            ps_amount = (txn.get("amount") or 0) / 100
            if abs(ps_amount - (row.amount_paid or 0)) > 0.01:
                rec_status = "Mismatch"
                reason = f"Paystack {ps_amount} vs local {row.amount_paid}"
                mismatch += 1
            elif txn.get("status") == "success":
                rec_status = "Reconciled"
                reason = ""
                reconciled += 1
            else:
                rec_status = "Pending"
                reason = f"Paystack status: {txn.get('status')}"
                pending += 1

            # Upsert reconciliation log
            existing = frappe.db.get_value(
                "Paystack Reconciliation Log", {"payment_log": row.name}, "name"
            )
            if existing:
                frappe.db.set_value(
                    "Paystack Reconciliation Log",
                    existing,
                    {
                        "status": rec_status,
                        "paystack_amount": ps_amount,
                        "local_amount": row.amount_paid,
                        "mismatch_reason": reason,
                    },
                )
            else:
                frappe.get_doc(
                    {
                        "doctype": "Paystack Reconciliation Log",
                        "payment_log": row.name,
                        "status": rec_status,
                        "paystack_amount": ps_amount,
                        "local_amount": row.amount_paid,
                        "mismatch_reason": reason,
                    }
                ).insert(ignore_permissions=True)
        except Exception as exc:  # noqa: BLE001
            frappe.log_error(title=f"Reconciliation error for {row.name}", message=str(exc))
            pending += 1

    frappe.db.commit()
    return _(
        "Reconciliation complete: {0} reconciled, {1} mismatches, {2} pending."
    ).format(reconciled, mismatch, pending)


# ── Gateway controller helper ─────────────────────────────────────────────────

@frappe.whitelist()
def get_gateway_controller(gateway_name: str) -> str:
    """Return the gateway controller name for a given Payment Gateway."""
    return frappe.db.get_value("Payment Gateway", gateway_name, "gateway_controller") or ""
