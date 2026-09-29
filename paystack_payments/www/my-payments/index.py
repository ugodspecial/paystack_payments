"""
www/my-payments/index.py
Portal page: shows a logged-in user's Paystack payment history.

Generic — matches by payer_email only, no Customer doctype dependency.
On ERPNext sites, the template can optionally show linked invoices.
"""

from __future__ import annotations

import frappe
from frappe import _

no_cache = 1
login_required = True


def get_context(context):
    context.title = _("My Payments")
    context.no_breadcrumbs = True

    user_email: str = frappe.session.user
    if not user_email or user_email == "Guest":
        frappe.throw(_("You must be logged in to view your payments."), frappe.PermissionError)

    context.payments = frappe.get_all(
        "Paystack Payment Log",
        filters={"payer_email": user_email},
        fields=[
            "name",
            "status",
            "amount",
            "currency",
            "payment_date",
            "reference_doctype",
            "reference_docname",
            "description",
            "paystack_txn_id",
        ],
        order_by="creation desc",
        limit=50,
        ignore_permissions=True,
    )

    # On ERPNext sites, optionally enrich with invoice links.
    context.erpnext_installed = "erpnext" in frappe.get_installed_apps()
