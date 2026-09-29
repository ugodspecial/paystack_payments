"""
gateway/refund.py
Initiates a Paystack refund and creates a Paystack Refund Log.

Deliberately generic — no knowledge of Payment Entry, Journal Entry,
or any ERPNext concept. Post-refund accounting is handled by the
ERPNext adapter via the payment lifecycle.
"""

from __future__ import annotations

import frappe

from paystack_payments.gateway.client import get_client_for_gateway


def initiate_refund(
    *,
    payment_log_name: str,
    amount: float,
    reason: str = "",
) -> frappe.Document:
    """
    Initiate a full or partial Paystack refund.

    Validates:
      - The Payment Log exists and is in a refundable state.
      - The requested amount does not exceed the refundable balance.
      - The Paystack API accepts the refund request.

    Creates a Paystack Refund Log in 'Pending' status.
    The log is updated to 'Processed' when the refund.processed webhook arrives.

    Returns the newly created Paystack Refund Log document.
    """
    log = frappe.get_doc("Paystack Payment Log", payment_log_name)

    # ── Validate state ────────────────────────────────────────────────────────
    if log.status not in ("Completed", "Partially Refunded"):
        frappe.throw(
            frappe._(
                "Cannot refund a Payment Log in status '{0}'. "
                "Only Completed or Partially Refunded logs can be refunded."
            ).format(log.status)
        )

    if not log.paystack_txn_id:
        frappe.throw(
            frappe._("Payment Log {0} has no Paystack transaction ID.").format(payment_log_name)
        )

    max_refundable = (log.amount_paid or 0) - (log.total_refunded or 0)
    if amount <= 0 or amount > max_refundable:
        frappe.throw(
            frappe._(
                "Refund amount {0} is invalid. Maximum refundable: {1} {2}."
            ).format(amount, max_refundable, log.currency)
        )

    # ── Call Paystack API ─────────────────────────────────────────────────────
    client = get_client_for_gateway(log.gateway_setting)
    resp = client.refund(
        transaction=log.paystack_txn_id,
        amount_kobo=round(amount * 100),
        reason=reason,
    )

    if not resp.get("status"):
        frappe.throw(
            frappe._("Paystack refund failed: {0}").format(
                resp.get("message", "Unknown error")
            )
        )

    # ── Create Refund Log ─────────────────────────────────────────────────────
    refund_log = frappe.get_doc(
        {
            "doctype": "Paystack Refund Log",
            "payment_log": payment_log_name,
            "amount": amount,
            "currency": log.currency,
            "reason": (reason or "")[:500],
            "status": "Pending",
        }
    )
    refund_log.insert(ignore_permissions=True)
    frappe.db.commit()

    return refund_log
