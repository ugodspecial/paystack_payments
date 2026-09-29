"""
integrations/erpnext/__init__.py
Public entry points for the ERPNext adapter.

These functions are called by payment/lifecycle.py when:
  - ERPNext is installed, AND
  - The payment log's reference_doctype is an ERPNext doctype.

All functions guard with require_erpnext() as the first call.
"""

from __future__ import annotations

import frappe

from paystack_payments.integrations.erpnext.adapter import require_erpnext

# Maps ERPNext reference doctypes to their adapter handler modules.
_PAYMENT_SUCCESS_ROUTES: dict[str, str] = {
    "Payment Request": "paystack_payments.integrations.erpnext.payment_request.on_payment_success",
    "Sales Invoice": "paystack_payments.integrations.erpnext.sales_invoice.on_payment_success",
    "Sales Order": "paystack_payments.integrations.erpnext.sales_order.on_payment_success",
    "POS Invoice": "paystack_payments.integrations.erpnext.pos.on_payment_success",
    "Dunning": "paystack_payments.integrations.erpnext.dunning.on_payment_success",
}

_PAYMENT_FAILED_ROUTES: dict[str, str] = {
    "Payment Request": "paystack_payments.integrations.erpnext.payment_request.on_payment_failed",
}

_REFUND_ROUTES: dict[str, str] = {
    "Payment Request": "paystack_payments.integrations.erpnext.payment_request.on_refund_processed",
    "Sales Invoice": "paystack_payments.integrations.erpnext.sales_invoice.on_refund_processed",
}


def on_payment_success(payment_log) -> None:
    """Route a successful payment to the correct ERPNext doctype handler."""
    require_erpnext()
    _dispatch(_PAYMENT_SUCCESS_ROUTES, payment_log.reference_doctype, payment_log)


def on_payment_failed(payment_log) -> None:
    """Route a failed payment to the correct ERPNext doctype handler."""
    require_erpnext()
    _dispatch(_PAYMENT_FAILED_ROUTES, payment_log.reference_doctype, payment_log)


def on_refund_processed(payment_log, refund_log) -> None:
    """Route a processed refund to the correct ERPNext doctype handler."""
    require_erpnext()
    _dispatch(_REFUND_ROUTES, payment_log.reference_doctype, payment_log, refund_log)


def on_settlement_received(settlement) -> None:
    """Post a settlement Journal Entry via the ERPNext adapter."""
    require_erpnext()
    from paystack_payments.integrations.erpnext.settlement import post_settlement_journal_entry
    post_settlement_journal_entry(settlement)


def _dispatch(routes: dict, ref_dt: str, *args) -> None:
    handler_path = routes.get(ref_dt)
    if not handler_path:
        frappe.log_error(
            title=f"Paystack ERPNext adapter: no handler for {ref_dt!r}",
            message=(
                f"Received payment for reference_doctype={ref_dt!r} but no ERPNext "
                f"adapter handler is registered. Payment recorded on the log only."
            ),
        )
        return
    try:
        fn = frappe.get_attr(handler_path)
        fn(*args)
    except Exception:  # noqa: BLE001
        frappe.log_error(
            title=f"Paystack ERPNext adapter error: {handler_path}",
            message=frappe.get_traceback(),
        )
