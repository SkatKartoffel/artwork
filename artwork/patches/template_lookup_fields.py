import frappe


def execute():
	"""v0.3: Items pick Supplier + Product Size + Print Type; the template is looked up from those."""
	from artwork.install import CUSTOM_FIELDS, seed_sizes_and_types
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	frappe.reload_doc("artwork", "doctype", "artwork_product_size")
	frappe.reload_doc("artwork", "doctype", "artwork_print_type")
	frappe.reload_doc("artwork", "doctype", "artwork_supplier_template")
	create_custom_fields(CUSTOM_FIELDS, update=True)
	seed_sizes_and_types()
	# Items that already pointed at a template: copy its supplier / size / type across
	for item, tpl in frappe.get_all("Item", filters={"artwork_template": ["is", "set"]},
			fields=["name", "artwork_template"], as_list=True):
		t = frappe.db.get_value("Artwork Supplier Template", tpl, ["supplier", "product_size", "print_type"], as_dict=True)
		if t:
			frappe.db.set_value("Item", item, {"artwork_supplier": t.supplier,
				"artwork_product_size": t.product_size, "artwork_print_type": t.print_type}, update_modified=False)
