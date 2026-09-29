"""
integrations/erpnext/payment_entry.py
Factory for creating and submitting ERPNext Payment Entries.

All functions in this file require ERPNext. They are only ever called
from the ERPNext adapter chain — never from gateway/ or payment/ code.
"""

from __future__ import annotations

import frappe
from frappe.utils import today

from paystack_payments.integrations.erpnext.adapter import (
    get_accounting_fields,
    require_erpnext,
)


def create_and_submit_payment_entry(
    *,
    payment_log,
    party_type: str = "Customer",
    party: str = "",
    against_account: str = "",
    reference_doctype: str = "",
    reference_docname: str = "",
) -> str:
    """
    Create and submit a Payment Entry against the suspense account.

    Returns the name of the submitted Payment Entry.

    Security / integrity:
      - Amount taken from the Payment Log (server-side), not from
        user-supplied input, preventing amount tampering.
      - Reference numbers come from the verified Paystack transaction ID.
      - Duplicate prevention: checks for existing PE by reference_no
        before creating.
    """
    require_erpnext()

    # Prevent duplicate Payment Entries for the same Paystack transaction.
    existing = frappe.db.get_value(
        "Payment Entry",
        {"reference_no": payment_log.paystack_txn_id, "docstatus": ["!=", 2]},
        "name",
    )
    if existing:
        return existing

    accounting = get_accounting_fields(payment_log.gateway_setting)

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Receive"
    pe.mode_of_payment = accounting["mode_of_payment"]
    pe.company = accounting["company"]

    pe.party_type = party_type
    pe.party = party

    pe.paid_from = against_account or _get_receivable_account(accounting["company"], party_type)
    pe.paid_to = accounting["suspense_account"]
    pe.paid_amount = payment_log.amount_paid or payment_log.amount
    pe.received_amount = payment_log.amount_paid or payment_log.amount
    pe.paid_from_account_currency = payment_log.currency
    pe.paid_to_account_currency = payment_log.currency
    pe.source_exchange_rate = 1
    pe.target_exchange_rate = 1

    pe.reference_no = payment_log.paystack_txn_id
    pe.reference_date = str(payment_log.payment_date or today())[:10]
    pe.remarks = f"Paystack transaction {payment_log.paystack_txn_id} — Log {payment_log.name}"

    if reference_doctype and reference_docname:
        pe.append(
            "references",
            {
                "reference_doctype": reference_doctype,
                "reference_name": reference_docname,
                "allocated_amount": pe.paid_amount,
            },
        )

    pe.insert(ignore_permissions=True)
    pe.submit()

    payment_log.db_set("payment_entry", pe.name)
    payment_log.db_set("status", "Completed")
    frappe.db.commit()

    return pe.name


def create_refund_payment_entry(
    *,
    payment_log,
    refund_log,
) -> str:
    """
    Create and submit a reversal Payment Entry for a refund.
    """
    require_erpnext()

    accounting = get_accounting_fields(payment_log.gateway_setting)

    pe = frappe.new_doc("Payment Entry")
    pe.payment_type = "Pay"
    pe.mode_of_payment = accounting["mode_of_payment"]
    pe.company = accounting["company"]

    pe.paid_from = accounting["suspense_account"]
    pe.paid_to = accounting["suspense_account"]
    pe.paid_amount = refund_log.amount
    pe.received_amount = refund_log.amount
    pe.paid_from_account_currency = payment_log.currency
    pe.paid_to_account_currency = payment_log.currency
    pe.source_exchange_rate = 1
    pe.target_exchange_rate = 1

    pe.reference_no = refund_log.paystack_refund_id or refund_log.name
    pe.reference_date = today()
    pe.remarks = (
        f"Paystack refund for transaction {payment_log.paystack_txn_id} "
        f"— Refund Log {refund_log.name}"
    )

    pe.insert(ignore_permissions=True)
    pe.submit()

    refund_log.db_set("payment_entry", pe.name)
    frappe.db.commit()

    return pe.name


def _get_receivable_account(company: str, party_type: str) -> str:
    """Look up the default receivable account for a company."""
    account = frappe.db.get_value(
        "Account",
        {
            "company": company,
            "account_type": "Receivable" if party_type == "Customer" else "Payable",
            "is_group": 0,
        },
        "name",
    )
    if not account:
        frappe.throw(
            frappe._("No default receivable account found for company '{0}'.").format(company)
        )
    return account
