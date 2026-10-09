"""
payment/lifecycle.py
Generic payment lifecycle coordinator.

This is the ONLY place that decides what happens after a Paystack event.
It enforces the correct dependency direction:

    Paystack event
         ↓
    gateway/webhook.py
         ↓
    payment/lifecycle.py  ← YOU ARE HERE
         ↓
    ┌────────────────────────────────────┐
    │                                    │
    on_payment_success callback          ERPNext adapter
    (LMS, School, any Frappe app)        (only if ERPNext installed
                                          AND reference is ERPNext doctype)

No ERPNext imports at module level. ERPNext code is imported lazily,
inside `_try_erpnext_adapter()`, and only when the installed-apps check
confirms ERPNext is present.
"""

from __future__ import annotations

import logging

import frappe

logger = logging.getLogger(__name__)

# DocTypes that belong to ERPNext and should be routed to the adapter.
_ERPNEXT_DOCTYPES: frozenset[str] = frozenset(
    {
        "Payment Request",
        "Sales Invoice",
        "Sales Order",
        "POS Invoice",
        "Dunning",
        "Subscription",
    }
)


# ── Public lifecycle hooks ────────────────────────────────────────────────────


def on_payment_success(log) -> None:
    """
    Called after a charge.success webhook is verified and the Payment Log
    has been updated to 'Processed'.

    Responsibility chain (in order):
      1. Update log status to Completed.
      2. Settle the Payment Log against its reference document:
           a. If the reference document has an on_payment_success() method
              → call it (generic Frappe app: LMS, School, etc.)
           b. Else if the reference is an ERPNext doctype and ERPNext is
              installed → delegate to the ERPNext adapter.
           c. Else → log only (no accounting action required).
    """
    _notify_reference(
        log=log,
        event="on_payment_success",
        erpnext_handler="paystack_payments.integrations.erpnext.on_payment_success",
    )
    _sync_lms_payment(log)
    log.mark_completed()


def on_payment_failed(log) -> None:
    """
    Called after a charge.failed webhook is received and the Payment Log
    has been updated to 'Failed'.
    """
    _notify_reference(
        log=log,
        event="on_payment_failed",
        erpnext_handler="paystack_payments.integrations.erpnext.on_payment_failed",
    )


def on_refund_processed(refund_log) -> None:
    """
    Called after a refund.processed webhook updates the Refund Log.

    Responsibility chain:
      1. Book the refund on the reference document.
      2. If ERPNext: create reversal Payment Entry via adapter.
    """
    payment_log = frappe.get_doc("Paystack Payment Log", refund_log.payment_log)

    # Update parent log totals atomically.
    new_total = (payment_log.total_refunded or 0) + (refund_log.amount or 0)
    new_status = (
        "Refunded"
        if abs(new_total - (payment_log.amount_paid or 0)) < 0.01
        else "Partially Refunded"
    )
    payment_log.db_set("total_refunded", new_total)
    payment_log.db_set("status", new_status)
    frappe.db.commit()

    refund_log.db_set("status", "Processed")
    frappe.db.commit()

    # Notify generic callback.
    _notify_reference(
        log=payment_log,
        event="on_refund_processed",
        erpnext_handler="paystack_payments.integrations.erpnext.on_refund_processed",
        extra_arg=refund_log,
    )


def on_settlement_received(settlement) -> None:
    """
    Called when a settlement.success webhook creates a Paystack Settlement.

    On non-ERPNext sites: settlement is recorded in the Settlement log only.
    On ERPNext sites: the adapter posts a Journal Entry.
    """
    if _is_erpnext_installed():
        _try_erpnext_adapter(
            "paystack_payments.integrations.erpnext.on_settlement_received",
            settlement,
        )


# ── LMS synchronization ───────────────────────────────────────────────────────


