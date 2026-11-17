import frappe


def execute():
	"""v0.2: Artwork Components were folded into Artwork Proof rows; print spec moved onto Item."""
	for dt in ("Artwork Component", "Artwork Component Upgrade"):
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, force=True, ignore_missing=True)
	for fname in ("Item-default_artwork_template", "Item-default_artwork_finish"):
		if frappe.db.exists("Custom Field", fname):
			frappe.delete_doc("Custom Field", fname, force=True)
