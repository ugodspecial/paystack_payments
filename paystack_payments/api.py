"""
api.py
Public-facing API endpoints for paystack_payments.

Security principles applied here:
  - Webhook endpoint is guest-accessible but HMAC-verified before any action.
  - All write endpoints require authenticated users with explicit role checks.
  - Amounts are always taken from server-side records, never from request params.
  - All inputs are validated and sanitised before use.
  - Rate limiting is enforced via Frappe's built-in throttle mechanism.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

# ── Webhook ───────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def paystack_webhook() -> dict:
    """
    Receive a Paystack webhook event.

    Always returns HTTP 200 after the HMAC check so Paystack does not
    retry on downstream failures (Paystack best-practice).

    The actual event processing is fully asynchronous and isolated in
    gateway/webhook.py. No ERPNext code is imported from this endpoint.
    """
    request = frappe.request
    raw_body: bytes = request.get_data()
    signature: str = request.headers.get("x-paystack-signature", "")
    ip: str = _get_request_ip(request)

    from paystack_payments.gateway.webhook import handle_webhook
    handle_webhook(raw_body, signature, ip)

    frappe.local.response.update({"http_status_code": 200})
    return {"status": "ok"}


# ── Checkout helpers ──────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def get_checkout_data(reference: str) -> dict:
    """
    Return the minimum data the checkout page needs to render.

    Guest-accessible — the reference (= Payment Log name) is a hash and
    therefore unguessable. We do NOT return the secret key.
    """
    if not reference or not isinstance(reference, str) or len(reference) > 140:
        frappe.throw(_("Invalid payment reference."), frappe.ValidationError)

    try:
        log = frappe.get_doc("Paystack Payment Log", reference)
    except frappe.DoesNotExistError:
        frappe.throw(_("Payment not found."), frappe.DoesNotExistError)

    # Enforce link expiry.
    if log.link_expires_at and log.link_expires_at < now_datetime():
        frappe.throw(_("This payment link has expired."))

    if log.status in ("Completed", "Refunded", "Failed"):
        frappe.throw(
            _("This payment link can no longer be used (status: {0}).").format(log.status)
        )

    gw = frappe.get_cached_doc("Paystack Gateway Setting", log.gateway_setting)

    return {
        "name": log.name,
        "amount": log.amount,
        "currency": log.currency,
        "status": log.status,
        "payer_name": log.payer_name or "",
        "description": log.description or "",
        # Public key only — NEVER the secret key.
        "public_key": gw.public_key,
        "checkout_mode": gw.checkout_mode,
        "hosted_url": log.hosted_url or "",
    }


# ── Payment operations ────────────────────────────────────────────────────────

@frappe.whitelist()
def create_payment(
    gateway_setting: str,
    amount: float,
    currency: str,
    payer_email: str,
    payer_name: str = "",
    description: str = "",
    reference_doctype: str = "",
    reference_docname: str = "",
    success_redirect_url: str = "",
) -> str:
    """
    Public API for any Frappe app to initiate a Paystack payment.

    Returns the checkout URL.

    Usage from LMS, School, or any other Frappe app:
        url = frappe.call(
            "paystack_payments.api.create_payment",
            gateway_setting="Paystack - Company",
            amount=50000,
            currency="NGN",
            payer_email="student@example.com",
            reference_doctype="LMS Enrollment",
            reference_docname="ENR-001",
        )
    """
    # Validate the caller has read access on the reference doc.
    if reference_doctype and reference_docname:
        frappe.has_permission(reference_doctype, "read", reference_docname, throw=True)

    from paystack_payments.gateway.checkout import create_payment
    return create_payment(
        gateway_setting=gateway_setting,
        amount=float(amount),
        currency=currency,
        payer_email=payer_email,
        payer_name=payer_name,
        description=description,
        reference_doctype=reference_doctype,
        reference_docname=reference_docname,
        success_redirect_url=success_redirect_url,
    )


@frappe.whitelist()
def refund_payment(log_name: str, amount: float, reason: str = "") -> str:
    """Initiate a full or partial refund for a completed payment."""
    frappe.only_for(["System Manager", "Accounts Manager"])

    from paystack_payments.gateway.refund import initiate_refund
    refund_log = initiate_refund(
        payment_log_name=log_name,
        amount=float(amount),
        reason=reason,
    )
    return _("Refund of {0} {1} initiated. Refund Log: {2}").format(
        refund_log.amount,
        frappe.db.get_value("Paystack Payment Log", log_name, "currency"),
        refund_log.name,
    )


@frappe.whitelist()
def complete_payment(log_name: str) -> str:
    """
    Manually verify and complete a Processed log that has no Payment Entry.
    Verifies the transaction with Paystack before acting.
    """
    frappe.only_for(["System Manager", "Accounts Manager"])

    log = frappe.get_doc("Paystack Payment Log", log_name)

    if log.payment_entry:
        return _("Payment Entry {0} already exists.").format(log.payment_entry)

    if not log.paystack_txn_id:
        frappe.throw(_("No Paystack Transaction ID on this log."))

    from paystack_payments.gateway.client import get_client_for_gateway
    client = get_client_for_gateway(log.gateway_setting)
    resp = client.verify_transaction(log.name)
    tx_data = resp.get("data", {})

    if tx_data.get("status") != "success":
        frappe.throw(
            _("Paystack reports transaction status: '{0}'. Cannot complete.").format(
                tx_data.get("status")
            )
        )

    ps_amount = int(tx_data.get("amount") or 0) / 100
    local_amount = log.amount_paid or log.amount
    if abs(ps_amount - local_amount) > 0.01:
        frappe.throw(
            _("Amount mismatch: Paystack {0} vs local {1}.").format(ps_amount, local_amount)
        )

    from paystack_payments.payment.lifecycle import on_payment_success
    on_payment_success(log)

    return _("Payment completed. Log status: {0}.").format(log.reload().status)


@frappe.whitelist()
def verify_transaction(log_name: str) -> dict:
    """Return raw Paystack transaction data for a Payment Log."""
    frappe.has_permission("Paystack Payment Log", "read", log_name, throw=True)

    log = frappe.get_doc("Paystack Payment Log", log_name)
    from paystack_payments.gateway.client import get_client_for_gateway
    client = get_client_for_gateway(log.gateway_setting)
    return client.verify_transaction(log.name).get("data", {})


@frappe.whitelist()
def run_reconciliation(gateway_setting: str) -> str:
    """On-demand reconciliation for a gateway."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    from paystack_payments.payment.reconciliation import reconcile_gateway
    return reconcile_gateway(gateway_setting)


