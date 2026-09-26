import frappe
from frappe.model.document import Document


class PaystackSettlement(Document):

    def book_journal_entry(self):
        """
        Post a Journal Entry:
          Dr Settlement Bank Account   net
          Dr Paystack Fee Account      fees
          Cr Suspense Account          gross
        """
        gw = frappe.get_doc("Paystack Gateway Setting", self.gateway_setting)

        missing = []
        for field, label in [
            ("suspense_account", "Suspense Account"),
            ("settlement_bank_account", "Settlement Bank Account"),
        ]:
            if not getattr(gw, field):
                missing.append(label)

        # Fee account only required when there are actual fees/deductions to post
        if (self.fees or self.deductions) and not gw.paystack_fee_account:
            missing.append("Paystack Fee Account")

        if missing:
            self.errors = f"Cannot book: gateway missing {', '.join(missing)}"
            self.status = "Failed"
            self.save(ignore_permissions=True)
            frappe.db.commit()
            return

        # Integrity check: net + fees + deductions must equal gross
        total_debits = (self.net or 0) + (self.fees or 0) + (self.deductions or 0)
        if abs(total_debits - (self.gross or 0)) > 0.01:
            self.errors = (
                "Paystack figures do not add up: "
                f"net {self.net} + fees {self.fees} + deductions {self.deductions} "
                f"= {total_debits} ≠ gross {self.gross}"
            )
            self.status = "Failed"
            self.save(ignore_permissions=True)
            frappe.db.commit()
            return

        # Correct double-entry structure:
        #   Dr  Settlement Bank Account   = net
        #   Dr  Paystack Fee Account      = fees   (if any)
        #   Dr  Paystack Fee Account      = deductions (if any; reuse fee account)
        #   Cr  Suspense Account          = gross  (always)
        # The three debit entries must sum to gross = net + fees + deductions.

        accounts = [
            # Cr: Suspense — reverse the gross amount captured there earlier
            {
                "account": gw.suspense_account,
                "debit_in_account_currency": 0,
                "credit_in_account_currency": self.gross or 0,
            },
            # Dr: Bank — net amount actually received
            {
                "account": gw.settlement_bank_account,
                "debit_in_account_currency": self.net or 0,
                "credit_in_account_currency": 0,
            },
        ]

        # Dr: Fees charged by Paystack
        if self.fees:
            accounts.append(
                {
                    "account": gw.paystack_fee_account,
                    "debit_in_account_currency": self.fees,
                    "credit_in_account_currency": 0,
                }
            )

        # Dr: Other deductions (chargebacks, etc.) — also booked to fee account
        # If a dedicated deductions account is needed, add a field to the gateway setting.
        if self.deductions:
            accounts.append(
                {
                    "account": gw.paystack_fee_account,
                    "debit_in_account_currency": self.deductions,
                    "credit_in_account_currency": 0,
                }
            )

        je = frappe.get_doc(
            {
                "doctype": "Journal Entry",
                "voucher_type": "Journal Entry",
                "company": self.company,
                "posting_date": self.settlement_date or frappe.utils.today(),
                "accounts": accounts,
                "user_remark": f"Paystack Settlement {self.paystack_settlement_id}",
            }
        )
        je.insert(ignore_permissions=True)
        je.submit()

        self.journal_entry = je.name
        self.status = "Booked"
        self.save(ignore_permissions=True)
        frappe.db.commit()
