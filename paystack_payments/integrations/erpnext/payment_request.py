"""
integrations/erpnext/payment_request.py
ERPNext Payment Request adapter.

Handles payments where reference_doctype = "Payment Request".
"""

from __future__ import annotations

import frappe

from paystack_payments.integrations.erpnext.adapter import require_erpnext
from paystack_payments.integrations.erpnext.payment_entry import create_and_submit_payment_entry


def on_payment_success(payment_log) -> None:
    """
    Called when a Payment Request-backed payment succeeds.

    1. Verify amount matches the Payment Request.
    2. Create and submit the Payment Entry.
    3. Mark the Payment Request as paid.
    """
    require_erpnext()

    pr = frappe.get_doc("Payment Request", payment_log.reference_docname)

    # Amount tolerance check — prevent partial-payment exploits.
    if abs((payment_log.amount_paid or 0) - pr.grand_total) > 0.01:
        frappe.log_error(
            title=f"Paystack: amount mismatch on Payment Request {pr.name}",
            message=(
                f"Paystack captured {payment_log.amount_paid} {payment_log.currency} "
                f"but Payment Request expects {pr.grand_total} {pr.currency}. "
                f"Payment Log: {payment_log.name}. Manual review required."
            ),
        )
        payment_log.db_set("status", "Needs Attention")
        payment_log.db_set(
            "errors",
            f"Amount mismatch: captured {payment_log.amount_paid}, expected {pr.grand_total}",
        )
        frappe.db.commit()
        return

    party = pr.party or ""
    party_type = pr.party_type or "Customer"

    pe_name = create_and_submit_payment_entry(
        payment_log=payment_log,
        party_type=party_type,
        party=party,
        reference_doctype=pr.reference_doctype,
        reference_docname=pr.reference_name,
    )

    # Mark Payment Request as paid via the payments-app method.
    if pr.docstatus == 1 and pr.status != "Paid":
        try:
            pr.run_payment_flow()
        except AttributeError:
            # Older versions of the payments app / ERPNext.
            pr.db_set("status", "Paid")
            frappe.db.commit()

    payment_log.db_set("payment_entry", pe_name)
    payment_log.db_set("status", "Completed")
    frappe.db.commit()


def on_payment_failed(payment_log) -> None:
    require_erpnext()
    pr_name = payment_log.reference_docname
    if frappe.db.exists("Payment Request", pr_name):
        frappe.db.set_value("Payment Request", pr_name, "status", "Failed")
        frappe.db.commit()


def on_refund_processed(payment_log, refund_log) -> None:
    require_erpnext()
    from paystack_payments.integrations.erpnext.payment_entry import create_refund_payment_entry
    create_refund_payment_entry(payment_log=payment_log, refund_log=refund_log)
