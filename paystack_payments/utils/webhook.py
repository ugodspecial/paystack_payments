"""
webhook.py
Handles Paystack webhook events.
Signature-verified before any business logic runs.
No ERPNext imports.
"""

import hashlib
import hmac
import ipaddress
import json

import frappe
from frappe.utils import now_datetime

# Per-source rate limits (requests per minute)
_IP_LIMIT = 100
_GLOBAL_LIMIT = 1000


def handle_webhook(raw_body: bytes, signature: str, ip: str):
    """
    Entry point called by the whitelisted API endpoint.
    Returns HTTP 200 on a valid signature regardless of subsequent processing
    (Paystack best-practice).
    """
    gw = _find_gateway_for_signature(raw_body, signature)
    if not gw:
        _audit_reject("Invalid or unverifiable webhook signature", ip, raw_body)
        return  # Still 200 to Paystack

    if not _ip_allowed(ip, gw):
        _audit_reject(f"Webhook source IP {ip} not in allowlist", ip, raw_body)
        return

    try:
        event = json.loads(raw_body)
    except ValueError:
        _audit_reject("Non-JSON webhook body", ip, raw_body)
        return

    event_type = event.get("event", "")
    data = event.get("data", {})

    dispatch = {
        "charge.success": _on_charge_success,
        "charge.failed": _on_charge_failed,
        "refund.processed": _on_refund_processed,
        "refund.failed": _on_refund_failed,
        "settlement.success": _on_settlement_success,
    }

    handler = dispatch.get(event_type)
    if handler:
        try:
            handler(data, gw)
        except Exception as exc:  # noqa: BLE001
            frappe.log_error(
                title=f"Paystack webhook handler error: {event_type}",
                message=str(exc),
            )


# ── Gateway discovery ─────────────────────────────────────────────────────────

def _find_gateway_for_signature(raw_body: bytes, signature: str):
    """
    Find the enabled gateway whose signing secret verifies this request.
    Rejects if more than one matches (per-company key hygiene).
    """
    gateways = frappe.get_all(
        "Paystack Gateway Setting",
        filters={"enabled": 1},
        pluck="name",
    )
    matching = []
    for gw_name in gateways:
        gw = frappe.get_doc("Paystack Gateway Setting", gw_name)
        secret = gw.get_password("webhook_secret") or gw.get_password("secret_key")
        expected = hmac.new(
            secret.encode("utf-8"), raw_body, hashlib.sha512
        ).hexdigest()
        if hmac.compare_digest(expected, signature or ""):
            matching.append(gw)

    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        frappe.log_error(
            title="Paystack: one signing secret serves several companies",
            message="Give each company its own key pair.",
        )
    return None


# ── IP allowlist ──────────────────────────────────────────────────────────────

def _ip_allowed(ip: str, gw) -> bool:
    raw = (gw.allowed_webhook_ips or "").strip()
    if not raw:
        return True
    src = ipaddress.ip_address(ip)
    for line in raw.replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            if "/" in line:
                if src in ipaddress.ip_network(line, strict=False):
                    return True
            elif src == ipaddress.ip_address(line):
                return True
        except ValueError:
            pass
    return False


# ── Event handlers ────────────────────────────────────────────────────────────

def _on_charge_success(data: dict, gw):
    reference = data.get("reference")
    txn_id = str(data.get("id", ""))
    amount_kobo = data.get("amount", 0)
    fee_kobo = (data.get("fees") or 0)
    paid_at = data.get("paid_at") or str(now_datetime())

    if not reference:
        return

    # Deduplicate
    existing = frappe.db.get_value(
        "Paystack Payment Log", {"paystack_txn_id": txn_id}, "name"
    )
    if existing:
        return

    log = _get_log_by_reference(reference)
    if not log:
        return

    # Company scope check
    if log.company != gw.company:
        return

    log.mark_processed(
        txn_id=txn_id,
        amount_paid=amount_kobo / 100,
        fee=fee_kobo / 100,
        payment_date=paid_at,
    )

    # Store card authorization if reusable
    _store_authorization(data, gw)

    # Try to settle immediately
    try:
        log.settle_payment_request()
    except Exception as exc:  # noqa: BLE001
        log.errors = str(exc)
        log.save(ignore_permissions=True)
        frappe.db.commit()


