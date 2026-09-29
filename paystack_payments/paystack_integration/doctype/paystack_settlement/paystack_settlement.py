"""
paystack_settlement.py
Tracks Paystack settlement payouts.

On non-ERPNext sites: records settlement data only.
On ERPNext sites: delegates Journal Entry posting to integrations/erpnext/settlement.py
via payment/lifecycle.py.
"""

from __future__ import annotations

from frappe.model.document import Document


class PaystackSettlement(Document):
    pass  # All logic is in payment/lifecycle.py and integrations/erpnext/settlement.py
