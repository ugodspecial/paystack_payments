"""
www/paystack-checkout/index.py
Portal checkout page. Loads payment log data server-side,
renders the Paystack inline popup or redirects to Hosted URL.
No ERPNext dependency.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

no_cache = 1


def get_context(context):
    reference = frappe.form_dict.get("reference") or ""

    if not reference:
        frappe.redirect_to_message(
            _("Invalid Link"),
            _("No payment reference found in this URL."),
        )
        raise frappe.Redirect

    try:
        log = frappe.get_doc("Paystack Payment Log", reference)
    except frappe.DoesNotExistError:
        frappe.redirect_to_message(
            _("Not Found"),
            _("Payment link not found."),
        )
        raise frappe.Redirect

    # Expired?
    if log.link_expires_at and log.link_expires_at < now_datetime():
        context.expired = True
        context.title = _("Link Expired")
        return

    # Already paid?
    if log.status in ("Completed", "Refunded"):
        context.already_paid = True
        context.title = _("Payment Received")
        return

    # Failed / cancelled?
    if log.status == "Failed":
        context.failed = True
        context.title = _("Payment Failed")
        return

    gw = frappe.get_doc("Paystack Gateway Setting", log.gateway_setting)

    # Hosted mode — redirect straight to Paystack
    if gw.checkout_mode == "Hosted" and log.hosted_url:
        frappe.local.response.location = log.hosted_url
        frappe.local.response.type = "redirect"
        raise frappe.Redirect

    # Inline mode
    context.log_name = log.name
    context.amount = log.amount
    context.currency = log.currency
    context.payer_email = log.payer_email or ""
    context.payer_name = log.payer_name or ""
    context.description = log.reference_name or log.name
    context.public_key = gw.public_key
    context.title = _("Pay {0} {1}").format(
        log.currency, frappe.utils.fmt_money(log.amount, currency=log.currency)
    )
