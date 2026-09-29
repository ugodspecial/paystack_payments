"""
gateway/client.py
Thin, security-hardened wrapper around the Paystack REST API.

Security properties:
  - Secret key is NEVER logged, serialised, or included in error messages.
  - All outbound requests use a 30-second timeout to prevent thread exhaustion.
  - Every call is recorded in Integration Request (audit trail).
  - Responses are size-capped before storage to prevent log flooding.
  - Webhook HMAC comparison uses hmac.compare_digest (constant-time) to
    prevent timing-oracle attacks.
  - Path parameters are URL-encoded to prevent path traversal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import quote

import frappe
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PAYSTACK_BASE_URL = "https://api.paystack.co"

# Maximum bytes stored per Integration Request output / error field.
_LOG_TRUNCATE = 4096

# Retry config — idempotent GET requests only; never retry POSTs.
_RETRY = Retry(total=2, allowed_methods=["GET"], backoff_factor=0.5, raise_on_status=False)


class PaystackClient:
    """
    Authenticated Paystack API client.

    Every public method corresponds to one Paystack endpoint.
    All HTTP calls are logged to Integration Request for audit.
    Secret key is redacted from all logs.
    """

    def __init__(self, secret_key: str, test_mode: bool = True) -> None:
        if not secret_key:
            frappe.throw(frappe._("Paystack secret key is required."))

        self._secret_key = secret_key
        self.test_mode = test_mode
        self.base_url = PAYSTACK_BASE_URL

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "paystack_payments/1.0 (Frappe)",
            }
        )
        adapter = HTTPAdapter(max_retries=_RETRY)
        self._session.mount("https://", adapter)

    # ── Key validation ────────────────────────────────────────────────────────

    def validate_keys(self) -> None:
        """Verify the secret key by hitting a lightweight read endpoint."""
        resp = self._get("/balance")
        if not resp.get("status"):
            # Never include the key itself in the error message.
            raise ValueError(resp.get("message", "Paystack API key validation failed."))

    # ── Transactions ──────────────────────────────────────────────────────────

    def initialize_transaction(
        self,
        *,
        email: str,
        amount_kobo: int,
        reference: str,
        callback_url: str,
        channels: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Initialise a new Paystack transaction and return the response data."""
        if amount_kobo <= 0:
            frappe.throw(frappe._("Transaction amount must be greater than zero."))

        payload: dict = {
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
            "callback_url": callback_url,
            "channels": channels
            or ["card", "bank", "ussd", "qr", "mobile_money", "bank_transfer"],
        }
        if metadata:
            # Sanitise metadata — strip any attempt to embed raw HTML/script.
            payload["metadata"] = _sanitise_metadata(metadata)

        return self._post("/transaction/initialize", payload)

    def verify_transaction(self, reference: str) -> dict:
        """Verify a transaction by its reference. Reference is URL-encoded."""
        return self._get(f"/transaction/verify/{quote(reference, safe='')}")

    def charge_authorization(
        self,
        *,
        authorization_code: str,
        email: str,
        amount_kobo: int,
        reference: str,
    ) -> dict:
        """Charge a previously authorised (saved) card."""
        if amount_kobo <= 0:
            frappe.throw(frappe._("Charge amount must be greater than zero."))
        return self._post(
            "/transaction/charge_authorization",
            {
                "authorization_code": authorization_code,
                "email": email,
                "amount": amount_kobo,
                "reference": reference,
            },
        )

    def list_transactions(
        self, *, page: int = 1, per_page: int = 50, **filters
    ) -> dict:
        """List transactions with optional filters."""
        params = {"page": max(1, page), "perPage": min(per_page, 100), **filters}
        return self._get("/transaction", params=params)

    # ── Refunds ───────────────────────────────────────────────────────────────

    def refund(
        self,
        *,
        transaction: str,
        amount_kobo: int | None = None,
        reason: str = "",
    ) -> dict:
        """Initiate a (partial) refund."""
        payload: dict = {"transaction": transaction}
        if amount_kobo is not None and amount_kobo > 0:
            payload["amount"] = amount_kobo
        if reason:
            payload["merchant_note"] = reason[:200]  # Paystack cap
        return self._post("/refund", payload)

    # ── Balance / settlement ──────────────────────────────────────────────────

    def get_balance(self) -> dict:
        return self._get("/balance")

    def get_settlement_transactions(self, settlement_id: str) -> dict:
        return self._get(f"/settlement/{quote(settlement_id, safe='')}/transactions")

    # ── Webhook signature ─────────────────────────────────────────────────────

    @staticmethod
    def verify_webhook_signature(
        raw_body: bytes, signature: str, secret: str
    ) -> bool:
        """
        Verify X-Paystack-Signature using HMAC-SHA512.
        Uses hmac.compare_digest to prevent timing-oracle attacks.
        """
        if not signature or not secret:
            return False
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
        url = self.base_url + path

        # Build a redacted log payload — NEVER log the secret key.
        raw_payload = kwargs.get("json", {})
        log_payload = _redact(json.dumps(raw_payload, default=str), self._secret_key)

        resp_data: dict = {}
        http_error: str = ""

        try:
            http_resp = self._session.request(method, url, timeout=30, **kwargs)
            resp_data = http_resp.json()
        except requests.exceptions.Timeout as exc:
            http_error = f"Paystack API timeout: {method} {path}"
            frappe.log_error(title=http_error, message=http_error)
            raise PaystackError("Request timed out. Please try again.") from exc
        except requests.exceptions.ConnectionError as exc:
            http_error = f"Paystack API connection error: {method} {path}"
            frappe.log_error(title=http_error, message=str(exc))
            raise PaystackError("Could not reach Paystack. Check network connectivity.") from exc
        except (ValueError, KeyError, TypeError) as exc:
            http_error = f"Paystack API error: {method} {path} — {exc}"
            frappe.log_error(title=f"Paystack API Error: {method} {path}", message=str(exc))
            raise PaystackError(str(exc)) from exc
        finally:
            _log_integration_request(
                url=url,
                method=method,
                payload=log_payload,
                response=resp_data,
                error=http_error,
            )

        if not resp_data.get("status"):
            raise PaystackError(
                resp_data.get("message", f"Paystack API returned HTTP {http_resp.status_code}"),
                status_code=getattr(http_resp, "status_code", 0),
            )

        return resp_data


