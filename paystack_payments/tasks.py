"""
tasks.py
Scheduled background jobs.

ERPNext-specific jobs (subscription auto-charge) are guarded by
is_erpnext_installed() checks and delegated to integrations/erpnext/.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime, today


def retry_pending_captures() -> None:
    """
    Every 10 minutes: re-drive Processed logs that have no Payment Entry yet.
    Uses exponential back-off stored on the log.
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
        ignore_permissions=True,
    )
    for name in logs:
        frappe.enqueue(
            "paystack_payments.tasks._settle_one",
            queue="default",
            name=name,
            now=frappe.flags.in_test,
        )


def _settle_one(name: str) -> None:
    log = frappe.get_doc("Paystack Payment Log", name)
    try:
        from paystack_payments.payment.lifecycle import on_payment_success
        on_payment_success(log)
    except Exception:  # noqa: BLE001
        frappe.log_error(
            title=f"Paystack: retry failed for Payment Log {name}",
            message=frappe.get_traceback(),
        )
        log.schedule_retry()


def retry_pending_settlements() -> None:
    """Hourly: re-attempt failed Paystack Settlements."""
    cutoff = add_days(today(), -30)
    settlements = frappe.get_all(
        "Paystack Settlement",
        filters={"status": "Failed", "creation": [">", cutoff]},
        pluck="name",
        limit=50,
        ignore_permissions=True,
    )
    for name in settlements:
        frappe.enqueue(
            "paystack_payments.tasks._book_settlement",
            queue="default",
            name=name,
            now=frappe.flags.in_test,
        )


def _book_settlement(name: str) -> None:
    from paystack_payments.payment.lifecycle import on_settlement_received
    settlement = frappe.get_doc("Paystack Settlement", name)
    try:
        on_settlement_received(settlement)
    except Exception:  # noqa: BLE001
        frappe.log_error(
            title=f"Paystack: settlement retry failed for {name}",
            message=frappe.get_traceback(),
        )


def daily_reconciliation() -> None:
    """Daily: reconcile all enabled gateways against Paystack."""
    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1},
        pluck="name",
        ignore_permissions=True,
    )
    for gw_name in gateways:
        try:
            from paystack_payments.payment.reconciliation import reconcile_gateway
            reconcile_gateway(gw_name)
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack: daily reconciliation error (gateway {gw_name})",
                message=frappe.get_traceback(),
            )


def collect_subscription_invoices() -> None:
    """
    Daily: auto-charge ERPNext Subscription invoices.
    Silently skipped when ERPNext is not installed.
    """
    if "erpnext" not in frappe.get_installed_apps():
        return
    from paystack_payments.integrations.erpnext.subscriptions import (
        collect_subscription_invoices as _collect,
    )
    _collect()
