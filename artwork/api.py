"""Whitelisted endpoints: desk helpers and the guest customer portal."""
from __future__ import annotations

import mimetypes
import os

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, now_datetime

from artwork.processing import enqueue_component, file_path


# --------------------------------------------------------------------------- desk
@frappe.whitelist()
def proof_summary(proof: str):
	doc = frappe.get_doc("Artwork Proof", proof)
	doc.check_permission("read")
	rows = []
	for c in doc.components():
		warns = frappe.get_all("Artwork Preflight Warning", filters={"parent": c.name, "parenttype": "Artwork Component",
			"severity": ["in", ["Error", "Warning"]]}, fields=["severity", "message", "acknowledged"], order_by="idx asc")
		c["issues"] = warns
		rows.append(c)
	return rows


@frappe.whitelist()
def new_component(proof: str, item: str | None = None):
	"""Create a component on a proof, pre-filled from the item's artwork defaults."""
	doc = frappe.get_doc("Artwork Proof", proof)
	doc.check_permission("write")
	comp = frappe.new_doc("Artwork Component")
	comp.artwork_proof = proof
	comp.item = item
	return comp.as_dict()


@frappe.whitelist()
def item_defaults(item: str):
	meta = frappe.get_meta("Item")
	out = {}
	for f in ("default_artwork_template", "default_artwork_finish"):
		if meta.has_field(f):
			out[f] = frappe.db.get_value("Item", item, f)
	if out.get("default_artwork_template"):
		out["supplier"] = frappe.db.get_value("Artwork Supplier Template", out["default_artwork_template"], "supplier")
	return out


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


@frappe.whitelist(allow_guest=True, methods=["POST"])
def customer_upload(proof: str, key: str, component: str, target: str = "artwork", row: str | None = None):
	"""Customer uploads artwork (or an upgrade mask) for one component."""
	doc = get_portal_proof(proof, key)
	if doc.status in ("Approved", "Sent to Customer"):
		frappe.throw(_("Artwork can't be changed while a proof is awaiting approval or approved. "
			"Use 'Request changes' instead."))
	comp = frappe.get_doc("Artwork Component", component)
	if comp.artwork_proof != doc.name:
		raise frappe.PermissionError
	files = frappe.request.files
	if "file" not in files:
		frappe.throw(_("No file received."))
	up = files["file"]
	content = up.stream.read()
	fname = os.path.basename(up.filename or "artwork.pdf")
	ext = os.path.splitext(fname)[1].lower()
	if ext not in (".pdf", ".ai", ".eps", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".psd"):
		frappe.throw(_("Please upload a PDF, AI, EPS, TIFF, JPG or PNG file."))
	f = frappe.get_doc({
		"doctype": "File", "file_name": fname, "is_private": 1, "content": content,
		"attached_to_doctype": "Artwork Component", "attached_to_name": comp.name,
		"attached_to_field": "artwork_file" if target == "artwork" else "upgrades",
	})
	f.insert(ignore_permissions=True)
	if target == "artwork":
		comp.artwork_file = f.file_url
	else:
		r = next((u for u in comp.upgrades if u.name == row), None)
		if not r:
			frappe.throw(_("Unknown upgrade row."))
		r.mask_file = f.file_url
		r.source = "Separate file"
	comp.flags.ignore_permissions = True
	comp.save()
	doc.reload()
	doc.add_log("Customer upload", f"{comp.item}: {fname}", by=f"Customer ({_ip()})")
	doc.flags.ignore_permissions = True
	doc.save()
	frappe.db.commit()
	return {"file": fname}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def portal_file(proof: str, key: str, kind: str, component: str | None = None):
	"""Stream a private file to the customer: preview / proof / template / combined proof."""
	doc = get_portal_proof(proof, key)
	url = None
	if kind == "combined":
		url = doc.proof_pdf
	else:
		comp = frappe.get_doc("Artwork Component", component)
		if comp.artwork_proof != doc.name:
			raise frappe.PermissionError
		if kind == "preview":
			url = comp.preview_image
		elif kind == "proof":
			url = comp.proof_file
		elif kind == "template":
			url = frappe.db.get_value("Artwork Supplier Template", comp.supplier_template, "template_file")
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
	comps = []
	for c in doc.components():
		comp = frappe.get_doc("Artwork Component", c.name)
		tpl = frappe.get_cached_doc("Artwork Supplier Template", comp.supplier_template) if comp.supplier_template else None
		comps.append({
			"name": comp.name,
			"title": f"{comp.component_type or 'Artwork'} – {comp.item_name or comp.item}",
			"item": comp.item,
			"finish": comp.finish,
			"template": tpl.template_name if tpl else "",
			"spec": (f"Trim {tpl.trim_width_mm or '?'} × {tpl.trim_height_mm or '?'} mm · bleed {tpl.bleed_mm or 3} mm"
				+ (f" · spine {tpl.spine_width_mm} mm" if tpl and tpl.spine_width_mm else "")) if tpl else "",
			"has_artwork": bool(comp.artwork_file),
			"status": comp.processing_status,
			"has_preview": bool(comp.preview_image),
			"issues": [{"severity": w.severity, "message": escape_html(w.message)} for w in comp.warnings
				if w.severity in ("Error", "Warning")],
			"upgrades": [{"row": u.name, "upgrade": u.upgrade, "source": u.source, "has_file": bool(u.mask_file)}
				for u in comp.upgrades],
		})
	return doc, comps


@frappe.whitelist()
def reprocess(component: str):
	comp = frappe.get_doc("Artwork Component", component)
	comp.check_permission("write")
	enqueue_component(component)
	return "Queued"
