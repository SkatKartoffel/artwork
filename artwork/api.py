"""Whitelisted endpoints: desk helpers and the guest customer portal."""
from __future__ import annotations

import mimetypes
import os

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, now_datetime

from artwork.processing import file_path


# --------------------------------------------------------------------------- desk
@frappe.whitelist()
def item_proofs(item: str):
	"""Artwork Proofs that include this print item (for the Item form)."""
	frappe.has_permission("Artwork Proof", "read", throw=True)
	parents = frappe.get_all("Artwork Proof Item", filters={"item": item, "parenttype": "Artwork Proof"},
		pluck="parent")
	if not parents:
		return []
	return frappe.get_all("Artwork Proof", filters={"name": ["in", parents]},
		fields=["name", "title", "status", "revision", "project", "modified"], order_by="modified desc", limit=50)


# --------------------------------------------------------------------------- portal auth
def get_portal_proof(proof: str, key: str):
	if not proof or not key:
		raise frappe.PermissionError(_("Invalid link"))
	doc = frappe.get_doc("Artwork Proof", proof)
	if not doc.access_key or len(key) != len(doc.access_key) or not _consteq(key, doc.access_key):
		raise frappe.PermissionError(_("Invalid link"))
	if doc.link_expires and get_datetime(doc.link_expires) < now_datetime():
		raise frappe.PermissionError(_("This link has expired. Please ask us for a new one."))
	if doc.status == "Cancelled":
		raise frappe.PermissionError(_("This proof has been cancelled."))
	return doc


def _consteq(a: str, b: str) -> bool:
	import hmac

	return hmac.compare_digest(a.encode(), b.encode())


def _ip():
	try:
		return frappe.local.request_ip
	except Exception:
		return ""


# --------------------------------------------------------------------------- portal endpoints
@frappe.whitelist(allow_guest=True, methods=["POST"])
def customer_respond(proof: str, key: str, action: str, name: str, comments: str | None = None, confirm=0):
	doc = get_portal_proof(proof, key)
	name = (name or "").strip()
	if not name:
		frappe.throw(_("Please enter your name."))
	if doc.status != "Sent to Customer":
		frappe.throw(_("This proof is not awaiting approval (status: {0}).").format(doc.status))
	tag = f"{name} ({_ip()})"
	if action == "approve":
		if not cint(confirm):
			frappe.throw(_("Please tick the box to confirm you have checked the proof."))
		doc.customer_approve(tag, comments)
		frappe.db.commit()
		return {"status": "Approved"}
	if action == "changes":
		if not (comments or "").strip():
			frappe.throw(_("Please tell us what needs changing."))
		doc.customer_request_changes(tag, comments)
		frappe.db.commit()
		return {"status": "Changes Requested"}
	frappe.throw(_("Unknown action"))


ALLOWED_EXT = (".pdf", ".ai", ".eps", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".psd")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def customer_upload(proof: str, key: str, row: str, target: str = "artwork"):
	"""Customer uploads artwork (target=artwork, row=print item row) or a finish mask (target=mask, row=mask row)."""
	doc = get_portal_proof(proof, key)
	if doc.status in ("Approved", "Sent to Customer"):
		frappe.throw(_("Artwork can't be changed while a proof is awaiting approval or approved. "
			"Use 'Request changes' instead."))
	table = doc.items if target == "artwork" else doc.masks
	r = next((x for x in table if x.name == row), None)
	if r is None:
		raise frappe.PermissionError
	files = frappe.request.files
	if "file" not in files:
		frappe.throw(_("No file received."))
	up = files["file"]
	fname = os.path.basename(up.filename or "artwork.pdf")
	if os.path.splitext(fname)[1].lower() not in ALLOWED_EXT:
		frappe.throw(_("Please upload a PDF, AI, EPS, TIFF, JPG or PNG file."))
	f = frappe.get_doc({
		"doctype": "File", "file_name": fname, "is_private": 1, "content": up.stream.read(),
		"attached_to_doctype": "Artwork Proof", "attached_to_name": doc.name,
	})
	f.insert(ignore_permissions=True)
	if target == "artwork":
		r.artwork_file = f.file_url
	else:
		r.mask_file = f.file_url
	doc.add_log("Customer upload", f"{r.item}: {fname}", by=f"Customer ({_ip()})")
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	return {"file": fname}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def portal_file(proof: str, key: str, kind: str, row: str | None = None):
	"""Stream a private file to the customer: preview / proof / template / combined proof."""
	doc = get_portal_proof(proof, key)
	url = None
	if kind == "combined":
		url = doc.proof_pdf
	else:
		r = next((x for x in doc.items if x.name == row), None)
		if r is None:
			raise frappe.PermissionError
		if kind == "preview":
			url = r.preview_image
		elif kind == "proof":
			url = r.proof_file
		elif kind == "template":
			url = frappe.db.get_value("Artwork Supplier Template", r.supplier_template, "template_file") \
				if r.supplier_template else None
	path = file_path(url)
	if not path or not os.path.exists(path):
		raise frappe.DoesNotExistError(_("File not available"))
	with open(path, "rb") as fh:
		frappe.local.response.filecontent = fh.read()
	frappe.local.response.filename = os.path.basename(path)
	frappe.local.response.type = "download"
	frappe.local.response.content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
	if kind in ("preview", "proof", "combined"):
		frappe.local.response.display_content_as = "inline"


