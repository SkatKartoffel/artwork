import frappe


def execute():
	"""v0.5: one Print Template field on print items; supplier + packing component come from it."""
	from artwork.install import CUSTOM_FIELDS, hide_packing_component_on_print_items, seed_sizes_and_types
	from artwork.processing import print_groups
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	frappe.reload_doc("artwork", "doctype", "artwork_print_type")
	frappe.reload_doc("artwork", "doctype", "artwork_supplier_template")
	create_custom_fields(CUSTOM_FIELDS, update=True)
	seed_sizes_and_types()  # also fills packing components on existing print types
	for name in ("Item-artwork_product_size", "Item-artwork_print_type"):
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=True)
	for t in frappe.get_all("Artwork Supplier Template", fields=["name", "print_type"]):
		if t.print_type:
			frappe.db.set_value("Artwork Supplier Template", t.name, "packing_component",
				frappe.db.get_value("Artwork Print Type", t.print_type, "packing_component"), update_modified=False)
	groups = print_groups()
	if groups:
		frappe.db.sql("update `tabItem` set is_print_item = (item_group in %(g)s)", {"g": tuple(groups)})
		has_packing = frappe.get_meta("Item").has_field("packing_component")
		for item, tpl in frappe.get_all("Item", filters={"is_print_item": 1, "artwork_template": ["is", "set"]},
				fields=["name", "artwork_template"], as_list=True):
			t = frappe.db.get_value("Artwork Supplier Template", tpl, ["supplier", "packing_component"], as_dict=True)
			if not t:
				continue
			values = {"artwork_supplier": t.supplier}
			if has_packing and t.packing_component:
				values["packing_component"] = t.packing_component
			frappe.db.set_value("Item", item, values, update_modified=False)
	hide_packing_component_on_print_items()
