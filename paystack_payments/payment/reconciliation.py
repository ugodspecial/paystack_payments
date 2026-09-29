"""
payment/reconciliation.py
Reconciles Paystack Payment Logs against the Paystack transaction API.

Generic — no ERPNext dependency. Creates/updates Paystack Reconciliation Log
records and returns a human-readable summary.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, today


def reconcile_gateway(gateway_setting: str) -> str:
    """
    Compare the last two days of Payment Logs against Paystack.
    Returns a summary string for display.
    """
    from paystack_payments.gateway.client import get_client_for_gateway

    client = get_client_for_gateway(gateway_setting)

    logs = frappe.get_all(
        "Paystack Payment Log",
        filters={
            "gateway_setting": gateway_setting,
            "status": ["in", ["Processed", "Completed", "Needs Attention"]],
            "creation": [">", add_days(today(), -2)],
        },
        fields=["name", "paystack_txn_id", "amount_paid", "status"],
        ignore_permissions=True,
        limit=500,
    )

    reconciled = mismatch = pending = error = 0

    for row in logs:
        if not row.paystack_txn_id:
            pending += 1
            continue
        try:
            txn = client.verify_transaction(row.name).get("data", {})
            ps_amount = int(txn.get("amount") or 0) / 100

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

            _upsert_reconciliation_log(row.name, rec_status, ps_amount, row.amount_paid, reason)
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack reconciliation error for log {row.name}",
                message=frappe.get_traceback(),
            )
            error += 1

    frappe.db.commit()
    return _(
        "Reconciliation complete — Reconciled: {0}, Mismatches: {1}, Pending: {2}, Errors: {3}."
    ).format(reconciled, mismatch, pending, error)


def _upsert_reconciliation_log(
    payment_log: str,
    status: str,
    paystack_amount: float,
    local_amount: float,
    mismatch_reason: str,
) -> None:
    existing = frappe.db.get_value(
        "Paystack Reconciliation Log", {"payment_log": payment_log}, "name"
    )
    if existing:
        frappe.db.set_value(
            "Paystack Reconciliation Log",
            existing,
            {
                "status": status,
                "paystack_amount": paystack_amount,
                "local_amount": local_amount,
                "mismatch_reason": mismatch_reason,
            },
        )
    else:
        frappe.get_doc(
            {
                "doctype": "Paystack Reconciliation Log",
                "payment_log": payment_log,
                "status": status,
                "paystack_amount": paystack_amount,
                "local_amount": local_amount,
                "mismatch_reason": mismatch_reason,
            }
        ).insert(ignore_permissions=True)
