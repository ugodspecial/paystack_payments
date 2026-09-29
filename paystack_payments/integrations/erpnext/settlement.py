"""
integrations/erpnext/settlement.py
Posts a Journal Entry for a Paystack settlement when ERPNext is installed.

Journal Entry structure:
  Dr  Settlement Bank Account    net
  Dr  Paystack Fee Account       fees + deductions
  Cr  Suspense Account           gross

Journal Entry is a Frappe core doctype; Account is ERPNext.
This module therefore requires ERPNext for the Account lookups.
"""

from __future__ import annotations

import frappe
from frappe.utils import today

from paystack_payments.integrations.erpnext.adapter import (
    get_accounting_fields,
    require_erpnext,
)


def post_settlement_journal_entry(settlement) -> None:
    """
    Post a Journal Entry for a Paystack Settlement.
    Only called when ERPNext is installed and accounting fields are set.
    """
    require_erpnext()

    accounting = get_accounting_fields(settlement.gateway_setting)

    missing = [
        label
        for field, label in [
            ("suspense_account", "Suspense Account"),
            ("settlement_bank_account", "Settlement Bank Account"),
        ]
        if not accounting.get(field)
    ]

    total_fees = (settlement.fees or 0) + (settlement.deductions or 0)
    if total_fees and not accounting.get("paystack_fee_account"):
        missing.append("Paystack Fee Account")

    if missing:
        settlement.db_set(
            "errors",
            f"Missing accounting fields: {', '.join(missing)}. Journal Entry not posted.",
        )
        settlement.db_set("status", "Failed")
        frappe.db.commit()
        return

    # Integrity check: net + fees + deductions must equal gross.
    total_debits = (settlement.net or 0) + total_fees
    if abs(total_debits - (settlement.gross or 0)) > 0.01:
        settlement.db_set(
            "errors",
            f"Settlement figures do not balance: net {settlement.net} + fees "
            f"{settlement.fees} + deductions {settlement.deductions} = {total_debits} "
            f"≠ gross {settlement.gross}.",
        )
        settlement.db_set("status", "Failed")
        frappe.db.commit()
        return

    accounts = [
        # Credit: reverse the gross from suspense.
        {
            "account": accounting["suspense_account"],
            "debit_in_account_currency": 0,
            "credit_in_account_currency": settlement.gross or 0,
        },
        # Debit: bank receives net.
        {
            "account": accounting["settlement_bank_account"],
            "debit_in_account_currency": settlement.net or 0,
            "credit_in_account_currency": 0,
        },
    ]

    if total_fees:
        accounts.append(
            {
                "account": accounting["paystack_fee_account"],
                "debit_in_account_currency": total_fees,
                "credit_in_account_currency": 0,
            }
        )

    je = frappe.get_doc(
        {
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "company": accounting["company"],
            "posting_date": settlement.settlement_date or today(),
            "accounts": accounts,
            "user_remark": f"Paystack Settlement {settlement.paystack_settlement_id}",
        }
    )
    je.insert(ignore_permissions=True)
    je.submit()

    settlement.db_set("journal_entry", je.name)
    settlement.db_set("status", "Booked")
    frappe.db.commit()
