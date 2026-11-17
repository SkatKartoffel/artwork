import frappe


def execute():
	"""v0.2.2: the auto-created 'Print' item group must be selectable on Items (not a group node)."""
	if not frappe.db.exists("Item Group", "Print"):
		return
	is_group = frappe.db.get_value("Item Group", "Print", "is_group")
	has_children = frappe.db.exists("Item Group", {"parent_item_group": "Print"})
	if is_group and not has_children:
		frappe.db.set_value("Item Group", "Print", "is_group", 0)
