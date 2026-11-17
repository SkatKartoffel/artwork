"""Bridge between Frappe documents and the framework-free PDF engine."""
from __future__ import annotations

import json
import os
import shutil

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

INPUT_FIELDS = (
	"artwork_file", "artwork_page", "supplier_template", "finish", "outline_fonts",
	"keep_other_spots", "auto_detect_spine", "spine_offset_mm",
)
INTENTS = {"Perceptual": 0, "Relative Colorimetric": 1, "Saturation": 2, "Absolute Colorimetric": 3}


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
	# fall back to the conventional locations
	if file_url.startswith("/private/files/"):
		return frappe.get_site_path("private", "files", file_url.split("/private/files/", 1)[1])
	if file_url.startswith("/files/"):
		return frappe.get_site_path("public", "files", file_url.split("/files/", 1)[1])
	return None


def inputs_signature(doc) -> str:
	data = {f: doc.get(f) for f in INPUT_FIELDS}
	data["upgrades"] = [(u.upgrade, u.source, u.mask_file, u.page) for u in doc.get("upgrades") or []]
	return json.dumps(data, sort_keys=True, default=str)


# --------------------------------------------------------------------------- queueing
def enqueue_component(name: str, now: bool = False):
	frappe.db.set_value("Artwork Component", name, {"processing_status": "Queued", "processing_error": None})
	frappe.enqueue(
		"artwork.processing.run_component",
		queue="long",
		timeout=1800,
		job_id=f"artwork-component::{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=now or frappe.flags.in_test,
		name=name,
	)


def run_component(name: str):
	comp = frappe.get_doc("Artwork Component", name)
	frappe.db.set_value("Artwork Component", name, "processing_status", "Processing")
	frappe.db.commit()
	_notify(comp, "Processing")
	s = settings()
	work = frappe.get_site_path("private", "artwork_work", name)
	shutil.rmtree(work, ignore_errors=True)
	os.makedirs(work, exist_ok=True)
	try:
		from artwork.engine import process_component

		job = build_job(comp, s, work)
		result = process_component(job)
		_store_result(comp, result)
	except Exception as e:
		frappe.db.rollback()
		msg = str(e) or e.__class__.__name__
		frappe.db.set_value("Artwork Component", name,
			{"processing_status": "Failed", "processing_error": msg[:1000], "processed_on": now_datetime()})
		frappe.log_error(title=f"Artwork processing failed: {name}", reference_doctype="Artwork Component",
			reference_name=name)
		frappe.db.commit()
		_notify(comp, "Failed")
	finally:
		if not cint(s.keep_work_files):
			shutil.rmtree(work, ignore_errors=True)
	from artwork.artwork.doctype.artwork_proof.artwork_proof import refresh_proof_status

	refresh_proof_status(comp.artwork_proof)
	frappe.db.commit()


def build_job(comp, s, work) -> dict:
	if not comp.artwork_file:
		frappe.throw(_("No artwork file attached."))
	tpl = frappe.get_doc("Artwork Supplier Template", comp.supplier_template)
	finish = frappe.get_doc("Artwork Finish", comp.finish)
	profile = frappe.get_doc("Artwork Colour Profile", finish.colour_profile)
	icc = file_path(profile.icc_file)
	if not icc or not os.path.exists(icc):
		frappe.throw(_("ICC profile file for {0} is missing. Upload it on the Artwork Colour Profile.").format(profile.name))
	tpath = file_path(tpl.template_file)
	if not tpath or not os.path.exists(tpath):
		frappe.throw(_("Template PDF for {0} is missing.").format(tpl.name))

	upgrades = []
	for row in comp.get("upgrades") or []:
		up = frappe.get_cached_doc("Artwork Print Upgrade", row.upgrade)
		upgrades.append({
			"label": up.upgrade_name,
			"spot_name": up.spot_name,
			"aliases": split_list(up.aliases),
			"source": row.source or "Artwork spot",
			"file": file_path(row.mask_file),
			"page": cint(row.page) or None,
			"display_rgb": up.display_colour or "#29ABE2",
			"opacity": flt(up.preview_opacity or 55) / 100.0,
			"alt_cmyk": parse_cmyk(up.alternate_cmyk, (0.3, 0, 0, 0)),
		})
	known = [{"spot_name": u.spot_name, "aliases": split_list(u.aliases)}
		for u in frappe.get_all("Artwork Print Upgrade", fields=["spot_name", "aliases"])]

	proof = frappe.get_doc("Artwork Proof", comp.artwork_proof)
	logo = file_path(s.proof_logo) if s.proof_logo else None
	details = [
		("Proof", f"{proof.name}  rev {cint(proof.revision) or 1}"),
		("Job ticket", f"{proof.project}  {proof.project_name or ''}".strip()),
		("Customer", proof.customer or ""),
		("SKU", f"{comp.item}  {comp.item_name or ''}".strip()),
		("Component", comp.component_type or ""),
		("Supplier template", tpl.template_name),
		("Finish", finish.finish_name),
		("Upgrades", ", ".join(u["label"] for u in upgrades) or "None"),
	]
	return {
		"art_path": file_path(comp.artwork_file),
		"art_page": cint(comp.artwork_page) or 1,
		"work_dir": work,
		"out_basename": f"{comp.name}-{comp.item}".replace("/", "-").replace(" ", "_"),
		"template": {
			"path": tpath,
			"page": cint(tpl.template_page) or 1,
			"spot_name": s.template_spot_name or "Template",
			"alt_cmyk": parse_cmyk(s.template_alt_cmyk, (0, 1, 0, 0)),
			"display_rgb": s.template_display_colour or "#E6007E",
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
			"outline_fonts": cint(comp.outline_fonts),
			"keep_other_spots": cint(comp.keep_other_spots),
			"spine_offset_mm": flt(comp.spine_offset_mm),
			"auto_detect_art_spine": cint(comp.auto_detect_spine),
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


def _attach(comp, path, fieldname):
	if not path or not os.path.exists(path):
		return None
	with open(path, "rb") as fh:
		content = fh.read()
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": os.path.basename(path),
		"attached_to_doctype": "Artwork Component",
		"attached_to_name": comp.name,
		"attached_to_field": fieldname,
		"is_private": 1,
		"content": content,
	})
	f.flags.ignore_file_validate = True
	f.insert(ignore_permissions=True)
	return f.file_url


