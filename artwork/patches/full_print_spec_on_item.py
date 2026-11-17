import frappe


def execute():
	"""v0.6: full print spec on the Item (supplier, size, type, stock, colours, finish, coating, notes);
	template matched from supplier + size + type; Description built from the spec."""
	from artwork.install import CUSTOM_FIELDS, hide_packing_component_on_print_items, seed_sizes_and_types, seed_spec_lists
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	for dt in ("artwork_stock", "artwork_print_colour", "artwork_coating", "artwork_print_type", "artwork_supplier_template"):
		frappe.reload_doc("artwork", "doctype", dt)
	create_custom_fields(CUSTOM_FIELDS, update=True)
	seed_sizes_and_types()
	seed_spec_lists()
	# Items that picked a template directly (v0.5): copy its supplier / size / type into the item's own spec
	for item, tpl in frappe.get_all("Item", filters={"artwork_template": ["is", "set"]},
			fields=["name", "artwork_template"], as_list=True):
		t = frappe.db.get_value("Artwork Supplier Template", tpl, ["supplier", "product_size", "print_type"], as_dict=True)
		if t:
			frappe.db.set_value("Item", item, {"artwork_supplier": t.supplier, "artwork_product_size": t.product_size,
				"artwork_print_type": t.print_type}, update_modified=False)
	hide_packing_component_on_print_items()
