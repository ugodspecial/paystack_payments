import frappe
from frappe import _
from frappe.model.document import Document


class PaystackRefundLog(Document):

    def validate(self):
        pl = frappe.get_doc("Paystack Payment Log", self.payment_log)
        max_refund = (pl.amount_paid or 0) - (pl.total_refunded or 0)
        if self.amount > max_refund:
            frappe.throw(
                _("Refund amount {0} exceeds maximum refundable amount {1}.").format(
                    self.amount, max_refund
                )
            )

    def book_reversal(self):
        """
        Create and submit a reversal Payment Entry out of the suspense account.
        Called on refund.processed webhook.
        """
        pl = frappe.get_doc("Paystack Payment Log", self.payment_log)
        gw = frappe.get_doc("Paystack Gateway Setting", pl.gateway_setting)

        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Pay"
        pe.mode_of_payment = gw.mode_of_payment
        pe.company = pl.company
        pe.paid_from = gw.suspense_account
        pe.paid_amount = self.amount
        pe.received_amount = self.amount
        pe.paid_from_account_currency = self.currency or pl.currency
        pe.reference_no = self.paystack_refund_id or self.name
        pe.reference_date = frappe.utils.today()
        pe.remarks = f"Refund for Paystack Payment Log {self.payment_log}"
        pe.insert(ignore_permissions=True)
        pe.submit()

        self.payment_entry = pe.name
        self.status = "Processed"
        self.save(ignore_permissions=True)

        # Update parent log totals
        new_total = (pl.total_refunded or 0) + self.amount
        new_status = (
            "Refunded" if abs(new_total - pl.amount_paid) < 0.01 else "Partially Refunded"
        )
        pl.db_set("total_refunded", new_total)
        pl.db_set("status", new_status)
        frappe.db.commit()
