import frappe


def execute():
	"""0.9.0: spot colours are rows in the item's finishes table, not a separate count. Existing upgrades are
	finish layers; a count already entered becomes that many Spot Colour rows."""
	frappe.db.sql("update `tabArtwork Print Upgrade` set kind = 'Finish layer' where ifnull(kind, '') = ''")
	from artwork.install import seed_upgrades
	seed_upgrades()
	if frappe.db.has_column("Item", "artwork_spot_colours"):
		for name, n in frappe.db.sql("select name, artwork_spot_colours from `tabItem` where ifnull(artwork_spot_colours, 0) > 0"):
			item = frappe.get_doc("Item", name)
			for _i in range(int(n)):
				item.append("artwork_upgrades", {"upgrade": "Spot Colour"})
			item.flags.ignore_validate = item.flags.ignore_mandatory = True
			item.save(ignore_permissions=True)
	if frappe.db.exists("Custom Field", "Item-artwork_spot_colours"):
		frappe.delete_doc("Custom Field", "Item-artwork_spot_colours", force=True, ignore_permissions=True)
