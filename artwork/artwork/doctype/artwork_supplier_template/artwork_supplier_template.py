# Copyright (c) 2026, Packaged Sounds and contributors
# For license information, please see license.txt

import os
import tempfile

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class ArtworkSupplierTemplate(Document):
	def validate(self):
		if self.template_file and not self.template_file.lower().endswith((".pdf", ".ai")):
			frappe.throw(_("The supplier template must be a PDF (or PDF-compatible .ai) file."))
		if self.has_value_changed("template_file") or self.has_value_changed("template_page"):
			self.flags.redetect = True

	def on_update(self):
		if self.flags.redetect and self.template_file and not self.flags.in_detect:
			try:
				self.detect_geometry()
			except Exception:
				frappe.log_error(title=f"Template detection failed: {self.name}")

	@frappe.whitelist()
	def detect_geometry(self):
		"""Read the template PDF: page size, TrimBox, spine fold lines and a preview image."""
		from artwork.engine import geometry
		from artwork.engine.normalise import normalise_page
		from artwork.engine.util import mm
		from artwork.processing import file_path

		path = file_path(self.template_file)
		if not path or not os.path.exists(path):
			frappe.throw(_("Template file not found."))
		with tempfile.TemporaryDirectory() as tmp:
			norm = os.path.join(tmp, "t.pdf")
			geo = normalise_page(path, (self.template_page or 1) - 1, norm)
			hint = geometry.box_centre(geo["trim"])[0] if geo["has_trim"] else geo["width"] / 2
			spine = geometry.find_spine(norm, hint, flt(self.spine_width_mm) or None)
			self.page_width_mm = round(mm(geo["width"]), 2)
			self.page_height_mm = round(mm(geo["height"]), 2)
			self.detected_spine_centre_mm = round(mm(spine["centre"]), 2) if spine else None
			self.detected_spine_width_mm = round(mm(spine["width"]), 2) if spine else None
			if geo["has_trim"] and not (self.trim_width_mm and self.trim_height_mm):
				t = geo["trim"]
				self.trim_width_mm = round(mm(t[2] - t[0]), 2)
				self.trim_height_mm = round(mm(t[3] - t[1]), 2)
			if spine and not self.spine_width_mm:
				self.spine_width_mm = self.detected_spine_width_mm
			import pymupdf

			with pymupdf.open(norm) as d:
				png = d[0].get_pixmap(dpi=40).tobytes("png")
		f = frappe.get_doc({"doctype": "File", "file_name": f"{self.name}-preview.png".replace("/", "-"),
			"attached_to_doctype": self.doctype, "attached_to_name": self.name,
			"attached_to_field": "template_preview", "is_private": 1, "content": png})
		f.insert(ignore_permissions=True)
		self.template_preview = f.file_url
		self.flags.in_detect = True
		self.save()
		return {
			"page": [self.page_width_mm, self.page_height_mm],
			"spine_centre": self.detected_spine_centre_mm,
			"spine_width": self.detected_spine_width_mm,
			"trim": [self.trim_width_mm, self.trim_height_mm],
		}