def _store_result(comp, result: dict):
	comp.reload()
	# remember acknowledgements so re-processing the same issue doesn't nag again
	acked = {(w.code, w.message) for w in comp.warnings if w.acknowledged}
	comp.set("warnings", [])
	errors = warnings = 0
	for w in result.get("warnings", []):
		comp.append("warnings", {
			"severity": w["severity"], "code": w["code"], "message": w["message"],
			"acknowledged": 1 if (w["code"], w["message"]) in acked else 0,
		})
		errors += w["severity"] == "Error"
		warnings += w["severity"] == "Warning"
	info = result.get("info", {})
	al = info.get("alignment", {})
	tpl = info.get("template", {})
	ink = info.get("ink", {})
	comp.alignment_info = (
		f"Art spine reference: {al.get('method', '?')}. Moved {al.get('offset_x_mm', 0):+.2f}mm horizontally, "
		f"{al.get('offset_y_mm', 0):+.2f}mm vertically.\n"
		f"Template spine centre {tpl.get('spine_centre_mm')}mm ({tpl.get('spine_source')}), "
		f"spine width {tpl.get('spine_width_mm')}mm, trim {tpl.get('trim_mm')}mm."
	)
	comp.ink_info = (
		f"{ink.get('profile')} – max ink {ink.get('limit', 0):.0f}%. "
		f"Before: {ink.get('max_before', 0):.0f}%, after: {ink.get('max_after', 0):.0f}%."
	)
	comp.print_file = _attach(comp, result.get("print_pdf"), "print_file")
	comp.preview_image = _attach(comp, result.get("preview_png"), "preview_image")
	comp.proof_file = _attach(comp, result.get("proof_pdf"), "proof_file")
	comp.error_count = errors
	comp.warning_count = warnings
	comp.processing_status = "Processed"
	comp.processing_error = None
	comp.processed_on = now_datetime()
	comp.report_json = json.dumps(info, indent=1, default=str)[:60000]
	comp.flags.from_processing = True
	comp.save(ignore_permissions=True)
	frappe.db.commit()
	_notify(comp, "Processed")


def _notify(comp, status):
	frappe.publish_realtime("artwork_component_status", {"name": comp.name, "status": status,
		"artwork_proof": comp.artwork_proof}, doctype="Artwork Component", docname=comp.name, after_commit=False)
	frappe.publish_realtime("artwork_component_status", {"name": comp.name, "status": status,
		"artwork_proof": comp.artwork_proof}, doctype="Artwork Proof", docname=comp.artwork_proof, after_commit=False)


# --------------------------------------------------------------------------- combined proof
def build_combined_proof(proof_name: str) -> str | None:
	import pymupdf

	comps = frappe.get_all("Artwork Component", filters={"artwork_proof": proof_name},
		fields=["name", "proof_file"], order_by="creation asc")
	out = pymupdf.open()
	for c in comps:
		p = file_path(c.proof_file)
		if p and os.path.exists(p):
			with pymupdf.open(p) as src:
				out.insert_pdf(src)
	if not len(out):
		return None
	proof = frappe.get_doc("Artwork Proof", proof_name)
	content = out.tobytes(deflate=True, garbage=3)
	f = frappe.get_doc({
		"doctype": "File",
		"file_name": f"{proof.name}-rev{cint(proof.revision) or 1}-proof.pdf",
		"attached_to_doctype": "Artwork Proof",
		"attached_to_name": proof.name,
		"attached_to_field": "proof_pdf",
		"is_private": 1,
		"content": content,
	})
	f.flags.ignore_file_validate = True
	f.insert(ignore_permissions=True)
	frappe.db.set_value("Artwork Proof", proof.name, "proof_pdf", f.file_url)
	return f.file_url
