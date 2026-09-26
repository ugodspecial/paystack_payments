"""
events/payment_request.py
Doc events for the `Payment Request` doctype (from the payments app).
Triggered via hooks.py > doc_events.
No ERPNext imports.
"""

import frappe


def on_submit(doc, method=None):
    """
    When a Payment Request backed by the Paystack gateway is submitted,
    ensure a Payment Log exists.  The checkout URL was already created
    by get_payment_url() during Payment Request creation.
    """
    if not _is_paystack_gateway(doc):
        return

    existing = frappe.db.get_value(
        "Paystack Payment Log", {"payment_request": doc.name}, "name"
    )
    if existing:
        return  # Already created during get_payment_url

    # Fallback: create the log now (e.g. if PR was created without going through
    # our gateway controller — shouldn't happen in normal flow, but be defensive)
    gw_setting = _get_gateway_setting(doc)
    if not gw_setting:
        return

    from paystack_payments.utils.checkout import (
        create_payment_log_and_url,
    )

    create_payment_log_and_url(
        gateway_setting=gw_setting,
        payment_request_name=doc.name,
        amount=doc.grand_total,
        currency=doc.currency,
        payer_email=doc.email_to or "",
        payer_name=doc.party_name or "",
        reference_doctype=doc.reference_doctype,
        reference_name=doc.reference_name,
    )


def on_cancel(doc, method=None):
    """
    Cancel any pending/processed Payment Logs for this Payment Request.
    """
    if not _is_paystack_gateway(doc):
        return

    logs = frappe.get_all(
        "Paystack Payment Log",
        filters={
            "payment_request": doc.name,
            "status": ["in", ["Pending", "Processed"]],
        },
        pluck="name",
    )
    for name in logs:
        frappe.db.set_value("Paystack Payment Log", name, "status", "Failed")

    if logs:
        frappe.db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_paystack_gateway(doc) -> bool:
    gw = getattr(doc, "payment_gateway", None)
    return bool(gw and "Paystack" in gw)


def _get_gateway_setting(doc) -> str | None:
    gw = getattr(doc, "payment_gateway", None)
    if not gw:
        return None
    return frappe.db.get_value("Payment Gateway", gw, "gateway_controller")
