"""Bridge between Frappe documents and the framework-free PDF engine.

One background job per Artwork Proof works through its queued print-item rows
one at a time, so rows never overwrite each other.
"""
from __future__ import annotations

import json
import os
import shutil

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

INTENTS = {"Perceptual": 0, "Relative Colorimetric": 1, "Saturation": 2, "Absolute Colorimetric": 3}
OUTPUT_FIELDS = (
	"processing_status", "error_count", "warning_count", "approved", "print_file", "proof_file", "preview_image",
	"processed_on", "processing_error", "alignment_info", "ink_info", "input_signature",
)


def settings():
	return frappe.get_cached_doc("Artwork Settings")


def split_list(value) -> list[str]:
	if not value:
		return []
	return [v.strip() for v in str(value).replace("\n", ",").split(",") if v.strip()]


def parse_cmyk(value, default=(0, 0, 0, 0)):
	try:
		parts = [flt(v) / 100.0 for v in split_list(value)]
		return parts if len(parts) == 4 else list(default)
	except Exception:
		return list(default)


def file_path(file_url: str | None) -> str | None:
	if not file_url:
		return None
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if name:
		return frappe.get_doc("File", name).get_full_path()
	if file_url.startswith("/private/files/"):
		return frappe.get_site_path("private", "files", file_url.split("/private/files/", 1)[1])
	if file_url.startswith("/files/"):
		return frappe.get_site_path("public", "files", file_url.split("/files/", 1)[1])
	return None


# --------------------------------------------------------------------------- print items
def print_groups() -> list[str]:
	root = settings().print_item_group or "Print"
	if not frappe.db.exists("Item Group", root):
		return []
	from frappe.utils.nestedset import get_descendants_of

	return [root, *get_descendants_of("Item Group", root)]


def is_print_item(item: str) -> bool:
	groups = print_groups()
	return bool(groups) and frappe.db.get_value("Item", item, "item_group") in groups


def resolve_template(supplier, product_size, print_type) -> str | None:
	"""The enabled Supplier Template for this supplier + product size + print type."""
	if not (supplier and product_size and print_type):
		return None
	return frappe.db.get_value("Artwork Supplier Template", {"supplier": supplier, "product_size": product_size,
		"print_type": print_type, "enabled": 1}, "name")


def item_template(item_doc) -> str | None:
	return resolve_template(item_doc.get("artwork_supplier"), item_doc.get("artwork_product_size"),
		item_doc.get("artwork_print_type"))


def spec_problems(item_doc) -> list[str]:
	out = []
	missing = [label for field, label in (("artwork_supplier", _("Supplier")), ("artwork_product_size", _("Product Size")),
		("artwork_print_type", _("Print Type"))) if not item_doc.get(field)]
	if missing:
		out.append(_("no {0}").format(", ".join(missing)))
	elif not item_template(item_doc):
		out.append(_("no Supplier Template exists for {0} · {1} · {2} – create one").format(
			item_doc.artwork_supplier, item_doc.artwork_product_size, item_doc.artwork_print_type))
	if not item_doc.get("artwork_finish"):
		out.append(_("no Finish"))
	else:
		prof = frappe.db.get_value("Artwork Finish", item_doc.artwork_finish, "colour_profile")
		icc = frappe.db.get_value("Artwork Colour Profile", prof, "icc_file") if prof else None
		if not icc:
			out.append(_("colour profile {0} has no ICC file").format(prof or "?"))
	return out


def row_signature(proof, row) -> str:
	"""Short fingerprint of a row's inputs (file names can be long, so store a hash)."""
	import hashlib

	masks = sorted((m.upgrade, m.mask_file or "") for m in proof.get("masks") or [] if m.item == row.item)
	raw = json.dumps([row.item, row.artwork_file, cint(row.artwork_page) or 1, flt(row.spine_offset_mm), masks])
	return hashlib.sha1(raw.encode()).hexdigest()


# --------------------------------------------------------------------------- queueing
def enqueue_proof(name: str, now: bool = False):
	frappe.enqueue(
		"artwork.processing.run_proof",
		queue="long",
		timeout=3600,
		job_id=f"artwork-proof::{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=now or frappe.flags.in_test,
		name=name,
	)


def run_proof(name: str):
	"""Process every row marked Queued; loops so rows queued meanwhile are picked up too."""
	from artwork.artwork.doctype.artwork_proof.artwork_proof import refresh_proof_status

	for _round in range(20):
		rows = frappe.get_all("Artwork Proof Item", filters={"parent": name, "parenttype": "Artwork Proof",
			"processing_status": "Queued"}, pluck="name", order_by="idx asc")
		if not rows:
			break
		for row_name in rows:
			process_row(name, row_name)
	refresh_proof_status(name)
	frappe.db.commit()


