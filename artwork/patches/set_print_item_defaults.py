import frappe


def execute():
	"""v0.2: new print-item settings – look inside BOMs by default, use the 'Print' item group."""
	from artwork.install import ensure_print_group

	ensure_print_group()
	frappe.db.set_single_value("Artwork Settings", "include_bom_items", 1)
	if not frappe.db.get_single_value("Artwork Settings", "print_item_group") and frappe.db.exists("Item Group", "Print"):
		frappe.db.set_single_value("Artwork Settings", "print_item_group", "Print")
