"""
www/paystack-checkout/index.py
Generic portal checkout page. No ERPNext dependency.

Security:
  - Reference (Payment Log name) is an auto-generated hash — unguessable.
  - Link expiry is enforced server-side.
  - Public key is exposed; secret key is never sent to the browser.
  - Hosted-mode redirects to Paystack via HTTP 302 — browser never sees the URL in JS.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

no_cache = 1


def get_context(context):
    reference: str = (frappe.form_dict.get("reference") or "").strip()

    if not reference or len(reference) > 140:
        _redirect_invalid(context)
        return

    try:
        log = frappe.get_doc("Paystack Payment Log", reference)
    except frappe.DoesNotExistError:
        _redirect_invalid(context)
        return

    # ── Enforce link expiry ───────────────────────────────────────────────────
    if log.link_expires_at and log.link_expires_at < now_datetime():
        context.expired = True
        context.title = _("Payment Link Expired")
        return

    # ── Already settled states ────────────────────────────────────────────────
    if log.status == "Completed":
        context.already_paid = True
        context.title = _("Payment Already Received")
        return

    if log.status in ("Refunded",):
        context.refunded = True
        context.title = _("Payment Refunded")
        return

    if log.status == "Failed":
        context.failed = True
        context.title = _("Payment Failed")
        return

    # ── Load gateway config ───────────────────────────────────────────────────
    gw = frappe.get_cached_doc("Paystack Gateway Setting", log.gateway_setting)

    if not gw.enabled:
        context.failed = True
        context.title = _("Gateway Unavailable")
        return

    # ── Hosted mode: redirect immediately ────────────────────────────────────
    if gw.checkout_mode == "Hosted" and log.hosted_url:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = log.hosted_url
        return

    # ── Inline mode: pass context to template ────────────────────────────────
    context.log_name = log.name
    context.amount = log.amount
    context.currency = log.currency
    context.payer_email = log.payer_email or ""
    context.payer_name = log.payer_name or ""
    context.description = log.description or log.reference_docname or ""
    # Public key only — secret key is NEVER sent to the client.
    context.public_key = gw.public_key
    context.title = _("Pay {0} {1}").format(
        log.currency,
        frappe.utils.fmt_money(log.amount, currency=log.currency),
    )


def _redirect_invalid(context):
    context.not_found = True
    context.title = _("Payment Not Found")
