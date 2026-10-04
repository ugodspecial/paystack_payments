"""
gateway/checkout.py
Creates a Paystack Payment Log and initialises a transaction on Paystack.

This module is intentionally generic — it knows nothing about Sales Invoices,
Payment Requests, LMS Enrollments, or any other business document.
The caller supplies reference_doctype + reference_docname; this module
stores them on the log and returns a checkout URL.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_url, now_datetime

from paystack_payments.gateway.client import PaystackClient, get_client_for_gateway


def create_payment(
    *,
    gateway_setting: str,
    amount: float,
    currency: str,
    payer_email: str,
    payer_name: str = "",
    description: str = "",
    reference_doctype: str = "",
    reference_docname: str = "",
    success_redirect_url: str = "",
    extra_metadata: dict | None = None,
) -> str:
    """
    Create a Paystack Payment Log and initialise a Paystack transaction.
    Returns the checkout URL the customer should visit.

    Security:
    - amount is validated > 0.
    - payer_email is validated and sanitised.
    - The Paystack reference is the auto-named Payment Log name (a hash) —
      unguessable and never user-supplied.
    """
    if amount <= 0:
        frappe.throw(frappe._("Payment amount must be greater than zero."))

    # Sanitise payer_email — fall back to a site-local placeholder so
    # Paystack always gets a valid email.
    payer_email = (payer_email or "").strip()
    if not payer_email or payer_email == "Guest":
        frappe.throw(
            frappe._("A payer email is required to initiate a Paystack transaction.")
        )

    gw = frappe.get_cached_doc("Paystack Gateway Setting", gateway_setting)

    if not gw.enabled:
        frappe.throw(
            frappe._("Paystack gateway '{0}' is not enabled.").format(gateway_setting)
        )

    # Currency check — only when a currency is explicitly supplied.
    if currency:
        gw.validate_transaction_currency(currency)
    else:
        currency = gw.currency

    amount_kobo = _to_smallest_unit(amount, currency)

    # Create the Payment Log first — its auto-generated name becomes the
    # Paystack reference, ensuring it is unguessable.
    log = frappe.get_doc(
        {
            "doctype": "Paystack Payment Log",
            "gateway_setting": gateway_setting,
            "currency": currency,
            "amount": amount,
            "payer_email": payer_email,
            "payer_name": payer_name,
            "description": description,
            "reference_doctype": reference_doctype,
            "reference_docname": reference_docname,
            "status": "Pending",
        }
    )
    log.insert(ignore_permissions=True)
    frappe.db.commit()

    checkout_page_url = get_url(f"/paystack-checkout/{log.name}")

    client: PaystackClient = get_client_for_gateway(gateway_setting)

    metadata: dict = {
        "payment_log": log.name,
        "description": description[:500],
        "payer_name": payer_name[:200],
        "reference_doctype": reference_doctype,
        "reference_docname": reference_docname,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    resp = client.initialize_transaction(
        email=payer_email,
        amount_kobo=amount_kobo,
        reference=log.name,
        callback_url=success_redirect_url or checkout_page_url,
        metadata=metadata,
    )

    tx_data = resp.get("data", {})

    # Determine the URL to return to the caller.
    if gw.checkout_mode == "Hosted":
        return_url = tx_data.get("authorization_url") or checkout_page_url
        log.db_set("hosted_url", return_url, update_modified=False)
    else:
        return_url = checkout_page_url

    log.db_set("checkout_url", checkout_page_url, update_modified=False)

    # Set link expiry if configured.
    validity_hours: int = gw.get("payment_link_validity_hours") or 0
    if validity_hours:
        log.db_set(
            "link_expires_at",
            add_to_date(now_datetime(), hours=validity_hours),
            update_modified=False,
        )

    frappe.db.commit()
    return return_url


def _to_smallest_unit(amount: float, currency: str) -> int:
    """
    Convert a human-readable amount to Paystack's smallest currency unit.
    All Paystack-supported currencies use *100 (kobo, cents, pesewas, etc.).
    """
    return round(amount * 100)
