import frappe


def item_validate(doc, method=None):
	"""Print items: flag them, and copy supplier + packing component from the chosen Print Template."""
	meta = frappe.get_meta("Item")
	if not meta.has_field("is_print_item"):
		return
	from artwork.processing import print_groups

	doc.is_print_item = 1 if doc.item_group in print_groups() else 0
	if not doc.is_print_item or not doc.get("artwork_template"):
		return
	tpl = frappe.db.get_value("Artwork Supplier Template", doc.artwork_template, ["supplier", "print_type"], as_dict=True)
	if not tpl:
		return
	doc.artwork_supplier = tpl.supplier
	if meta.has_field("packing_component"):
		packing = frappe.db.get_value("Artwork Print Type", tpl.print_type, "packing_component") if tpl.print_type else None
		doc.packing_component = packing or None
