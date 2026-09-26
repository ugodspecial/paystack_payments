from frappe.model.document import Document
from frappe.utils import getdate


class PaystackCustomerAuthorization(Document):

    def is_expired(self):
        """Return True if the card has passed its expiry month."""
        if not self.expiry_month or not self.expiry_year:
            return False
        today = getdate()
        exp_year = int(self.expiry_year)
        exp_month = int(self.expiry_month)
        return (today.year, today.month) > (exp_year, exp_month)

    def is_usable(self):
        return self.is_active and self.is_reusable and not self.is_expired()
