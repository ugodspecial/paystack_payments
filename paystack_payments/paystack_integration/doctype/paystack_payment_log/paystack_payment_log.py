"""
paystack_payment_log.py
Controller for the Paystack Payment Log doctype.

Tracks a single Paystack charge lifecycle:
  Pending → Processed → Completed / Partially Refunded / Refunded / Failed / Needs Attention

All business-logic methods are thin — they update the log's state and
then delegate to payment/lifecycle.py for cross-app notifications.
No ERPNext imports anywhere in this file.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, get_datetime, now_datetime

# Maximum number of automated retries before flagging for human review.
_MAX_RETRIES = 12
_MAX_WAIT_HOURS = 24


class PaystackPaymentLog(Document):

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def before_insert(self) -> None:
        self.retry_count = 0
        self.status = self.status or "Pending"

    def validate(self) -> None:
        if (
            self.total_refunded
            and self.amount_paid
            and self.total_refunded > self.amount_paid
        ):
            frappe.throw(
                _("Total Refunded ({0}) cannot exceed Amount Paid ({1}).").format(
                    self.total_refunded, self.amount_paid
                )
            )

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_processed(
        self,
        *,
        txn_id: str,
        amount_paid: float,
        fee: float,
        payment_date: str,
    ) -> None:
        """
        Called by the webhook handler on charge.success.
        Moves the log from Pending → Processed.
        """
        self.db_set("paystack_txn_id", txn_id)
        self.db_set("amount_paid", amount_paid)
        self.db_set("paystack_fee", fee)
        # Paystack returns ISO-8601 timestamps (for example, with a
        # trailing Z). MariaDB expects a Frappe-compatible datetime.
        self.db_set("payment_date", get_datetime(payment_date))
        self.db_set("status", "Processed")
        frappe.db.commit()

    def mark_completed(self, payment_entry_name: str | None = None) -> None:
        """Moves the log to Completed and optionally links a Payment Entry."""
        if payment_entry_name:
            self.db_set("payment_entry", payment_entry_name)
        self.db_set("status", "Completed")
        frappe.db.commit()

    def mark_failed(self, reason: str) -> None:
        self.db_set("errors", (reason or "")[:2000])
        self.db_set("status", "Failed")
        frappe.db.commit()

    def schedule_retry(self) -> None:
        """
        Schedule the next retry attempt with exponential back-off.
        Caps at _MAX_RETRIES; marks Needs Attention thereafter.
        """
        current_retries = (self.retry_count or 0) + 1
        self.db_set("retry_count", current_retries)

        if current_retries > _MAX_RETRIES:
            self.db_set("status", "Needs Attention")
            frappe.log_error(
                title=f"Paystack Payment Log {self.name} needs attention",
                message=(
                    f"Payment Log {self.name} (Paystack txn {self.paystack_txn_id}) "
                    f"has exceeded {_MAX_RETRIES} retries without completing."
                ),
            )
        else:
            wait_minutes = min(10 * (2 ** (current_retries - 1)), _MAX_WAIT_HOURS * 60)
            self.db_set("next_retry_at", add_to_date(now_datetime(), minutes=wait_minutes))

        frappe.db.commit()