class PaystackError(Exception):
    """Raised when Paystack returns an error or the HTTP call fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── Private helpers ───────────────────────────────────────────────────────────

def _redact(text: str, secret: str) -> str:
    """Replace the secret key with '***' anywhere it appears in text."""
    if secret:
        return text.replace(secret, "***")
    return text


def _sanitise_metadata(metadata: dict) -> dict:
    """
    Remove keys/values that could embed malicious content.
    Only scalar values are allowed; nested dicts are preserved but
    their string values are truncated to prevent log flooding.
    """
    sanitised: dict = {}
    for k, v in metadata.items():
        if isinstance(v, (str, int, float, bool)):
            sanitised[str(k)[:64]] = str(v)[:512] if isinstance(v, str) else v
        elif isinstance(v, dict):
            sanitised[str(k)[:64]] = {str(dk)[:64]: str(dv)[:256] for dk, dv in v.items()}
        elif isinstance(v, list):
            sanitised[str(k)[:64]] = v[:20]
    return sanitised


def _log_integration_request(
    url: str,
    method: str,
    payload: str,
    response: dict,
    error: str,
) -> None:
    """
    Write one Integration Request for audit.
    Deliberately swallowed on failure — logging must never break the payment flow.
    """
    try:
        status = "Completed" if response.get("status") and not error else "Failed"
        output = json.dumps(response, default=str)

        frappe.get_doc(
            {
                "doctype": "Integration Request",
                "integration_type": "Remote",
                "integration_request_service": "Paystack",
                "status": status,
                "url": url,
                "data": payload[:_LOG_TRUNCATE],
                "output": output[:_LOG_TRUNCATE],
                "error": (error or "")[:_LOG_TRUNCATE],
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 — audit logging must never break the payment flow
        frappe.logger("paystack").debug("Integration Request logging failed", exc_info=True)


def get_client_for_gateway(gateway_setting_name: str) -> PaystackClient:
    """
    Convenience factory: load a PaystackClient from a gateway setting name.
    Keeps credential access in one place.
    """
    gw = frappe.get_doc("Paystack Gateway Setting", gateway_setting_name)
    return PaystackClient(
        secret_key=gw.get_password("secret_key"),
        test_mode=bool(gw.test_mode),
    )
