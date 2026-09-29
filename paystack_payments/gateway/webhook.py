"""
gateway/webhook.py
Processes inbound Paystack webhook events.

Security properties:
  - HMAC-SHA512 signature verified before ANY business logic runs.
  - IP allowlist enforced per gateway setting.
  - One-time deduplication via paystack_txn_id prevents double-booking.
  - Always returns HTTP 200 to Paystack after signature check, regardless
    of downstream processing outcome (Paystack best-practice).
  - No ERPNext imports. All post-payment actions go through
    payment/lifecycle.py which routes to the appropriate adapter.
"""

from __future__ import annotations

import ipaddress
import json
import logging

import frappe
from frappe.utils import now_datetime

from paystack_payments.gateway.client import PaystackClient

logger = logging.getLogger(__name__)

# ── Public entry point ────────────────────────────────────────────────────────


def handle_webhook(raw_body: bytes, signature: str, ip: str) -> None:
    """
    Main entry point called by api.paystack_webhook().

    Steps:
      1. Find which enabled gateway matches this signature.
      2. Enforce IP allowlist.
      3. Parse event JSON.
      4. Dispatch to the correct event handler.

    All errors are logged and swallowed — Paystack must always get HTTP 200.
    """
    gw = _resolve_gateway(raw_body, signature)
    if gw is None:
        _audit_reject("Webhook signature could not be verified against any enabled gateway.", ip)
        return

    if not _ip_allowed(ip, gw):
        _audit_reject(
            f"Webhook source IP {ip!r} is not in the allowlist for gateway {gw.name!r}.",
            ip,
        )
        return

    try:
        event = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        _audit_reject(f"Non-JSON webhook body: {exc}", ip)
        return

    event_type: str = event.get("event", "")
    data: dict = event.get("data") or {}

    _DISPATCH = {
        "charge.success": _on_charge_success,
        "charge.failed": _on_charge_failed,
        "refund.processed": _on_refund_processed,
        "refund.failed": _on_refund_failed,
        "settlement.success": _on_settlement_success,
    }

    handler = _DISPATCH.get(event_type)
    if handler:
        try:
            handler(data, gw)
        except Exception:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack webhook handler error [{event_type}]",
                message=frappe.get_traceback(),
            )
    else:
        # Unknown/unsupported event — log and ignore.
        logger.debug("paystack_payments: unhandled webhook event %r", event_type)


# ── Gateway resolution ────────────────────────────────────────────────────────


def _resolve_gateway(raw_body: bytes, signature: str):
    """
    Iterate all enabled gateways and find the one whose signing secret
    produces a digest matching `signature`.

    Returns the matching gateway doc, or None.
    Raises a hard error if two gateways match the same signature (key hygiene
    violation — each company/gateway should have its own key pair).
    """
    gateway_names: list[str] = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1},
        pluck="name",
        ignore_permissions=True,
    )

    matches = []
    for name in gateway_names:
        gw = frappe.get_cached_doc("Paystack Gateway Setting", name)
        # Prefer the dedicated webhook_secret; fall back to secret_key.
        secret = gw.get_password("webhook_secret") or gw.get_password("secret_key")
        if PaystackClient.verify_webhook_signature(raw_body, signature, secret):
            matches.append(gw)

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        frappe.log_error(
            title="Paystack: ambiguous webhook signature",
            message=(
                "Multiple enabled gateway settings share the same signing secret. "
                "Give each gateway its own unique webhook secret. "
                f"Matching gateways: {[g.name for g in matches]}"
            ),
        )
    return None


# ── IP allowlist ──────────────────────────────────────────────────────────────