def _sync_lms_payment(log) -> None:
    """Update the native LMS Payment row when one is the payment reference."""
    if not log.reference_docname:
        return
    if log.reference_doctype not in ("LMS Payment", "LMS Enrollment"):
        return
    try:
        if log.reference_doctype == "LMS Enrollment":
            payment_name = frappe.db.get_value(
                "LMS Payment", {"reference_name": log.reference_docname}, "name"
            )
            if not payment_name:
                payment_name = frappe.db.get_value(
                    "LMS Payment", {"payment_reference": log.reference_docname}, "name"
                )
        else:
            payment_name = log.reference_docname
        if not payment_name:
            return
        meta = frappe.get_meta("LMS Payment")
        values = {}
        if meta.has_field("payment_received"):
            values["payment_received"] = 1
        if meta.has_field("payment_id"):
            values["payment_id"] = log.paystack_txn_id
        if meta.has_field("status"):
            values["status"] = "Paid"
        if values:
            frappe.db.set_value("LMS Payment", payment_name, values, update_modified=False)
            frappe.db.commit()
    except Exception:  # noqa: BLE001
        frappe.log_error(title="Paystack: LMS payment sync failed", message=frappe.get_traceback())


# ── Internal notification engine ──────────────────────────────────────────────


def _notify_reference(
    *,
    log,
    event: str,
    erpnext_handler: str,
    extra_arg=None,
) -> None:
    """
    Notify the document referenced by a Payment Log of a lifecycle event.

    Resolution order:
      1. Generic callback — reference_doctype has a method named `event`.
      2. ERPNext adapter — reference_doctype is an ERPNext doctype.
      3. No-op — the app does not need a callback.
    """
    ref_dt: str = log.reference_doctype or ""
    ref_dn: str = log.reference_docname or ""

    if not ref_dt or not ref_dn:
        logger.debug(
            "paystack_payments: %s — no reference doc on log %s, skipping notify",
            event,
            log.name,
        )
        return

    # ── Attempt generic callback ──────────────────────────────────────────────
    try:
        ref_doc = frappe.get_doc(ref_dt, ref_dn)
    except frappe.DoesNotExistError:
        logger.warning(
            "paystack_payments: %s — reference doc %s %s not found",
            event,
            ref_dt,
            ref_dn,
        )
        return

    callback = getattr(ref_doc, event, None)
    if callable(callback):
        try:
            if extra_arg is not None:
                callback(log, extra_arg)
            else:
                callback(log)
            return  # Generic callback handled it — stop here.
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack lifecycle: {event} callback failed on {ref_dt} {ref_dn}",
                message=frappe.get_traceback(),
            )
            return

    # ── Attempt ERPNext adapter ───────────────────────────────────────────────
    if ref_dt in _ERPNEXT_DOCTYPES and _is_erpnext_installed():
        if extra_arg is not None:
            _try_erpnext_adapter(erpnext_handler, log, extra_arg)
        else:
            _try_erpnext_adapter(erpnext_handler, log)
        return

    # ── No-op ─────────────────────────────────────────────────────────────────
    logger.info(
        "paystack_payments: %s — no callback found for %s %s (not an ERPNext doctype "
        "and no %s() method). Payment recorded on the log only.",
        event,
        ref_dt,
        ref_dn,
        event,
    )


def _is_erpnext_installed() -> bool:
    """
    Check if ERPNext is installed on this site.
    Result is cached per request via frappe.local.
    """
    cache_key = "_paystack_erpnext_installed"
    cached = getattr(frappe.local, cache_key, None)
    if cached is None:
        cached = "erpnext" in frappe.get_installed_apps()
        setattr(frappe.local, cache_key, cached)
    return cached


def _try_erpnext_adapter(dotted_path: str, *args) -> None:
    """
    Dynamically call an ERPNext adapter function.
    If the import fails (e.g. ERPNext not installed), log and continue.
    """
    try:
        fn = frappe.get_attr(dotted_path)
        fn(*args)
    except ImportError:
        logger.warning(
            "paystack_payments: ERPNext adapter %r not importable — ERPNext not installed?",
            dotted_path,
        )
    except Exception:  # noqa: BLE001
        frappe.log_error(
            title=f"Paystack ERPNext adapter error: {dotted_path}",
            message=frappe.get_traceback(),
        )
