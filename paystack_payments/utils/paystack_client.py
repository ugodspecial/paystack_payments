"""
paystack_client.py
Thin wrapper around the Paystack REST API.
All HTTP calls are logged as Integration Requests for audit purposes.
No ERPNext imports.
"""

import hashlib
import hmac
import json

import frappe
import requests

LIVE_BASE = "https://api.paystack.co"
TEST_BASE = "https://api.paystack.co"  # Paystack uses the same base; test vs live is key-based


class PaystackClient:
    """
    Thin wrapper around requests.Session for the Paystack REST API.
    Logs every call to Integration Request (service=Paystack).
    """

    def __init__(self, secret_key: str, test_mode: bool = True):
        self.secret_key = secret_key
        self.test_mode = test_mode
        self.base = TEST_BASE if test_mode else LIVE_BASE
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
            }
        )

    # ── Public methods ────────────────────────────────────────────────────────

    def validate_keys(self):
        """Ping the Paystack balance endpoint; raises on auth failure."""
        resp = self._get("/balance")
        if not resp.get("status"):
            raise ValueError(resp.get("message", "Invalid keys"))

    def initialize_transaction(
        self,
        email: str,
        amount_kobo: int,
        reference: str,
        callback_url: str,
        channels: list | None = None,
        metadata: dict | None = None,
    ) -> dict:
        payload = {
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
            "callback_url": callback_url,
            "channels": channels or ["card", "bank", "ussd", "qr", "mobile_money", "bank_transfer"],
        }
        if metadata:
            payload["metadata"] = metadata
        return self._post("/transaction/initialize", payload)

    def verify_transaction(self, reference: str) -> dict:
        return self._get(f"/transaction/verify/{reference}")

    def refund(self, transaction: str, amount_kobo: int | None = None, reason: str = "") -> dict:
        payload: dict = {"transaction": transaction}
        if amount_kobo:
            payload["amount"] = amount_kobo
        if reason:
            payload["merchant_note"] = reason
        return self._post("/refund", payload)

    def charge_authorization(
        self, authorization_code: str, email: str, amount_kobo: int, reference: str
    ) -> dict:
        return self._post(
            "/transaction/charge_authorization",
            {
                "authorization_code": authorization_code,
                "email": email,
                "amount": amount_kobo,
                "reference": reference,
            },
        )

    def list_transactions(self, page: int = 1, per_page: int = 50, **filters) -> dict:
        params = {"page": page, "perPage": per_page, **filters}
        return self._get("/transaction", params=params)

    def get_settlement(self, settlement_id: str) -> dict:
        return self._get(f"/settlement/{settlement_id}/transactions")

    # ── Webhook signature verification ────────────────────────────────────────

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
        expected = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ── Internal HTTP helpers ─────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, data: dict) -> dict:
        return self._request("POST", path, json=data)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = self.base + path

        # Redact secret before logging
        log_payload = json.dumps(kwargs.get("json", {}), default=str)
        log_payload = log_payload.replace(self.secret_key, "***")

        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
            data = resp.json()
        except Exception as exc:
            frappe.log_error(
                title=f"Paystack API Error: {method} {path}",
                message=str(exc),
            )
            raise

        # Log to Integration Request
        try:
            ir = frappe.get_doc(
                {
                    "doctype": "Integration Request",
                    "integration_type": "Remote",
                    "integration_request_service": "Paystack",
                    "status": "Completed" if data.get("status") else "Failed",
                    "url": url,
                    "data": log_payload,
                    "output": json.dumps(data, default=str)[:5000],
                    "error": "" if data.get("status") else data.get("message", ""),
                }
            )
            ir.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as log_exc:  # noqa: BLE001
            # Intentionally swallowed — audit logging must never break the payment flow.
            frappe.logger("paystack").debug("Integration Request logging failed: %s", log_exc)

        if not resp.ok and not data.get("status"):
            raise PaystackError(
                data.get("message", f"HTTP {resp.status_code}"),
                status_code=resp.status_code,
            )

        return data


class PaystackError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
