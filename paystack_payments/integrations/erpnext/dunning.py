"""
integrations/erpnext/dunning.py
ERPNext Dunning adapter.
"""

from __future__ import annotations

import frappe

from paystack_payments.integrations.erpnext.adapter import require_erpnext
from paystack_payments.integrations.erpnext.payment_entry import create_and_submit_payment_entry


def on_payment_success(payment_log) -> None:
    require_erpnext()
    dunning = frappe.get_doc("Dunning", payment_log.reference_docname)
    pe_name = create_and_submit_payment_entry(
        payment_log=payment_log,
        party_type="Customer",
        party=dunning.customer,
        reference_doctype="Dunning",
        reference_docname=dunning.name,
    )
    payment_log.db_set("payment_entry", pe_name)
    payment_log.db_set("status", "Completed")
    frappe.db.commit()
