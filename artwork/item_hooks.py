import frappe


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


def erpnext_supplier(doc):
	"""The supplier set in ERPNext's own fields (Item Defaults, then the Purchasing tab's Supplier Items)."""
	for d in doc.get("item_defaults") or []:
		if d.get("default_supplier"):
			return d.default_supplier
	for s in doc.get("supplier_items") or []:
		if s.get("supplier"):
			return s.supplier
	return None


def apply_print_spec(doc, meta=None):
	from artwork.processing import item_template

	meta = meta or frappe.get_meta("Item")
	if not doc.get("artwork_supplier"):
		doc.artwork_supplier = erpnext_supplier(doc)
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
	parts = [
		product,
		_label("Artwork Stock", doc.get("artwork_stock")),
		_label("Artwork Print Colour", doc.get("artwork_print_colours")),
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
