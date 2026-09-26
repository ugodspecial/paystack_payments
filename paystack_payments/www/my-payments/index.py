"""
www/my-payments/index.py
Portal page that shows a logged-in user's Paystack payment history.
Matches Payment Logs by the session user's email address.
No ERPNext dependency.
"""

import frappe
from frappe import _

no_cache = 1
login_required = True


def get_context(context):
    context.title = _("My Payments")
    context.no_breadcrumbs = True

    email = frappe.session.user
    if email == "Guest":
        frappe.throw(_("You must be logged in to view your payments."), frappe.PermissionError)

    context.payments = frappe.db.get_all(
        "Paystack Payment Log",
        filters={"payer_email": email},
        fields=[
            "name",
            "status",
            "amount",
            "currency",
            "payment_date",
            "reference_doctype",
            "reference_name",
            "paystack_txn_id",
        ],
        order_by="creation desc",
        limit=50,
    )
