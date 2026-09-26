"""
paystack_payment_log.py
Tracks a single Paystack charge lifecycle from Pending → Completed.
No ERPNext imports — all accounting goes through the `payments` app
Payment Request / Payment Entry chain, or direct frappe.accounting calls.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime


class PaystackPaymentLog(Document):

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def before_insert(self):
        self.retry_count = 0

    def validate(self):
        if self.total_refunded and self.amount_paid and self.total_refunded > self.amount_paid:
            frappe.throw(
                _("Total Refunded ({0}) cannot exceed Amount Paid ({1}).").format(
                    self.total_refunded, self.amount_paid
                )
            )

    # ── Business methods ──────────────────────────────────────────────────────

    def mark_processed(self, txn_id, amount_paid, fee, payment_date):
        """Called by the webhook handler on charge.success."""
        self.paystack_txn_id = txn_id
        self.amount_paid = amount_paid
        self.paystack_fee = fee
        self.payment_date = payment_date
        self.status = "Processed"
        self.save(ignore_permissions=True)
        frappe.db.commit()

    def mark_completed(self, payment_entry_name):
        """Called once the Payment Entry is booked."""
        self.payment_entry = payment_entry_name
        self.status = "Completed"
        self.save(ignore_permissions=True)
        frappe.db.commit()

    def mark_failed(self, reason):
        self.status = "Failed"
        self.errors = reason
        self.save(ignore_permissions=True)
        frappe.db.commit()

    def schedule_retry(self):
        """Exponential back-off — doubles each attempt, capped at 24 h."""
        MAX_RETRIES = 12
        MAX_WAIT_HOURS = 24

        self.retry_count = (self.retry_count or 0) + 1

        if self.retry_count > MAX_RETRIES:
            self.status = "Needs Attention"
            frappe.log_error(
                title="Paystack Needs Attention",
                message=f"Payment Log {self.name} ({self.paystack_txn_id}) "
                        "has exceeded max retries without a Payment Entry.",
            )
        else:
            wait_minutes = min(10 * (2 ** (self.retry_count - 1)), MAX_WAIT_HOURS * 60)
            self.next_retry_at = add_to_date(now_datetime(), minutes=wait_minutes)

        self.save(ignore_permissions=True)
        frappe.db.commit()

    def settle_payment_request(self):
        """
        Drive the Payment Request from the `payments` app to create the
        Payment Entry.  Works for any app that has Payment Request — no
        ERPNext import needed.
        """
        if not self.payment_request:
            return self._book_direct_payment_entry()

        pr = frappe.get_doc("Payment Request", self.payment_request)

        # Amount tolerance check (0.01 in the charged currency)
        if abs((self.amount_paid or 0) - pr.grand_total) > 0.01:
            msg = (
                f"Amount mismatch: Paystack charged {self.amount_paid} "
                f"{self.currency}, Payment Request expects {pr.grand_total}."
            )
            self.errors = msg
            self.status = "Needs Attention"
            self.save(ignore_permissions=True)
            frappe.db.commit()
            return

        # Use run_payment_flow() — the payments-app method that creates the
        # Payment Entry and marks the Payment Request as paid.
        # (set_as_paid() is ERPNext-only; run_payment_flow() is the payments-app equivalent)
        try:
            pr.run_payment_flow()
        except AttributeError:
            # Fallback: older payments-app versions expose set_as_paid()
            pr.set_as_paid()

        # Look up the Payment Entry that was just created
        pe_name = frappe.db.get_value(
            "Payment Entry",
            {"reference_no": self.paystack_txn_id},
            "name",
        ) or frappe.db.get_value(
            "Payment Entry",
            {"letter_head": self.payment_request},  # some versions link via letter_head
            "name",
        ) or frappe.db.get_value(
            "Payment Entry Reference",
            {"reference_name": self.payment_request},
            "parent",
        )
        self.mark_completed(pe_name)

    def _book_direct_payment_entry(self):
        """
        For logs with no Payment Request (direct / dunning / manual).
        Creates and submits a Payment Entry against the suspense account.
        """
        gw = frappe.get_doc("Paystack Gateway Setting", self.gateway_setting)

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.mode_of_payment = gw.mode_of_payment
        pe.company = self.company

        # paid_from must be the receivable/debtor account; use the suspense
        # account as paid_to (where the money lands before bank reconciliation)
        pe.paid_to = gw.suspense_account
        pe.paid_to_account_currency = self.currency or gw.currency

        # Use the suspense account as the source too for a direct unlinked entry
        pe.paid_from = gw.suspense_account
        pe.paid_from_account_currency = self.currency or gw.currency

        pe.paid_amount = self.amount_paid or self.amount
        pe.received_amount = self.amount_paid or self.amount
        pe.source_exchange_rate = 1
        pe.target_exchange_rate = 1

        pe.reference_no = self.paystack_txn_id or self.name
        pe.reference_date = str(self.payment_date or frappe.utils.today())[:10]
        pe.remarks = f"Paystack direct charge — Payment Log {self.name}"

        pe.insert(ignore_permissions=True)
        pe.submit()
        self.mark_completed(pe.name)