def _on_charge_failed(data: dict, gw):
    reference = data.get("reference")
    if not reference:
        return
    log = _get_log_by_reference(reference)
    if log and log.company == gw.company:
        log.mark_failed(data.get("gateway_response", "Charge failed"))


def _on_refund_processed(data: dict, gw):
    txn_id = str(data.get("transaction", {}).get("id", ""))
    refund_id = str(data.get("id", ""))
    amount_kobo = data.get("amount", 0)

    log = frappe.db.get_value(
        "Paystack Payment Log", {"paystack_txn_id": txn_id}, "name"
    )
    if not log:
        return

    refund_log = frappe.db.get_value(
        "Paystack Refund Log", {"payment_log": log, "status": "Pending"}, "name"
    )
    if not refund_log:
        return

    rl = frappe.get_doc("Paystack Refund Log", refund_log)
    rl.paystack_refund_id = refund_id
    rl.amount = amount_kobo / 100
    rl.save(ignore_permissions=True)
    rl.book_reversal()


def _on_refund_failed(data: dict, gw):
    txn_id = str(data.get("transaction", {}).get("id", ""))
    log_name = frappe.db.get_value(
        "Paystack Payment Log", {"paystack_txn_id": txn_id}, "name"
    )
    if log_name:
        rl = frappe.db.get_value(
            "Paystack Refund Log", {"payment_log": log_name, "status": "Pending"}, "name"
        )
        if rl:
            frappe.db.set_value("Paystack Refund Log", rl, "status", "Failed")
            frappe.db.commit()


def _on_settlement_success(data: dict, gw):
    settlement_id = str(data.get("id", ""))
    if not settlement_id:
        return

    if frappe.db.exists("Paystack Settlement", settlement_id):
        return

    settlement = frappe.get_doc(
        {
            "doctype": "Paystack Settlement",
            "paystack_settlement_id": settlement_id,
            "gateway_setting": gw.name,
            "company": gw.company,
            "currency": gw.currency,
            "gross": (data.get("total_amount") or 0) / 100,
            "fees": (data.get("total_fees") or 0) / 100,
            "deductions": (data.get("total_processed") or 0) / 100,
            "net": (data.get("settlement_amount") or 0) / 100,
            "settlement_date": data.get("settled_at", "")[:10] or frappe.utils.today(),
        }
    )
    settlement.insert(ignore_permissions=True)
    frappe.db.commit()
    settlement.book_journal_entry()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_log_by_reference(reference: str):
    name = frappe.db.get_value("Paystack Payment Log", reference, "name")
    if name:
        return frappe.get_doc("Paystack Payment Log", name)
    return None


def _store_authorization(data: dict, gw):
    auth = (data.get("authorization") or {})
    if not (auth.get("reusable") and auth.get("channel") == "card"):
        return
    code = auth.get("authorization_code")
    sig = auth.get("signature")
    if not (code and sig):
        return

    customer_data = data.get("customer") or {}
    customer_email = customer_data.get("email", "")
    customer = frappe.db.get_value("Customer", {"email_id": customer_email}, "name")
    if not customer:
        return

    exists = frappe.db.exists("Paystack Customer Authorization", {"signature": sig})
    if exists:
        return

    ca = frappe.get_doc(
        {
            "doctype": "Paystack Customer Authorization",
            "customer": customer,
            "authorization_code": code,
            "signature": sig,
            "card_brand": auth.get("brand", ""),
            "bank": auth.get("bank", ""),
            "last_four": auth.get("last4", ""),
            "expiry_month": auth.get("exp_month", ""),
            "expiry_year": auth.get("exp_year", ""),
            "email": customer_email,
            "is_reusable": 1,
            "is_active": 1,
        }
    )
    ca.insert(ignore_permissions=True)
    frappe.db.commit()


def _audit_reject(reason: str, ip: str, raw_body: bytes):
    frappe.get_doc(
        {
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "integration_request_service": "Paystack",
            "status": "Failed",
            "url": "webhook",
            "data": raw_body[:2000].decode("utf-8", errors="replace"),
            "error": reason,
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
