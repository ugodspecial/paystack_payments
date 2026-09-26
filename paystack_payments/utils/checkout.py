"""
checkout.py
Creates a Paystack Payment Log and returns the checkout URL.
Works for any source doctype — no ERPNext dependency.
"""

import frappe
from frappe.utils import add_to_date, get_url, now_datetime


def create_payment_log_and_url(
    gateway_setting: str,
    payment_request_name: str | None = None,
    amount: float = 0,
    currency: str = "NGN",
    payer_email: str = "",
    payer_name: str = "",
    description: str = "",
    reference_doctype: str = "",
    reference_name: str = "",
) -> str:
    """
    Create a Paystack Payment Log and initialise a transaction on Paystack.
    Returns the checkout page URL (/paystack-checkout/<log-name>).
    """
    gw = frappe.get_doc("Paystack Gateway Setting", gateway_setting)

    # Amount in kobo / smallest unit (×100)
    amount_kobo = int(amount * 100)

    # Create the Payment Log first so its name is the Paystack reference
    log = frappe.get_doc(
        {
            "doctype": "Paystack Payment Log",
            "gateway_setting": gateway_setting,
            "company": gw.company,
            "currency": gw.currency,
            "amount": amount,
            "payment_request": payment_request_name,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "payer_email": payer_email,
            "payer_name": payer_name,
            "status": "Pending",
        }
    )
    log.insert(ignore_permissions=True)
    frappe.db.commit()

    checkout_page = get_url(f"/paystack-checkout/{log.name}")

    from paystack_payments.utils.paystack_client import PaystackClient

    client = PaystackClient(gw.get_password("secret_key"), gw.test_mode)

    # Inline checkout: initialise and cache the access_code
    resp = client.initialize_transaction(
        email=payer_email or f"noreply+{log.name}@paystack.internal",
        amount_kobo=amount_kobo,
        reference=log.name,
        callback_url=checkout_page,
        metadata={
            "payment_log": log.name,
            "description": description,
            "payer_name": payer_name,
        },
    )
    data = resp.get("data", {})

    update = {
        "checkout_url": checkout_page,
    }
    if gw.checkout_mode == "Hosted":
        update["hosted_url"] = data.get("authorization_url") or checkout_page

    if gw.payment_link_validity_hours:
        update["link_expires_at"] = add_to_date(
            now_datetime(), hours=int(gw.payment_link_validity_hours)
        )

    for key, val in update.items():
        log.db_set(key, val, update_modified=False)

    frappe.db.commit()

    if gw.checkout_mode == "Hosted":
        return update.get("hosted_url", checkout_page)
    return checkout_page