def portal_context(proof: str, key: str):
	"""Shared context for the two portal pages."""
	doc = get_portal_proof(proof, key)
	rows = []
	for r in doc.items:
		tpl = frappe.get_cached_doc("Artwork Supplier Template", r.supplier_template) if r.supplier_template else None
		rows.append({
			"row": r.name,
			"title": r.item_name or r.item,
			"item": r.item,
			"finish": r.finish or "",
			"template": tpl.template_name if tpl else "",
			"product": f"{tpl.product_size} {tpl.print_type}" if tpl else "",
			"spec": (f"Trim {tpl.trim_width_mm or '?'} × {tpl.trim_height_mm or '?'} mm · bleed {tpl.bleed_mm or 3} mm"
				+ (f" · spine {tpl.spine_width_mm} mm" if tpl.spine_width_mm else "")) if tpl else "",
			"has_template": bool(tpl and tpl.template_file),
			"has_artwork": bool(r.artwork_file),
			"status": r.processing_status,
			"has_preview": bool(r.preview_image),
			"issues": [{"severity": w.severity, "message": escape_html(w.message)} for w in doc.warnings
				if w.row_ref == r.name and w.severity in ("Error", "Warning")],
			"upgrades": [u.upgrade for u in frappe.get_all("Artwork Item Upgrade",
				filters={"parent": r.item, "parenttype": "Item"}, fields=["upgrade"], order_by="idx asc")],
			"masks": [{"row": m.name, "upgrade": m.upgrade, "has_file": bool(m.mask_file)}
				for m in doc.masks if m.item == r.item],
		})
	return doc, rows


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def print_item_query(doctype, txt, searchfield, start, page_len, filters):
	"""Item search limited to the Print item group (and its sub-groups)."""
	from artwork.processing import print_groups

	groups = print_groups()
	conditions = {"disabled": 0}
	if groups:
		conditions["item_group"] = ["in", groups]
	return frappe.get_all("Item", filters=conditions,
		or_filters={"name": ["like", f"%{txt}%"], "item_name": ["like", f"%{txt}%"]},
		fields=["name", "item_name", "item_group"], start=start, page_length=page_len, order_by="name asc",
		as_list=True)


@frappe.whitelist()
def is_print_group(item_group: str) -> bool:
	from artwork.processing import print_groups

	return item_group in print_groups()


@frappe.whitelist()
def template_summary(template: str):
	frappe.has_permission("Artwork Supplier Template", "read", throw=True)
	t = frappe.db.get_value("Artwork Supplier Template", template,
		["supplier", "product_size", "print_type", "packing_component", "no_spine"], as_dict=True) or {}
	return t


@frappe.whitelist()
def print_spec_preview(doc):
	"""Live preview on the Item form: matched template, packing component and description."""
	import json

	from artwork.item_hooks import apply_print_spec

	frappe.has_permission("Item", "read", throw=True)
	d = frappe.get_doc(json.loads(doc) if isinstance(doc, str) else doc)
	apply_print_spec(d)
	from artwork.processing import print_supplier, template_variants

	variants = [v.variant or "" for v in template_variants(print_supplier(d), d.get("artwork_product_size"),
		d.get("artwork_print_type"))]
	return {"artwork_template": d.artwork_template, "packing_component": d.get("packing_component"),
		"description": d.description, "variants": variants, "artwork_supplier": d.get("artwork_supplier")}
