# paystack_payments

> **Paystack payment gateway for Frappe** — depends on the `payments` app only.  
> Works with ERPNext, HRMS, or any other Frappe app that has `payments` installed.  
> No ERPNext requirement.

---

## Why this app?

The original [`frappe_paystack`](https://github.com/mymi14s/frappe_paystack) app depends on
ERPNext, which means it cannot be installed on Frappe sites that use other apps
(e.g. Education, Healthcare, custom apps).  
`paystack_payments` replaces that dependency with the first-party
[`payments`](https://github.com/frappe/payments) app, so it works anywhere `payments` is installed.

---

## Features

| Feature | Details |
|---|---|
| **Inline & Hosted checkout** | Paystack popup or Paystack-hosted page |
| **Webhook handling** | HMAC-SHA512 verified, per-company key routing, IP allowlist |
| **Automatic Payment Entry** | Driven from Payment Request (payments app) |
| **Refunds** | Full & partial, webhook-driven reversal JE |
| **Settlements** | `settlement.success` webhook → Journal Entry (Dr Bank, Cr Suspense) |
| **Saved cards** | Reusable authorization codes stored per Customer |
| **Subscription auto-charge** | Daily sweep charges subscription invoices with saved cards |
| **Reconciliation** | Daily sweep + on-demand report vs Paystack API |
| **5 reports** | Activity · Transactions · Unsettled · Settlements vs Ledger · Customer Volume |
| **Portal pages** | `/paystack-checkout/<ref>` · `/my-payments` |
| **QR & link Jinja helpers** | Drop payment links and QR codes into any print format |

---

## Prerequisites

| App | Version |
|---|---|
| Frappe | v15 or v16 |
| payments | any compatible version |

ERPNext is **not** required.

---

## Installation

```bash
# From your bench directory
bench get-app https://github.com/ugodspecial/paystack_payments
bench --site your.site install-app paystack_payments
bench --site your.site migrate
```

---

## Quick Setup

1. Open **Paystack Dashboard → Paystack Gateway Setting → New**
2. Fill in:
   - **Company** and **Currency** (NGN, USD, GHS, ZAR, or KES)
   - **Public Key** and **Secret Key** from your Paystack dashboard
   - **Suspense Account** (receives all gross captures)
   - **Mode of Payment** → select *Paystack*
   - **Settlement Bank Account** and **Paystack Fee Account** (for JE posting)
3. Tick **Enabled** and save — the app registers a `Payment Gateway` automatically.
4. Add the webhook URL in your Paystack dashboard:
   ```
   https://your.site/api/method/paystack_payments.api.paystack_webhook
   ```
5. Paste the **Webhook Secret** back into the Gateway Setting.

---

## How payments flow

```
Payment Request (payments app)
    │
    ▼ on_submit → events/payment_request.py
Paystack Payment Log created
    │
    ▼ checkout URL returned
Customer pays on /paystack-checkout/<ref>  OR  Paystack Hosted page
    │
    ▼ charge.success webhook
Payment Log → status: Processed
    │
    ▼ settle_payment_request()
Payment Entry submitted (via payments app Payment Request.set_as_paid)
    │
    ▼ status: Completed
settlement.success webhook → Paystack Settlement → Journal Entry
```

---

## Webhook events handled

| Event | Action |
|---|---|
| `charge.success` | Mark log Processed → create Payment Entry |
| `charge.failed` | Mark log Failed |
| `refund.processed` | Post reversal Journal Entry via Paystack Refund Log |
| `refund.failed` | Mark Refund Log Failed |
| `settlement.success` | Create Paystack Settlement → post JE (Dr Bank, Cr Suspense) |

---

## Reports

| Report | Purpose |
|---|---|
| **Paystack Activity** | Daily volume and charge counts with bar chart |
| **Paystack Transactions** | Row-level log with all key fields |
| **Paystack Unsettled Payments** | Processed logs with no Payment Entry |
| **Paystack Settlements vs Ledger** | Settlement JE variance check |
| **Customer Paystack Volume** | Total paid / refunded / net per email |

---

## Jinja helpers (print formats)

```html
{% set link = paystack_payment_link(doc) %}
{% if link %}
  <a href="{{ link }}">Pay Online</a>
  <img src="{{ paystack_payment_qr(doc) }}" width="120">
{% endif %}
```

---

## Scheduler jobs

| Cron | Job |
|---|---|
| Every 10 min | Retry Processed logs with no Payment Entry (exponential back-off) |
| Every hour | Retry Failed settlements |
| Daily 02:00 | Reconciliation sweep + subscription invoice charging |

---

## Multi-company support

Each company gets its own **Paystack Gateway Setting** with its own key pair.
Webhook routing is by HMAC signature — the signing secret is matched to the correct
company automatically.

---

## Supported currencies

NGN · USD · GHS · ZAR · KES

---

## License

MIT — see `license.txt`

---

## Credits

Inspired by [frappe_paystack](https://github.com/mymi14s/frappe_paystack) by mymi14s.  
Rewritten by **YoungAndCode LTD** to remove the ERPNext dependency and integrate
with the `payments` app.
