import frappe


def execute():
	"""v0.6.1: give the Print Spec its own tab (Frappe 16 put the section under Accounting) and drop a
	leftover column break from v0.2."""
	from artwork.install import CUSTOM_FIELDS
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if frappe.db.exists("Custom Field", "Item-artwork_cb"):
		frappe.delete_doc("Custom Field", "Item-artwork_cb", force=True)
	create_custom_fields(CUSTOM_FIELDS, update=True)
	frappe.clear_cache(doctype="Item")
