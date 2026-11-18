import frappe


def execute():
	"""Print Supplier on print items now follows the Purchasing tab (v0.7.6): bring existing items in line."""
	if not frappe.get_meta("Item").has_field("artwork_supplier"):
		return
	from artwork.item_hooks import apply_print_spec

	for name in frappe.get_all("Item", filters={"is_print_item": 1}, pluck="name"):
		doc = frappe.get_doc("Item", name)
		before = (doc.get("artwork_supplier"), doc.get("artwork_template"), doc.description)
		apply_print_spec(doc)
		if (doc.get("artwork_supplier"), doc.get("artwork_template"), doc.description) != before:
			frappe.db.set_value("Item", name, {"artwork_supplier": doc.get("artwork_supplier"),
				"artwork_template": doc.get("artwork_template"), "description": doc.description}, update_modified=False)
