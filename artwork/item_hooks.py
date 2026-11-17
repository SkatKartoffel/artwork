import frappe


def set_artwork_template(doc, method=None):
	"""Keep the Item's matched Supplier Template in step with Supplier + Product Size + Print Type."""
	if not frappe.get_meta("Item").has_field("artwork_print_type"):
		return
	from artwork.processing import item_template

	doc.artwork_template = item_template(doc)