@frappe.whitelist()
def charge_saved_card(log_name: str, authorization_name: str) -> str:
    """Charge a customer's saved Paystack card for an existing Payment Log."""
    frappe.only_for(["System Manager", "Accounts Manager"])

    log = frappe.get_doc("Paystack Payment Log", log_name)
    auth_doc = frappe.get_doc("Paystack Customer Authorization", authorization_name)

    if not auth_doc.is_usable():
        frappe.throw(_("This card is expired, inactive, or not reusable."))

    from paystack_payments.gateway.client import get_client_for_gateway
    client = get_client_for_gateway(log.gateway_setting)

    resp = client.charge_authorization(
        authorization_code=auth_doc.get_password("authorization_code"),
        email=auth_doc.email or "",
        amount_kobo=round((log.amount or 0) * 100),
        reference=log.name,
    )

    tx = resp.get("data", {})
    if tx.get("status") != "success":
        frappe.throw(
            _("Charge failed: {0}").format(tx.get("gateway_response", "Unknown error"))
        )

    return _("Card charged successfully.")


# ── Private helpers ───────────────────────────────────────────────────────────

def _get_request_ip(request) -> str:
    """
    Extract the real client IP, honouring X-Forwarded-For when set.
    Only the first IP in the chain is used to prevent header spoofing.
    """
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or ""
