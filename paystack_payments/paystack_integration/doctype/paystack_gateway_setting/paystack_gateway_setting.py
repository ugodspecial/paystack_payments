"""
paystack_gateway_setting.py
Controller for Paystack Gateway Setting.

Integration with the payments app
----------------------------------
The payments app resolves a gateway controller like this:

    from payments.utils import get_payment_gateway_controller
    controller = get_payment_gateway_controller("Paystack - My Gateway")
    url = controller.get_payment_url(**kwargs)

For this to work:
  1. A `Payment Gateway` record must exist with:
       gateway          = "Paystack - My Gateway"   (the name LMS/payments uses)
       gateway_settings = "Paystack Gateway Setting" (the DocType name)
       gateway_controller = self.name               (this doc's name)

  2. A `Payment Gateway Account` must exist with:
       payment_gateway  = "Paystack - My Gateway"
       currency         = self.currency
       (payment_account is ERPNext-only; left empty on non-ERPNext sites)

  3. This controller must implement:
       get_payment_url(**kwargs)          - called by LMS / payments Web Form
       validate_transaction_currency(cur) - called by the payments app
       request_for_payment(**kwargs)      - called by the POS / phone channel
       on_payment_request_submission(data)- called after a Payment Request form

Security / correctness notes:
  - Gateway name prefix is "Paystack - " + this doc's name (never the currency).
  - Gateway Settings and Controller names stored on Payment Gateway must always
    match this doc's actual module path and name — re-registered on every save.
  - Secret key never logged.
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

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _gateway_name(self) -> str:
        """
        The canonical name used for both `Payment Gateway` and
        `Payment Gateway Account` records in the payments app.

        Format: "Paystack - <gateway_setting_name>"

        This is what LMS and other apps see when they list available gateways.
        It must be stable — never based on currency or company (those change).
        """
        return f"Paystack - {self.name}"

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_currency(self) -> None:
        if self.currency and self.currency not in _SUPPORTED_CURRENCIES:
            frappe.throw(
                _("Currency '{0}' is not supported by Paystack. Supported: {1}.").format(
                    self.currency, ", ".join(sorted(_SUPPORTED_CURRENCIES))
                )
            )

    def _validate_keys(self) -> None:
        """Ping Paystack to confirm keys are valid. Secret key never logged."""
        try:
            client = PaystackClient(
                secret_key=self.get_password("secret_key"),
                test_mode=bool(self.test_mode),
            )
            client.validate_keys()
        except PaystackError as exc:
            frappe.throw(_("Paystack API key validation failed: {0}").format(str(exc)))
        except Exception as exc:  # noqa: BLE001
            frappe.throw(_("Could not reach Paystack to validate keys: {0}").format(str(exc)))

    def validate_transaction_currency(self, currency: str) -> None:
        """Called by the payments app before initiating a transaction."""
        if currency and currency != self.currency:
            frappe.throw(
                _("Paystack gateway '{0}' is configured for {1}, not {2}.").format(
                    self.name, self.currency, currency
                )
            )

    # ── payments app registration ─────────────────────────────────────────────

    def _register_payment_gateway(self) -> None:
        """
        Create or update the `Payment Gateway` and `Payment Gateway Account`
        records so the payments app can find and call this controller.

        Payment Gateway:
          gateway            = "Paystack - <name>"
          gateway_settings   = "Paystack Gateway Setting"   <- DocType name
          gateway_controller = self.name                    <- this doc's name

        Payment Gateway Account:
          payment_gateway    = "Paystack - <name>"
          currency           = self.currency
          payment_account    = self.suspense_account        <- ERPNext only

        The payments app's get_payment_gateway_controller() does:
            gw = frappe.get_doc("Payment Gateway", gateway_name)
            return frappe.get_doc(gw.gateway_settings, gw.gateway_controller)
        So gateway_settings + gateway_controller must resolve to this exact doc.
        """
        gw_name = self._gateway_name()

        # ── Payment Gateway ───────────────────────────────────────────────────
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
            # Always refresh — the doc name or settings class may have changed.
            frappe.db.set_value(
                "Payment Gateway",
                gw_name,
                {
                    "gateway_settings": "Paystack Gateway Setting",
                    "gateway_controller": self.name,
                },
            )

        # ── Payment Gateway Account ───────────────────────────────────────────
        # LMS (and other apps) list available gateways by querying
        # Payment Gateway Account. Without this record, Paystack won't appear.
        if not frappe.db.exists("DocType", "Payment Gateway Account"):
            self.db_set("payment_gateway", gw_name, update_modified=False)
            frappe.db.commit()
            return

        account_name = frappe.db.get_value(
            "Payment Gateway Account", {"payment_gateway": gw_name}, "name"
        )
        if not account_name:
            pga = frappe.new_doc("Payment Gateway Account")
            pga.is_default = 0
            pga.payment_gateway = gw_name
            pga.currency = self.currency
            # Do not set payment_channel here. It is not part of every
            # payments-app/LMS version and an invalid field makes the whole
            # gateway registration fail before the gateway can be selected.
            # payment_account links to ERPNext Account doctype.
            # Leave blank on non-ERPNext sites — LMS does not require it.
            if self.get("suspense_account"):
                pga.payment_account = self.suspense_account
            pga.insert(ignore_permissions=True)
        else:
            updates: dict = {"currency": self.currency}
            if self.get("suspense_account"):
                updates["payment_account"] = self.suspense_account
            frappe.db.set_value("Payment Gateway Account", account_name, updates)

        self.db_set("payment_gateway", gw_name, update_modified=False)
        frappe.db.commit()

    def _deregister_payment_gateway(self) -> None:
        gw_name = self._gateway_name()
        for dt in ("Payment Gateway Account", "Payment Gateway"):
            if frappe.db.exists(dt, gw_name):
                frappe.delete_doc(dt, gw_name, ignore_permissions=True, force=True)
        self.db_set("payment_gateway", None, update_modified=False)
        frappe.db.commit()

    # ── PaymentGatewayController interface ────────────────────────────────────
    # Called by the payments app / LMS / any Frappe app.

    def get_payment_url(self, **kwargs) -> str:
        """
        Called by:
          - LMS billing flow
          - payments app Web Form integration
          - any Frappe app that calls get_payment_gateway_controller().get_payment_url()

        The payments app passes these kwargs (from payment_webform.py):
            amount, title, description, reference_doctype, reference_docname,
            payer_email, payer_name, order_id, currency, redirect_to

        LMS passes similar kwargs. We accept all of them defensively.
        """
        from paystack_payments.gateway.checkout import create_payment

        # Resolve payer email — LMS may pass email as 'payer_email' or use
        # the session user. The payments Web Form passes frappe.session.user.
        payer_email = (
            kwargs.get("payer_email")
            or kwargs.get("email")
            or frappe.session.user
        )
        if payer_email == "Guest":
            payer_email = ""

        # Resolve reference document — LMS uses reference_doctype/reference_docname;
        # older patterns use order_id.
        reference_doctype = kwargs.get("reference_doctype") or ""
        reference_docname = (
            kwargs.get("reference_docname")
            or kwargs.get("order_id")
            or ""
        )

        # Amount — LMS passes int (smallest unit? No — LMS passes the float
        # course amount). The payments Web Form passes the float amount.
        # Always convert to float for safety.
        amount = float(kwargs.get("amount") or 0)

        # Currency — prefer explicit, fall back to gateway default.
        currency = kwargs.get("currency") or self.currency

        # Description — LMS passes title; payments app passes description.
        description = (
            kwargs.get("description")
            or kwargs.get("title")
            or f"Payment for {reference_doctype} {reference_docname}".strip()
        )

        # success_redirect — some callers pass redirect_to.
        success_redirect = kwargs.get("redirect_to") or kwargs.get("success_redirect_url") or ""

        return create_payment(
            gateway_setting=self.name,
            amount=amount,
            currency=currency,
            payer_email=payer_email,
            payer_name=kwargs.get("payer_name") or frappe.utils.get_fullname(frappe.session.user),
            description=description,
            reference_doctype=reference_doctype,
            reference_docname=reference_docname,
            success_redirect_url=success_redirect,
        )

    def request_for_payment(self, **kwargs) -> str:
        """Phone/POS channel — same as get_payment_url."""
        return self.get_payment_url(**kwargs)

    def on_payment_request_submission(self, data) -> None:
        """Called by the payments app when a Payment Request form is submitted."""
        # Handled via webhook + retry sweep.
