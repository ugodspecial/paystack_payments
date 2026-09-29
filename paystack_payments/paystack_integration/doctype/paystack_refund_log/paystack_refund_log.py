"""
paystack_refund_log.py
Tracks a single Paystack refund. No ERPNext imports.
Post-refund accounting is delegated to the payment lifecycle.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class PaystackRefundLog(Document):

    def validate(self) -> None:
        pl_name = self.payment_log
        if not pl_name:
            return
        amount_paid = frappe.db.get_value("Paystack Payment Log", pl_name, "amount_paid") or 0
        total_refunded = frappe.db.get_value("Paystack Payment Log", pl_name, "total_refunded") or 0
        max_refund = amount_paid - total_refunded
        if (self.amount or 0) > max_refund:
            frappe.throw(
                _("Refund amount {0} exceeds the maximum refundable amount {1}.").format(
                    self.amount, max_refund
                )
            )
