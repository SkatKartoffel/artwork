# Copyright (c) 2026, Packaged Sounds and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ArtworkColourProfile(Document):
	def validate(self):
		if not self.icc_file:
			return
		from artwork.processing import file_path

		path = file_path(self.icc_file)
		if not path:
			return
		try:
			with open(path, "rb") as fh:
				header = fh.read(40)
		except OSError:
			return
		if len(header) < 40 or header[36:40] != b"acsp":
			frappe.throw(_("{0} is not a valid ICC profile.").format(self.icc_file))
		if header[16:20] != b"CMYK":
			frappe.throw(_("The output profile must be a CMYK profile (this one is {0}).").format(
				header[16:20].decode("ascii", "replace").strip()))
		if self.max_ink and not (100 <= self.max_ink <= 400):
			frappe.throw(_("Maximum ink must be between 100% and 400%."))
