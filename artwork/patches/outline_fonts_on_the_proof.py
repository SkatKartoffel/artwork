import frappe


def execute():
	"""0.9.1: "Convert fonts to outlines" is a setting of the proof, not of each product. A proof keeps
	outlining on unless one of its items had it switched off."""
	had = frappe.db.has_column("Item", "artwork_outline_fonts")
	for name in frappe.get_all("Artwork Proof", pluck="name"):
		off = False
		if had:
			off = bool(frappe.db.sql("""select 1 from `tabArtwork Proof Item` r join `tabItem` i on i.name = r.item
				where r.parent = %s and ifnull(i.artwork_outline_fonts, 1) = 0 limit 1""", name))
		frappe.db.set_value("Artwork Proof", name, "outline_fonts", 0 if off else 1, update_modified=False)
	if frappe.db.exists("Custom Field", "Item-artwork_outline_fonts"):
		frappe.delete_doc("Custom Field", "Item-artwork_outline_fonts", force=True, ignore_permissions=True)
