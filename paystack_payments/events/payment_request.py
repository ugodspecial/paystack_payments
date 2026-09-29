"""
events/payment_request.py
Doc events for the Payment Request doctype (from the payments app).

Only registered when the site has the payments app installed.
Bridges the payments-app Payment Request submission to our checkout flow.
No ERPNext imports at module level.
"""

from __future__ import annotations

import frappe


def on_submit(doc, method=None) -> None:
    """
    When a Paystack-backed Payment Request is submitted, ensure a Payment Log
    and checkout URL exist. The controller's get_payment_url() should have
    already created the log, but this is a defensive fallback.
    """
    if not _is_paystack_gateway(doc):
        return

    existing = frappe.db.get_value(
        "Paystack Payment Log", {"reference_doctype": "Payment Request",
                                  "reference_docname": doc.name}, "name"
    )
    if existing:
        return

    gw_setting = _get_gateway_setting(doc)
    if not gw_setting:
        return

    from paystack_payments.gateway.checkout import create_payment
    create_payment(
        gateway_setting=gw_setting,
        amount=float(doc.grand_total or 0),
        currency=doc.currency or "NGN",
        payer_email=doc.email_to or "",
        payer_name=doc.party_name or "",
        description=f"Payment Request {doc.name}",
        reference_doctype="Payment Request",
        reference_docname=doc.name,
    )


def on_cancel(doc, method=None) -> None:
    """Cancel pending Payment Logs when a Payment Request is cancelled."""
    if not _is_paystack_gateway(doc):
        return

    pending_logs = frappe.get_all(
        "Paystack Payment Log",
        filters={
            "reference_doctype": "Payment Request",
            "reference_docname": doc.name,
            "status": ["in", ["Pending", "Processed"]],
        },
        pluck="name",
        ignore_permissions=True,
    )
    for name in pending_logs:
        frappe.db.set_value("Paystack Payment Log", name, "status", "Failed")

    if pending_logs:
        frappe.db.commit()


def _is_paystack_gateway(doc) -> bool:
    gw = getattr(doc, "payment_gateway", None) or ""
    return "paystack" in gw.lower()


def _get_gateway_setting(doc) -> str | None:
    gw = getattr(doc, "payment_gateway", None)
    if not gw:
        return None
    return frappe.db.get_value("Payment Gateway", gw, "gateway_controller")