def _ip_allowed(ip: str, gw) -> bool:
    """
    Return True if `ip` is permitted by the gateway's allowlist.
    Empty allowlist = accept all sources.
    Supports individual IPs and CIDR ranges (one per line or comma-separated).
    """
    raw: str = (gw.allowed_webhook_ips or "").strip()
    if not raw:
        return True

    try:
        src = ipaddress.ip_address(ip)
    except ValueError:
        logger.warning("paystack_payments: could not parse source IP %r", ip)
        return False

    for entry in raw.replace(",", "\n").splitlines():
        entry = entry.strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if src in ipaddress.ip_network(entry, strict=False):
                    return True
            elif src == ipaddress.ip_address(entry):
                return True
        except ValueError:
            logger.warning("paystack_payments: invalid IP/CIDR in allowlist: %r", entry)

    return False


# ── Event handlers ────────────────────────────────────────────────────────────


def _on_charge_success(data: dict, gw) -> None:
    """
    Paystack charge.success event.

    1. Extract transaction details from the event payload.
    2. Deduplicate — ignore if already processed.
    3. Locate the Payment Log by its reference (= log name).
    4. Update log with capture details.
    5. Store reusable card authorisation if present.
    6. Delegate to payment lifecycle for post-payment actions.
    """
    reference: str = (data.get("reference") or "").strip()
    txn_id: str = str(data.get("id") or "")
    amount_kobo: int = int(data.get("amount") or 0)
    fee_kobo: int = int(data.get("fees") or 0)
    paid_at: str = data.get("paid_at") or str(now_datetime())

    if not reference or not txn_id:
        logger.warning("paystack_payments: charge.success missing reference or id")
        return

    # Deduplication — never process the same Paystack transaction twice.
    if frappe.db.exists(
        "Paystack Payment Log", {"paystack_txn_id": txn_id}
    ):
        logger.info(
            "paystack_payments: duplicate charge.success for txn_id=%r — ignored", txn_id
        )
        return

    log = _get_log(reference)
    if log is None:
        logger.warning(
            "paystack_payments: charge.success for unknown reference %r", reference
        )
        return

    # Scope check — reject cross-company webhook spoofing.
    if log.gateway_setting != gw.name:
        frappe.log_error(
            title="Paystack: gateway mismatch on webhook",
            message=(
                f"Payment Log {log.name} belongs to gateway {log.gateway_setting!r} "
                f"but webhook arrived at gateway {gw.name!r}."
            ),
        )
        return

    # Mark as captured.
    log.mark_processed(
        txn_id=txn_id,
        amount_paid=amount_kobo / 100,
        fee=fee_kobo / 100,
        payment_date=paid_at,
    )

    # Store reusable card authorisation (non-blocking).
    _maybe_store_authorization(data, gw)

    # Delegate to the payment lifecycle — this is where ERPNext / LMS /
    # School actions happen, all without being called from here.
    from paystack_payments.payment.lifecycle import on_payment_success

    on_payment_success(log)


def _on_charge_failed(data: dict, gw) -> None:
    reference: str = (data.get("reference") or "").strip()
    if not reference:
        return
    log = _get_log(reference)
    if log and log.gateway_setting == gw.name:
        log.mark_failed(data.get("gateway_response") or "Charge failed")

        from paystack_payments.payment.lifecycle import on_payment_failed
        on_payment_failed(log)


def _on_refund_processed(data: dict, gw) -> None:
    txn_id: str = str(data.get("transaction", {}).get("id") or "")
    refund_id: str = str(data.get("id") or "")
    amount_kobo: int = int(data.get("amount") or 0)

    log_name: str | None = frappe.db.get_value(
        "Paystack Payment Log", {"paystack_txn_id": txn_id}, "name"
    )
    if not log_name:
        return

    refund_log_name: str | None = frappe.db.get_value(
        "Paystack Refund Log",
        {"payment_log": log_name, "status": "Pending"},
        "name",
        order_by="creation asc",
    )
    if not refund_log_name:
        return

    rl = frappe.get_doc("Paystack Refund Log", refund_log_name)
    rl.paystack_refund_id = refund_id
    rl.amount = amount_kobo / 100
    rl.save(ignore_permissions=True)

    from paystack_payments.payment.lifecycle import on_refund_processed
    on_refund_processed(rl)


