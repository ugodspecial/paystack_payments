"""
integrations/erpnext/sales_invoice.py
ERPNext Sales Invoice adapter.
"""

from __future__ import annotations

import frappe

from paystack_payments.integrations.erpnext.adapter import require_erpnext
from paystack_payments.integrations.erpnext.payment_entry import (
    create_and_submit_payment_entry,
    create_refund_payment_entry,
)


def on_payment_success(payment_log) -> None:
    require_erpnext()
    si = frappe.get_doc("Sales Invoice", payment_log.reference_docname)

    party = si.customer
    pe_name = create_and_submit_payment_entry(
        payment_log=payment_log,
        party_type="Customer",
        party=party,
        reference_doctype="Sales Invoice",
        reference_docname=si.name,
    )
    payment_log.db_set("payment_entry", pe_name)
    payment_log.db_set("status", "Completed")
    frappe.db.commit()


def on_refund_processed(payment_log, refund_log) -> None:
    require_erpnext()
    create_refund_payment_entry(payment_log=payment_log, refund_log=refund_log)
