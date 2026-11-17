# Copyright (c) 2026, Packaged Sounds and contributors
"""Loads the standard supplier templates that ship with the app (artwork/standard_templates) as
Artwork Supplier Templates. Safe to run again: anything that already exists is left alone.

    bench --site <site> execute artwork.template_loader.load
"""
import json
import os

import frappe

# how to recognise each supplier among the site's existing Suppliers before creating a new one
SUPPLIER_MATCH = {
	"Takt": ["Takt", "Takt %", "% Takt", "% Takt %", "Takt-%"],
	"Rad": ["Rad", "Rad %", "RAD %", "Radprint%", "Rad Print%"],
	"Voit": ["Voit", "% Voit", "% Voit %", "Voit %"],
}


def templates_dir():
	return frappe.get_app_path("artwork", "standard_templates")


def find_or_create_supplier(key: str) -> str:
	for pattern in SUPPLIER_MATCH.get(key, [key]):
		for field in ("name", "supplier_name"):
			hit = frappe.db.get_value("Supplier", {field: ["like", pattern]}, "name")
			if hit:
				return hit
	doc = frappe.new_doc("Supplier")
	doc.supplier_name = key
	doc.supplier_type = "Company"
	used = frappe.db.sql("""select supplier_group from tabSupplier where ifnull(supplier_group, '') != ''
		group by supplier_group order by count(*) desc limit 1""")
	group = frappe.db.get_single_value("Buying Settings", "supplier_group") or (used[0][0] if used else None) \
		or frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	if group:
		doc.supplier_group = group
	doc.insert(ignore_permissions=True)
	return doc.name


def load(update_files: int = 0):
	"""Create the missing templates. update_files=1 also swaps in the bundled PDF on templates that already exist."""
	from artwork.install import seed_sizes_and_types

	seed_sizes_and_types()
	base = templates_dir()
	with open(os.path.join(base, "manifest.json"), encoding="utf-8") as f:
		rows = json.load(f)
	suppliers, made, skipped, failed = {}, [], [], []
	for r in rows:
		try:
			supplier = suppliers.get(r["supplier"]) or suppliers.setdefault(r["supplier"], find_or_create_supplier(r["supplier"]))
			variant = (r.get("variant") or "").strip() or None
			existing = [t for t in frappe.get_all("Artwork Supplier Template", filters={"supplier": supplier,
				"product_size": r["product_size"], "print_type": r["print_type"]}, fields=["name", "variant", "enabled"])
				if (t.variant or None) == variant]
			path = os.path.join(base, r["file"])
			if existing and not int(update_files or 0):
				skipped.append(existing[0].name)
				continue
			with open(path, "rb") as fh:
				content = fh.read()
			title = f"{r['supplier']} – {r['product_size']} {r['print_type']}" + (f" – {variant}" if variant else "")
			fname = title.replace('"', "in").replace("–", "-").replace("/", "-") + ".pdf"
			fdoc = frappe.get_doc({"doctype": "File", "file_name": fname, "is_private": 1, "content": content})
			fdoc.insert(ignore_permissions=True)
			if existing:
				doc = frappe.get_doc("Artwork Supplier Template", existing[0].name)
				doc.template_file = fdoc.file_url
			else:
				doc = frappe.get_doc({"doctype": "Artwork Supplier Template", "supplier": supplier,
					"product_size": r["product_size"], "print_type": r["print_type"], "variant": variant, "enabled": 1,
					"template_file": fdoc.file_url, "template_page": 1, "bleed_mm": r.get("bleed_mm") or 3,
					"safe_margin_mm": r.get("safe_margin_mm") or 5,
					"notes": f"Packaged Sounds standard template (from {r.get('source') or r['file']})."})
			doc.flags.ignore_permissions = True
			doc.save() if existing else doc.insert()
			frappe.db.set_value("File", fdoc.name, {"attached_to_doctype": "Artwork Supplier Template",
				"attached_to_name": doc.name, "attached_to_field": "template_file"})
			made.append(doc.name)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			failed.append(f"{r['supplier']} {r['product_size']} {r['print_type']} {r.get('variant') or ''}".strip())
			frappe.log_error(title=f"Standard template not loaded: {r['file']}")
	out = {"created_or_updated": len(made), "already_there": len(skipped), "failed": failed, "suppliers": suppliers}
	print(json.dumps(out, indent=1, ensure_ascii=False))
	return out