def process_row(proof_name: str, row_name: str):
	proof = frappe.get_doc("Artwork Proof", proof_name)
	row = next((r for r in proof.items if r.name == row_name), None)
	if row is None:
		return
	frappe.db.set_value("Artwork Proof Item", row_name, "processing_status", "Processing", update_modified=False)
	frappe.db.commit()
	_notify(proof_name, row.item, "Processing")
	s = settings()
	work = frappe.get_site_path("private", "artwork_work", f"{proof_name}-{row_name}")
	shutil.rmtree(work, ignore_errors=True)
	os.makedirs(work, exist_ok=True)
	try:
		item = frappe.get_doc("Item", row.item)
		problems = spec_problems(item)
		if problems:
			_store_failure(proof_name, row_name, "Needs Setup",
				_("Set up the print spec on Item {0}: {1}.").format(row.item, ", ".join(problems)))
			return
		from artwork.engine import process_component

		job = build_job(proof, row, item, s, work)
		result = process_component(job)
		_store_result(proof_name, row_name, result, row_signature(proof, row))
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=f"Artwork processing failed: {proof_name} / {row.item}",
			reference_doctype="Artwork Proof", reference_name=proof_name)
		_store_failure(proof_name, row_name, "Failed", str(e) or e.__class__.__name__)
	finally:
		if not cint(s.keep_work_files):
			shutil.rmtree(work, ignore_errors=True)


def build_job(proof, row, item, s, work) -> dict:
	if not row.artwork_file:
		frappe.throw(_("No artwork file attached for {0}.").format(row.item))
	tpl = frappe.get_doc("Artwork Supplier Template", item_template(item))
	finish = frappe.get_doc("Artwork Finish", item.artwork_finish)
	profile = frappe.get_doc("Artwork Colour Profile", finish.colour_profile)
	icc = file_path(profile.icc_file)
	if not icc or not os.path.exists(icc):
		frappe.throw(_("ICC profile file for {0} is missing.").format(profile.name))
	tpath = file_path(tpl.template_file)
	if not tpath or not os.path.exists(tpath):
		frappe.throw(_("Template PDF for {0} is missing.").format(tpl.name))

	masks = {m.upgrade: m.mask_file for m in proof.get("masks") or [] if m.item == row.item}
	upgrades = []
	for u in item.get("artwork_upgrades") or []:
		up = frappe.get_cached_doc("Artwork Print Upgrade", u.upgrade)
		upgrades.append({
			"label": up.upgrade_name,
			"spot_name": up.spot_name,
			"aliases": split_list(up.aliases),
			"source": u.source or "Artwork spot",
			"file": file_path(masks.get(u.upgrade)),
			"page": cint(u.page) or None,
			"display_rgb": up.display_colour or "#29ABE2",
			"opacity": flt(up.preview_opacity or 55) / 100.0,
			"alt_cmyk": parse_cmyk(up.alternate_cmyk, (0.3, 0, 0, 0)),
		})
	known = [{"spot_name": u.spot_name, "aliases": split_list(u.aliases)}
		for u in frappe.get_all("Artwork Print Upgrade", fields=["spot_name", "aliases"])]
	logo = file_path(s.proof_logo) if s.proof_logo else None
	details = [
		("Proof", f"{proof.name}  rev {cint(proof.revision) or 1}"),
		("Job ticket", f"{proof.project}  {proof.project_name or ''}".strip()),
		("Customer", proof.customer or ""),
		("SKU", f"{item.name}  {item.item_name or ''}".strip()),
		("Supplier template", tpl.template_name),
		("Finish", finish.finish_name),
		("Upgrades", ", ".join(u["label"] for u in upgrades) or "None"),
	]
	return {
		"art_path": file_path(row.artwork_file),
		"art_page": cint(row.artwork_page) or 1,
		"work_dir": work,
		"out_basename": f"{proof.name}-{item.name}".replace("/", "-").replace(" ", "_"),
		"template": {
			"path": tpath,
			"page": cint(tpl.template_page) or 1,
			"spot_name": s.template_spot_name or "Template",
			"alt_cmyk": parse_cmyk(s.template_alt_cmyk, (0, 1, 0, 0)),
			"display_rgb": s.template_display_colour or "#E6007E",
			"no_spine": cint(tpl.get("no_spine")),
			"spine_centre_mm": flt(tpl.spine_centre_mm) or None,
			"spine_width_mm": flt(tpl.spine_width_mm) or None,
			"trim_width_mm": flt(tpl.trim_width_mm) or None,
			"trim_height_mm": flt(tpl.trim_height_mm) or None,
			"bleed_mm": flt(tpl.bleed_mm) if tpl.bleed_mm is not None else flt(s.default_bleed_mm or 3),
			"safe_margin_mm": flt(tpl.safe_margin_mm or s.safe_margin_mm or 3),
		},
		"template_aliases": split_list(s.template_aliases) or None,
		"colour": {
			"profile_path": icc,
			"identifier": profile.output_condition or profile.profile_name,
			"tac_limit": cint(profile.max_ink) or 300,
			"intent": INTENTS.get(profile.rendering_intent or "Relative Colorimetric", 1),
		},
		"upgrades": upgrades,
		"known_upgrades": known,
		"options": {
			"outline_fonts": cint(item.get("artwork_outline_fonts")),
			"keep_other_spots": 0,
			"spine_offset_mm": flt(row.spine_offset_mm),
			"auto_detect_art_spine": 1,
			"min_dpi": cint(s.min_dpi) or 300,
			"min_font_pt": flt(s.min_font_pt) or 5,
			"min_line_pt": flt(s.min_line_pt) or 0.25,
			"preview_dpi": cint(s.preview_dpi) or 110,
			"gs": s.ghostscript_path or None,
		},
		"proof": {
			"details": details,
			"brand": {"company": s.company_name or "Packaged Sounds", "colour": s.brand_colour or "#111111",
				"logo_path": logo},
		},
	}


