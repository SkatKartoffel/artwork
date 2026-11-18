import frappe
from frappe.utils import cint


def escape_html(text: str) -> str:
	"""Escape only what breaks HTML; keep inch marks (12") readable."""
	return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def item_validate(doc, method=None):
	"""Print items: flag them, match the supplier template, set packing component and rebuild the Description."""
	meta = frappe.get_meta("Item")
	if not meta.has_field("is_print_item"):
		return
	from artwork.processing import print_groups

	doc.is_print_item = 1 if doc.item_group in print_groups() else 0
	if not doc.is_print_item:
		return
	apply_print_spec(doc, meta)


def purchasing_suppliers(doc) -> list:
	"""Suppliers set in ERPNext's own Item fields: Item Defaults first, then the Purchasing tab's Supplier Items."""
	out = []
	for d in doc.get("item_defaults") or []:
		if d.get("default_supplier") and d.default_supplier not in out:
			out.append(d.default_supplier)
	for s in doc.get("supplier_items") or []:
		if s.get("supplier") and s.supplier not in out:
			out.append(s.supplier)
	return out


def erpnext_supplier(doc):
	"""The first supplier set in ERPNext's own fields."""
	found = purchasing_suppliers(doc)
	return found[0] if found else None


def apply_print_spec(doc, meta=None):
	from artwork.processing import item_template

	meta = meta or frappe.get_meta("Item")
	# The Print Supplier follows Purchasing: when the item has purchasing suppliers, the print supplier is one
	# of them, so changing the supplier on the Purchasing tab changes it here too. With no purchasing
	# supplier at all it can be picked by hand.
	suppliers = purchasing_suppliers(doc)
	if suppliers and doc.get("artwork_supplier") not in suppliers:
		doc.artwork_supplier = suppliers[0]
	doc.artwork_template = item_template(doc)
	if meta.has_field("packing_component") and doc.get("artwork_print_type"):
		doc.packing_component = frappe.db.get_value("Artwork Print Type", doc.artwork_print_type, "packing_component") or None
	text = spec_description(doc)
	if text:
		doc.description = text
	return doc


def _label(doctype, name):
	if not name:
		return None
	return frappe.db.get_value(doctype, name, "description_text") or name


def spec_description(doc) -> str:
	"""e.g. 12" Outer Sleeve – 3mm Spine · 350gsm GC1 Board · 4/0 CMYK · Matt finish · Matt Lamination ·
	Spot UV, Hot Foil Gold · <notes>"""
	product = " ".join(filter(None, [doc.get("artwork_product_size"), doc.get("artwork_print_type")]))
	if product and doc.get("artwork_variant"):
		product += f" ({doc.artwork_variant})"
	parts = [
		product,
		_label("Artwork Stock", doc.get("artwork_stock")),
		_label("Artwork Print Colour", doc.get("artwork_print_colours")),
		(f"+ {cint(doc.artwork_spot_colours)} spot colour" + ("s" if cint(doc.artwork_spot_colours) != 1 else ""))
		if cint(doc.get("artwork_spot_colours")) else None,
		f"{doc.artwork_finish} finish" if doc.get("artwork_finish") else None,
		_label("Artwork Coating", doc.get("artwork_coating")),
		", ".join(u.upgrade for u in doc.get("artwork_upgrades") or [] if u.upgrade) or None,
	]
	line = " · ".join(escape_html(p) for p in parts if p)
	if not line:
		return ""
	notes = (doc.get("artwork_notes") or "").strip()
	if notes:
		line += "<br>" + escape_html(notes).replace("\n", "<br>")
	return line