def _on_refund_failed(data: dict, gw) -> None:
    txn_id: str = str(data.get("transaction", {}).get("id") or "")
    log_name: str | None = frappe.db.get_value(
        "Paystack Payment Log", {"paystack_txn_id": txn_id}, "name"
    )
    if not log_name:
        return

    refund_log_name: str | None = frappe.db.get_value(
        "Paystack Refund Log",
        {"payment_log": log_name, "status": "Pending"},
        "name",
        order_by="creation asc",
    )
    if refund_log_name:
        frappe.db.set_value("Paystack Refund Log", refund_log_name, "status", "Failed")
        frappe.db.commit()


def _on_settlement_success(data: dict, gw) -> None:
    settlement_id: str = str(data.get("id") or "")
    if not settlement_id:
        return

    if frappe.db.exists("Paystack Settlement", settlement_id):
        logger.info(
            "paystack_payments: duplicate settlement.success for %r — ignored", settlement_id
        )
        return

    settlement = frappe.get_doc(
        {
            "doctype": "Paystack Settlement",
            "paystack_settlement_id": settlement_id,
            "gateway_setting": gw.name,
            "currency": gw.currency,
            "gross": int(data.get("total_amount") or 0) / 100,
            "fees": int(data.get("total_fees") or 0) / 100,
            "deductions": int(data.get("total_processed") or 0) / 100,
            "net": int(data.get("settlement_amount") or 0) / 100,
            "settlement_date": (data.get("settled_at") or "")[:10] or frappe.utils.today(),
            "status": "Pending",
        }
    )
    settlement.insert(ignore_permissions=True)
    frappe.db.commit()

    from paystack_payments.payment.lifecycle import on_settlement_received
    on_settlement_received(settlement)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_log(reference: str):
    """Load a Payment Log by its name (= Paystack reference). Returns None if not found."""
    try:
        return frappe.get_doc("Paystack Payment Log", reference)
    except frappe.DoesNotExistError:
        return None


def _maybe_store_authorization(charge_data: dict, gw) -> None:
    """
    If the charge includes a reusable card authorisation, store it.
    Runs inside a try/except so a storage failure never aborts the main flow.
    """
    try:
        auth: dict = charge_data.get("authorization") or {}
        if not (auth.get("reusable") and auth.get("channel") == "card"):
            return

        code: str = auth.get("authorization_code") or ""
        sig: str = auth.get("signature") or ""
        if not code or not sig:
            return

        # Idempotent — skip if already stored.
        if frappe.db.exists("Paystack Customer Authorization", {"signature": sig}):
            return

        customer_email: str = (charge_data.get("customer") or {}).get("email") or ""

        frappe.get_doc(
            {
                "doctype": "Paystack Customer Authorization",
                "gateway_setting": gw.name,
                "customer": customer_email,  # Data field — app-agnostic identifier
                "authorization_code": code,
                "signature": sig,
                "card_brand": (auth.get("brand") or "")[:64],
                "bank": (auth.get("bank") or "")[:128],
                "last_four": (auth.get("last4") or "")[:4],
                "expiry_month": str(auth.get("exp_month") or ""),
                "expiry_year": str(auth.get("exp_year") or ""),
                "email": customer_email,
                "is_reusable": 1,
                "is_active": 1,
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:  # noqa: BLE001
        frappe.log_error(
            title="Paystack: failed to store card authorization",
            message=frappe.get_traceback(),
        )


def _audit_reject(reason: str, ip: str) -> None:
    """Record a rejected webhook for audit purposes."""
    try:
        frappe.get_doc(
            {
                "doctype": "Integration Request",
                "integration_type": "Remote",
                "integration_request_service": "Paystack",
                "status": "Failed",
                "url": "webhook",
                "data": f"Source IP: {ip}",
                "error": reason[:1024],
            }
        ).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 — audit logging must never abort the main flow
        frappe.logger("paystack").debug("Webhook audit log failed", exc_info=True)
