# Copyright (c) 2026, Packaged Sounds and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ArtworkPrintUpgrade(Document):
	def validate(self):
		self.kind = self.kind or "Finish layer"
		if self.kind == "Finish layer" and not (self.spot_name or "").strip():
			frappe.throw(_("A finish layer needs the spot colour name used for it in the artwork."))