def _attach(proof_name, path):
	if not path or not os.path.exists(path):
		return None
	with open(path, "rb") as fh:
		content = fh.read()
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": os.path.basename(path),
		"attached_to_doctype": "Artwork Proof",
		"attached_to_name": proof_name,
		"is_private": 1,
		"content": content,
	})
	f.flags.ignore_file_validate = True
	f.insert(ignore_permissions=True)
	return f.file_url


def _store_result(proof_name, row_name, result, signature):
	urls = {k: _attach(proof_name, result.get(k)) for k in ("print_pdf", "preview_png", "proof_pdf")}
	proof = frappe.get_doc("Artwork Proof", proof_name)
	row = next(r for r in proof.items if r.name == row_name)
	acked = {(w.item, w.code, w.message) for w in proof.warnings if w.acknowledged}
	keep = [w.as_dict() for w in proof.warnings if w.row_ref != row_name]
	proof.set("warnings", [])
	for w in keep:
		for k in ("name", "idx", "parent", "parenttype", "parentfield", "doctype", "creation", "modified"):
			w.pop(k, None)
		proof.append("warnings", w)
	errors = warnings = 0
	for w in result.get("warnings", []):
		proof.append("warnings", {
			"item": row.item, "row_ref": row_name, "severity": w["severity"], "code": w["code"],
			"message": w["message"], "acknowledged": 1 if (row.item, w["code"], w["message"]) in acked else 0,
		})
		errors += w["severity"] == "Error"
		warnings += w["severity"] == "Warning"
	info = result.get("info", {})
	al, tpl, ink = info.get("alignment", {}), info.get("template", {}), info.get("ink", {})
	row.alignment_info = (
		f"Art spine reference: {al.get('method', '?')}. Moved {al.get('offset_x_mm', 0):+.2f}mm horizontally, "
		f"{al.get('offset_y_mm', 0):+.2f}mm vertically. Template spine centre {tpl.get('spine_centre_mm')}mm "
		f"({tpl.get('spine_source')}), spine width {tpl.get('spine_width_mm')}mm, trim {tpl.get('trim_mm')}mm."
	)
	row.ink_info = (f"{ink.get('profile')} – max ink {ink.get('limit', 0):.0f}%. "
		f"Before: {ink.get('max_before', 0):.0f}%, after: {ink.get('max_after', 0):.0f}%.")
	row.print_file, row.preview_image, row.proof_file = urls["print_pdf"], urls["preview_png"], urls["proof_pdf"]
	row.error_count, row.warning_count = errors, warnings
	row.processing_status = "Processed"
	row.processing_error = None
	row.processed_on = now_datetime()
	row.input_signature = signature
	row.approved = 0
	proof.flags.from_processing = True
	proof.flags.ignore_permissions = True
	proof.save()
	frappe.db.commit()
	_notify(proof_name, row.item, "Processed")


def _store_failure(proof_name, row_name, status, message):
	frappe.db.set_value("Artwork Proof Item", row_name, {"processing_status": status,
		"processing_error": (message or "")[:1000], "processed_on": now_datetime()}, update_modified=False)
	frappe.db.set_value("Artwork Proof", proof_name, "modified", now_datetime(), update_modified=False)
	frappe.db.commit()
	_notify(proof_name, None, status)


def _notify(proof_name, item, status):
	frappe.publish_realtime("artwork_proof_progress", {"name": proof_name, "item": item, "status": status},
		doctype="Artwork Proof", docname=proof_name, after_commit=False)


# --------------------------------------------------------------------------- combined proof
def build_combined_proof(proof_name: str) -> str | None:
	import pymupdf

	proof = frappe.get_doc("Artwork Proof", proof_name)
	out = pymupdf.open()
	for r in proof.items:
		p = file_path(r.proof_file)
		if p and os.path.exists(p):
			with pymupdf.open(p) as src:
				out.insert_pdf(src)
	if not len(out):
		return None
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": f"{proof.name}-rev{cint(proof.revision) or 1}-proof.pdf",
		"attached_to_doctype": "Artwork Proof",
		"attached_to_name": proof.name,
		"attached_to_field": "proof_pdf",
		"is_private": 1,
		"content": out.tobytes(deflate=True, garbage=3),
	})
	f.flags.ignore_file_validate = True
	f.insert(ignore_permissions=True)
	frappe.db.set_value("Artwork Proof", proof.name, "proof_pdf", f.file_url)
	return f.file_url
