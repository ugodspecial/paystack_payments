"""
gateway/checkout.py
Creates a Paystack Payment Log and initialises a transaction on Paystack.

This module is intentionally generic — it knows nothing about Sales Invoices,
Payment Requests, LMS Enrollments or any other business document.
The caller supplies reference_doctype + reference_docname; this module
stores them on the log and returns a checkout URL.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_url, now_datetime

from paystack_payments.gateway.client import get_client_for_gateway


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

    Returns the checkout URL the customer should be redirected to.

    This function is the single entry point for ALL apps — LMS, School,
    ERPNext, or any custom Frappe application. ERPNext-specific fields
    (payment_request, company, etc.) are set by callers that need them,
    not here.

    Security:
    - amount is validated > 0.
    - payer_email is validated as non-empty (Paystack requires it).
    - reference_doctype/docname are stored as-is; permission checks are
      the caller's responsibility.
    - The Paystack reference is the auto-named Payment Log name (hash),
      which is unguessable and not user-supplied.
    """
    if amount <= 0:
        frappe.throw(frappe._("Payment amount must be greater than zero."))

    if not payer_email:
        frappe.throw(frappe._("Payer email is required to initialise a Paystack transaction."))

    gw = frappe.get_cached_doc("Paystack Gateway Setting", gateway_setting)

    if not gw.enabled:
        frappe.throw(frappe._("Paystack gateway '{0}' is not enabled.").format(gateway_setting))

    gw.validate_transaction_currency(currency)

    amount_kobo = _to_kobo(amount, currency)

    # Create the Payment Log first — its auto-generated name becomes the
    # Paystack reference, ensuring it is cryptographically unguessable.
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

    client = get_client_for_gateway(gateway_setting)

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

    updates: dict = {"checkout_url": checkout_page_url}

    if gw.checkout_mode == "Hosted":
        updates["hosted_url"] = tx_data.get("authorization_url") or checkout_page_url

    if gw.payment_link_validity_hours:
        updates["link_expires_at"] = add_to_date(
            now_datetime(), hours=int(gw.payment_link_validity_hours)
        )

    for field, value in updates.items():
        log.db_set(field, value, update_modified=False)

    frappe.db.commit()

    return updates.get("hosted_url", checkout_page_url)


def _to_kobo(amount: float, currency: str) -> int:
    """
    Convert a human-readable amount to the smallest currency unit.

    Paystack expects amounts in the lowest denomination:
      NGN → kobo (x100)
      USD → cents (x100)
      GHS → pesewas (x100)
      ZAR → cents (x100)
      KES → cents (x100)

    All Paystack-supported currencies use x100.
    """
    return round(amount * 100)
