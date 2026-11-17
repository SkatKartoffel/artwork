import frappe


def execute():
	"""v0.2.1: make sure the Artwork workspace no longer links to the removed Artwork Component."""
	frappe.reload_doc("artwork", "workspace", "artwork", force=True)
	for dt in ("Workspace Shortcut", "Workspace Link"):
		frappe.db.delete(dt, {"link_to": "Artwork Component"})
	frappe.clear_cache()
