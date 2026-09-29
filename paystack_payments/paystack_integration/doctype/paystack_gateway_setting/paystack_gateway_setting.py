"""
paystack_gateway_setting.py
Controller for Paystack Gateway Setting.

Core responsibilities (no ERPNext required):
  - Validate Paystack API keys.
  - Register/deregister the Payment Gateway and Payment Gateway Account
    in the payments app.
  - Implement the PaymentGatewayController interface (get_payment_url, etc.)
    so the payments app can route Web Form payments through Paystack.

ERPNext-specific fields (company, suspense_account, mode_of_payment, etc.)
are present on the form but not validated or required when ERPNext is absent.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from paystack_payments.gateway.client import PaystackClient, PaystackError

_SUPPORTED_CURRENCIES = frozenset({"NGN", "USD", "GHS", "ZAR", "KES"})


class PaystackGatewaySetting(Document):

    # ── Frappe lifecycle ──────────────────────────────────────────────────────

    def validate(self) -> None:
        self._validate_currency()
        if self.enabled:
            self._validate_keys()

    def on_update(self) -> None:
        if self.enabled:
            self._register_payment_gateway()
        else:
            self._deregister_payment_gateway()

    def on_trash(self) -> None:
        self._deregister_payment_gateway()

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_currency(self) -> None:
        if self.currency and self.currency not in _SUPPORTED_CURRENCIES:
            frappe.throw(
                _("Currency '{0}' is not supported by Paystack. Supported: {1}.").format(
                    self.currency, ", ".join(sorted(_SUPPORTED_CURRENCIES))
                )
            )

    def _validate_keys(self) -> None:
        """
        Ping Paystack to confirm the keys are valid.
        Secret key is never logged on failure.
        """
        try:
            client = PaystackClient(
                secret_key=self.get_password("secret_key"),
                test_mode=bool(self.test_mode),
            )
            client.validate_keys()
        except PaystackError as exc:
            frappe.throw(
                _("Paystack API key validation failed: {0}").format(str(exc))
            )
        except Exception as exc:  # noqa: BLE001
            frappe.throw(
                _("Could not reach Paystack to validate keys: {0}").format(str(exc))
            )

    def validate_transaction_currency(self, currency: str) -> None:
        """Called by the payments app before initiating a transaction."""
        if currency != self.currency:
            frappe.throw(
                _(
                    "Paystack gateway '{0}' is configured for {1}, not {2}."
                ).format(self.name, self.currency, currency)
            )

    # ── payments app registration ─────────────────────────────────────────────

    def _register_payment_gateway(self) -> None:
        """
        Create or update the Payment Gateway and Payment Gateway Account
        records in the payments app. These are the authoritative registry.
        """
        gw_name = f"Paystack - {self.name}"

        if not frappe.db.exists("Payment Gateway", gw_name):
            frappe.get_doc(
                {
                    "doctype": "Payment Gateway",
                    "gateway": gw_name,
                    "gateway_settings": "Paystack Gateway Setting",
                    "gateway_controller": self.name,
                }
            ).insert(ignore_permissions=True)
        else:
            frappe.db.set_value(
                "Payment Gateway", gw_name, "gateway_controller", self.name
            )

        if not frappe.db.exists("Payment Gateway Account", gw_name):
            pga = frappe.get_doc(
                {
                    "doctype": "Payment Gateway Account",
                    "is_default": 0,
                    "payment_gateway": gw_name,
                    "currency": self.currency,
                    "payment_channel": "Email",
                }
            )
            # payment_account is an ERPNext Account field — only set when available.
            if self.get("suspense_account"):
                pga.payment_account = self.suspense_account
            pga.insert(ignore_permissions=True)

        self.db_set("payment_gateway", gw_name, update_modified=False)
        frappe.db.commit()

    def _deregister_payment_gateway(self) -> None:
        gw_name = f"Paystack - {self.name}"
        for dt in ("Payment Gateway Account", "Payment Gateway"):
            if frappe.db.exists(dt, gw_name):
                frappe.delete_doc(dt, gw_name, ignore_permissions=True)
        self.db_set("payment_gateway", None, update_modified=False)
        frappe.db.commit()

    # ── PaymentGatewayController interface ────────────────────────────────────
    # These methods are called by the payments app.

    def get_payment_url(self, **kwargs) -> str:
        """
        Called by the payments app Web Form integration.
        kwargs: amount, title, description, reference_doctype,
                reference_docname, payer_email, payer_name, order_id, currency
        """
        from paystack_payments.gateway.checkout import create_payment

        return create_payment(
            gateway_setting=self.name,
            amount=float(kwargs.get("amount") or 0),
            currency=kwargs.get("currency") or self.currency,
            payer_email=kwargs.get("payer_email") or kwargs.get("email") or "",
            payer_name=kwargs.get("payer_name") or "",
            description=kwargs.get("description") or kwargs.get("title") or "",
            reference_doctype=kwargs.get("reference_doctype") or "",
            reference_docname=kwargs.get("reference_docname") or kwargs.get("order_id") or "",
        )

    def request_for_payment(self, **kwargs) -> str:
        """Phone/POS channel — same as get_payment_url."""
        return self.get_payment_url(**kwargs)

    def on_payment_request_submission(self, data) -> None:
        """Called by the payments app when a Payment Request form is submitted."""
        # Handled via webhook + retry sweep.
