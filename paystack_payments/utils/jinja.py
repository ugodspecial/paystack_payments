"""
jinja.py
Jinja helpers registered in hooks.py under `jinja.methods`.
Available in any Frappe print format or web template:

    {% set link = paystack_payment_link(doc) %}
    {% if link %}
      <a href="{{ link }}">{{ link }}</a>
      <img src="{{ paystack_payment_qr(doc) }}">
    {% endif %}
"""

import base64
import io

import frappe
from frappe.utils import now_datetime


def _get_open_log(doc):
    """Return the open Paystack Payment Log for a document, or None."""
    filters = {
        "reference_doctype": doc.doctype,
        "reference_name": doc.name,
        "status": ["in", ["Pending", "Processed"]],
    }
    name = frappe.db.get_value("Paystack Payment Log", filters, "name")
    if not name:
        return None
    log = frappe.get_doc("Paystack Payment Log", name)
    if log.link_expires_at and log.link_expires_at < now_datetime():
        return None
    return log


def paystack_payment_link(doc) -> str:
    """Return the checkout URL for an open payment log, or empty string."""
    log = _get_open_log(doc)
    if not log:
        return ""
    return log.checkout_url or f"/paystack-checkout/{log.name}"


def paystack_payment_qr(doc) -> str:
    """Return a base64-encoded PNG data URI of the payment QR code, or empty string."""
    link = paystack_payment_link(doc)
    if not link:
        return ""
    try:
        import qrcode

        qr = qrcode.make(link)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/png;base64,{encoded}"
    except ImportError:
        return ""
