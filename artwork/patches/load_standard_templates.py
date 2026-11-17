import frappe


def execute():
	"""Variant field + the standard Takt / Rad / Voit templates (v0.7.0)."""
	frappe.reload_doc("artwork", "doctype", "artwork_supplier_template")
	from artwork.template_loader import load

	load()
