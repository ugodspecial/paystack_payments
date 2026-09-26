"""
paystack_gateway_setting.py
Controller for Paystack Gateway Setting.

Key responsibilities:
 - Validate keys against Paystack /transaction/verify on save
 - Register / de-register the Payment Gateway + Payment Gateway Account
   (both from the `payments` app — no ERPNext dependency)
 - Implement the `PaymentGatewayController` interface so the `payments` app
   can route Payment Requests through this gateway
"""

import frappe
from frappe import _
from frappe.model.document import Document

from paystack_payments.utils.paystack_client import PaystackClient


class PaystackGatewaySetting(Document):

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def validate(self):
        self._validate_currency()
        self._validate_keys()
        self._enforce_single_enabled_per_company()

    def on_update(self):
        if self.enabled:
            self._register_payment_gateway()
        else:
            self._deregister_payment_gateway()

    def on_trash(self):
        self._deregister_payment_gateway()

    # ── Validation helpers ────────────────────────────────────────────────────

    def _validate_currency(self):
        supported = {"NGN", "USD", "GHS", "ZAR", "KES"}
        if self.currency and self.currency not in supported:
            frappe.throw(
                _("Currency {0} is not supported by Paystack. Supported: {1}").format(
                    self.currency, ", ".join(sorted(supported))
                )
            )

    def _validate_keys(self):
        """
        Ping Paystack with the supplied keys to confirm they are valid.
        Uses a lightweight endpoint that returns 400 for bad keys but 200 for good ones.
        """
        try:
            client = PaystackClient(self.get_password("secret_key"), self.test_mode)
            client.validate_keys()
        except Exception as exc:  # noqa: BLE001
            frappe.throw(
                _("Paystack key validation failed: {0}").format(str(exc))
            )

    def _enforce_single_enabled_per_company(self):
        if not self.enabled:
            return
        existing = frappe.db.get_value(
            "Paystack Gateway Setting",
            {"company": self.company, "enabled": 1, "name": ("!=", self.name)},
            "name",
        )
        if existing:
            frappe.throw(
                _(
                    "Company {0} already has an enabled Paystack gateway: {1}. "
                    "Disable it before enabling this one."
                ).format(self.company, existing)
            )

    # ── Payment Gateway registration ──────────────────────────────────────────

    def _register_payment_gateway(self):
        """
        Create (or update) the `Payment Gateway` and `Payment Gateway Account`
        records from the `payments` app.
        """
        gateway_name = f"Paystack - {self.company}"

        # Payment Gateway (payments app doctype)
        if not frappe.db.exists("Payment Gateway", gateway_name):
            pg = frappe.get_doc(
                {
                    "doctype": "Payment Gateway",
                    "gateway": gateway_name,
                    "gateway_settings": "Paystack Gateway Setting",
                    "gateway_controller": self.name,
                }
            )
            pg.insert(ignore_permissions=True)
        else:
            frappe.db.set_value(
                "Payment Gateway",
                gateway_name,
                "gateway_controller",
                self.name,
            )

        # Payment Gateway Account (payments app doctype)
        pga_name = f"Paystack - {self.company}"
        if not frappe.db.exists("Payment Gateway Account", pga_name):
            pga = frappe.get_doc(
                {
                    "doctype": "Payment Gateway Account",
                    "is_default": 0,
                    "payment_gateway": gateway_name,
                    "payment_account": self.suspense_account,
                    "currency": self.currency,
                    "payment_channel": "Email",
                }
            )
            pga.insert(ignore_permissions=True)
        else:
            frappe.db.set_value(
                "Payment Gateway Account",
                pga_name,
                "payment_account",
                self.suspense_account,
            )

        self.db_set("payment_gateway", gateway_name, update_modified=False)
        frappe.db.commit()

    def _deregister_payment_gateway(self):
        gateway_name = f"Paystack - {self.company}"
        for dt in ("Payment Gateway Account", "Payment Gateway"):
            if frappe.db.exists(dt, gateway_name):
                frappe.delete_doc(dt, gateway_name, ignore_permissions=True)
        self.db_set("payment_gateway", None, update_modified=False)
        frappe.db.commit()

    # ── PaymentGatewayController interface ────────────────────────────────────
    # Called by the `payments` app when a Payment Request is submitted.

    def get_payment_url(self, **kwargs):
        """
        Return the checkout URL for the given Payment Request.
        kwargs keys: payment_request_name, amount, currency, payer_name, payer_email, description
        """
        from paystack_payments.utils.checkout import create_payment_log_and_url

        return create_payment_log_and_url(gateway_setting=self.name, **kwargs)

    def validate_transaction_currency(self, currency):
        if currency != self.currency:
            frappe.throw(
                _("Paystack gateway {0} only accepts {1}, not {2}.").format(
                    self.name, self.currency, currency
                )
            )

    def request_for_payment(self, **kwargs):
        """Called by the payments app for direct-charge flows (Phone POS channel)."""
        return self.get_payment_url(**kwargs)

    def on_payment_request_submission(self, data):
        """Called by the payments app after a Payment Request is submitted."""
        # Handled via webhook + retry sweep
