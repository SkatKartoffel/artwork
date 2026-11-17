# Copyright (c) 2026, Packaged Sounds and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from artwork.artwork.doctype.artwork_proof.artwork_proof import refresh_proof_status
from artwork.processing import enqueue_component, inputs_signature, settings


class ArtworkComponent(Document):
	def validate(self):
		self.apply_item_defaults()
		self.validate_template()
		self.validate_locked()
		self.validate_upgrades()
		before = self.get_doc_before_save()
		self.flags.inputs_changed = before is None or inputs_signature(before) != inputs_signature(self)
		if self.flags.inputs_changed and not self.flags.from_processing:
			self.approved = 0
			if self.processing_status in ("Processed", "Failed"):
				self.processing_status = "Not Processed"

	def on_update(self):
		if self.flags.from_processing:
			return
		if self.flags.inputs_changed and self.artwork_file and cint(settings().auto_process):
			enqueue_component(self.name)
		refresh_proof_status(self.artwork_proof)

	def on_trash(self):
		if frappe.db.get_value("Artwork Proof", self.artwork_proof, "status") == "Approved":
			frappe.throw(_("This component belongs to an approved proof."))

	def after_delete(self):
		refresh_proof_status(self.artwork_proof)

	# ------------------------------------------------------------------
	def apply_item_defaults(self):
		meta = frappe.get_meta("Item")
		if self.item and meta.has_field("default_artwork_template") and not self.supplier_template:
			self.supplier_template = frappe.db.get_value("Item", self.item, "default_artwork_template")
		if self.item and meta.has_field("default_artwork_finish") and not self.finish:
			self.finish = frappe.db.get_value("Item", self.item, "default_artwork_finish")
		if self.finish:
			self.colour_profile = frappe.db.get_value("Artwork Finish", self.finish, "colour_profile")
		if self.artwork_proof:
			self.project, self.customer = frappe.db.get_value("Artwork Proof", self.artwork_proof, ["project", "customer"])
		if self.is_new() and self.outline_fonts is None:
			self.outline_fonts = cint(settings().outline_fonts_default)

	def validate_template(self):
		if not self.supplier_template:
			return
		tpl = frappe.db.get_value("Artwork Supplier Template", self.supplier_template,
			["supplier", "component_type", "enabled"], as_dict=True)
		if not tpl:
			return
		if not tpl.enabled:
			frappe.throw(_("Supplier template {0} is disabled.").format(self.supplier_template))
		if not self.supplier:
			self.supplier = tpl.supplier
		elif self.supplier != tpl.supplier:
			frappe.throw(_("Template {0} belongs to supplier {1}, not {2}.").format(
				self.supplier_template, tpl.supplier, self.supplier))
		if not self.component_type and tpl.component_type:
			self.component_type = tpl.component_type

	def validate_locked(self):
		if self.flags.from_processing or self.is_new():
			return
		status = frappe.db.get_value("Artwork Proof", self.artwork_proof, "status")
		if status in ("Approved", "Cancelled"):
			before = self.get_doc_before_save()
			if before and inputs_signature(before) != inputs_signature(self):
				frappe.throw(_("Proof {0} is {1}. Open a new revision on the proof before changing artwork.").format(
					self.artwork_proof, status))

	def validate_upgrades(self):
		seen = set()
		for row in self.get("upgrades") or []:
			if row.upgrade in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.upgrade))
			seen.add(row.upgrade)
			if row.source == "Artwork page" and not cint(row.page):
				row.page = 2
			if row.source == "Separate file" and not cint(row.page):
				row.page = 1

	@frappe.whitelist()
	def process(self):
		if not self.artwork_file:
			frappe.throw(_("Attach the customer's artwork first."))
		self.check_permission("write")
		enqueue_component(self.name)
		refresh_proof_status(self.artwork_proof)
		return "Queued"
