# Copyright (c) 2026, Packaged Sounds and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, escape_html, flt, get_url, now_datetime, nowdate

from artwork.processing import (
	OUTPUT_FIELDS,
	enqueue_proof,
	is_print_item,
	item_template,
	print_groups,
	print_supplier,
	row_signature,
	settings,
)

LOCKED_STATUSES = ("Approved", "Cancelled")
PROJECT_LABEL = {
	"Draft": "Awaiting Artwork",
	"Awaiting Artwork": "Awaiting Artwork",
	"Processing": "In Proofing",
	"Needs Attention": "In Proofing",
	"Ready for Review": "In Proofing",
	"Sent to Customer": "Sent to Customer",
	"Changes Requested": "Changes Requested",
	"Approved": "Approved",
}


class ArtworkProof(Document):
	def before_insert(self):
		# a new proof starts with the house default (Artwork Settings); staff can change it per proof
		if not self.flags.get("outline_fonts_set"):
			self.outline_fonts = cint(settings().outline_fonts_default)

	def validate(self):
		if not self.title:
			self.title = " – ".join(filter(None, [self.project_name or self.project, self.customer]))
		if not self.contact_email and self.customer:
			self.contact_email = frappe.db.get_value("Customer", self.customer, "email_id")
		if self.is_new() and not self.items:
			self._pull_print_items()
		self.validate_items()
		self.sync_masks()
		if not self.flags.from_processing:
			if not self.is_new() and not self.flags.server_action:
				self.protect_outputs()
			self.validate_locked()
			self.queue_changed_rows()
		self.status = computed_status(self)

	def on_update(self):
		if self.flags.queue_rows:
			enqueue_proof(self.name)
		sync_project_status(self.project)

	def on_trash(self):
		if self.status == "Approved":
			frappe.throw(_("An approved proof can't be deleted. Cancel it instead."))

	def after_delete(self):
		sync_project_status(self.project)

	# ------------------------------------------------------------------ rows
	def validate_items(self):
		seen = set()
		groups = print_groups()
		for row in self.items:
			if row.item in seen:
				frappe.throw(_("Row {0}: {1} is listed twice.").format(row.idx, row.item))
			seen.add(row.item)
			if groups and not is_print_item(row.item):
				frappe.throw(_("Row {0}: {1} is not in the {2} item group.").format(
					row.idx, row.item, settings().print_item_group or "Print"))
			item = frappe.get_cached_doc("Item", row.item)
			row.supplier_template = item_template(item)
			row.finish = item.get("artwork_finish")

	def sync_masks(self):
		"""One mask row per print item whose upgrade is supplied as a separate file."""
		wanted = set()
		for row in self.items:
			for u in frappe.get_all("Artwork Item Upgrade", filters={"parent": row.item, "parenttype": "Item",
					"source": "Separate file"}, pluck="upgrade"):
				wanted.add((row.item, u))
		have = {(m.item, m.upgrade) for m in self.masks}
		self.set("masks", [m for m in self.masks if (m.item, m.upgrade) in wanted])
		for item, up in sorted(wanted - have):
			self.append("masks", {"item": item, "upgrade": up})

	def protect_outputs(self):
		"""Processing results come from the server only; a stale browser form can't overwrite them."""
		db_rows = {r.name: r for r in frappe.get_all("Artwork Proof Item", filters={"parent": self.name,
			"parenttype": self.doctype}, fields=["name", *OUTPUT_FIELDS])}
		for row in self.items:
			if row.name in db_rows:
				for f in OUTPUT_FIELDS:
					row.set(f, db_rows[row.name].get(f))
		acks = {w.name: w.acknowledged for w in self.warnings if w.name}
		live_rows = {r.name for r in self.items}
		db_warn = frappe.get_all("Artwork Preflight Warning", filters={"parent": self.name, "parenttype": self.doctype},
			fields=["name", "item", "severity", "code", "message", "acknowledged", "row_ref"], order_by="idx asc")
		self.set("warnings", [])
		for w in db_warn:
			if w.row_ref and w.row_ref not in live_rows:
				continue
			self.append("warnings", {
				"item": w.item, "severity": w.severity, "code": w.code, "message": w.message, "row_ref": w.row_ref,
				"acknowledged": acks.get(w.name, w.acknowledged),
			})

	def validate_locked(self):
		if self.is_new() or self.status not in LOCKED_STATUSES:
			return
		before = self.get_doc_before_save()
		if before is None:
			return
		old = {r.item: row_signature(before, r) for r in before.items}
		new = {r.item: row_signature(self, r) for r in self.items}
		if old != new:
			frappe.throw(_("This proof is {0}. Use New Revision before changing items or artwork.").format(self.status))

	def queue_changed_rows(self):
		auto = cint(settings().auto_process)
		for row in self.items:
			sig = row_signature(self, row)
			if not row.artwork_file:
				if row.processing_status not in ("Needs Setup",):
					row.processing_status = "Not Processed"
				continue
			if sig != (row.input_signature or "") and row.processing_status not in ("Queued", "Processing"):
				row.approved = 0
				if auto:
					self._queue(row, sig)
				else:
					row.processing_status = "Not Processed"

	def _job_running(self) -> bool:
		"""Is this proof's background job queued or running right now?"""
		try:
			from frappe.utils.background_jobs import is_job_enqueued

			return bool(is_job_enqueued(f"artwork-proof::{self.name}"))
		except Exception:
			return True

	def _queue(self, row, sig=None):
		row.input_signature = sig or row_signature(self, row)
		row.processing_status = "Queued"
		row.processing_error = None
		self.flags.queue_rows = True

	# ------------------------------------------------------------------ print items from the Sales Order
	def _pull_print_items(self):
		existing = {r.item for r in self.items}
		added = []
		for code in find_print_items(self):
			if code not in existing:
				self.append("items", {"item": code})
				existing.add(code)
				added.append(code)
		return added

	@frappe.whitelist()
	def get_print_items(self):
		self._fresh()
		self._check_unlocked()
		added = self._pull_print_items()
		self.save()
		return added

	# ------------------------------------------------------------------ helpers
	def add_log(self, action, comment=None, by=None):
		self.append("log", {
			"revision": cint(self.revision),
			"action": action,
			"logged_by": by or frappe.session.user,
			"logged_on": now_datetime(),
			"comment": comment,
		})

	def ensure_access_key(self):
		if not self.access_key:
			self.access_key = frappe.generate_hash(length=32)
		days = cint(frappe.db.get_single_value("Artwork Settings", "link_expiry_days")) or 30
		self.link_expires = add_days(now_datetime(), days)

	def portal_url(self, page="artwork_approval"):
		return get_url(f"/{page}?proof={self.name}&key={self.access_key}")

	def open_errors(self):
		return [w for w in self.warnings if w.severity == "Error" and not w.acknowledged]

	# ------------------------------------------------------------------ actions
	@frappe.whitelist()
	def process(self, row=None):
		"""(Re)process one row, or every row that has artwork."""
		self._fresh()
		self._check_unlocked()
		n = 0
		running = self._job_running()
		for r in self.items:
			# a row still marked Queued / Processing with no job behind it was cut off (worker restarted or
			# killed) and would otherwise be stuck for good - let it be queued again
			if (row and r.name != row) or not r.artwork_file or (
					r.processing_status in ("Queued", "Processing") and running):
				continue
			self._queue(r)
			n += 1
		if not n:
			frappe.throw(_("Nothing to process – attach customer artwork first."))
		self.save()
		return n

	@frappe.whitelist()
	def request_artwork(self, recipients=None, message=None):
		self._fresh()
		self._check_unlocked()
		recipients = recipients or self.contact_email
		if not recipients:
			frappe.throw(_("Set the customer email first."))
		self.ensure_access_key()
		url = self.portal_url("artwork_upload")
		rows = "".join(f"<li>{escape_html(r.item)} – {escape_html(r.item_name or '')}</li>" for r in self.items)
		body = (message or settings().upload_email_intro or "") + (
			f"<p><b>Job:</b> {escape_html(self.project_name or self.project)}</p><ul>{rows}</ul>"
			f'<p><a href="{url}" style="background:#111;color:#fff;padding:10px 18px;text-decoration:none;'
			f'border-radius:4px;display:inline-block">Upload artwork</a></p>'
			f"<p style='color:#777;font-size:12px'>Link valid until {frappe.utils.format_datetime(self.link_expires)}</p>"
		)
		frappe.sendmail(recipients=recipients, cc=self.cc_emails, subject=_("Artwork needed: {0}").format(self.title or self.name),
			message=body, reference_doctype=self.doctype, reference_name=self.name)
		self.add_log("Artwork requested", recipients)
		self.save()
		return url

	@frappe.whitelist()
	def send_to_customer(self, recipients=None, message=None, no_email=0):
		"""Open the proof for customer approval and email them the link. With no address (or no outgoing
		email account on the site) it is still opened: staff copy the approval link and send it themselves."""
		self._fresh()
		from artwork.processing import build_combined_proof, file_path

		self._check_unlocked()
		if not self.items:
			frappe.throw(_("Add at least one print item."))
		not_ready = [r.item for r in self.items if r.processing_status != "Processed"]
		if not_ready:
			frappe.throw(_("These items are not processed yet: {0}").format(", ".join(not_ready)))
		if cint(settings().block_send_on_errors) and self.open_errors():
			lines = "".join(f"<li><b>{escape_html(w.item)}</b>: {escape_html(w.message)}</li>" for w in self.open_errors()[:10])
			frappe.throw(_("Preflight errors must be fixed or ticked as OK'd before sending:") + f"<ul>{lines}</ul>",
				title=_("Unresolved preflight errors"))
		recipients = (recipients or "").strip()
		no_email = cint(no_email) or not recipients       # no address: open it, staff send the link themselves

		self.revision = cint(self.revision) + 1
		self.ensure_access_key()
		for r in self.items:
			r.approved = 0
		self.save()
		proof_url = build_combined_proof(self.name)
		self.reload()
		url = self.portal_url()
		body = (message or settings().proof_email_intro or "") + (
			f"<p><b>Job:</b> {escape_html(self.project_name or self.project)} &nbsp; <b>Proof:</b> {self.name} "
			f"rev {self.revision}</p>"
			f'<p><a href="{url}" style="background:#111;color:#fff;padding:10px 18px;text-decoration:none;'
			f'border-radius:4px;display:inline-block">Review &amp; approve</a></p>'
			f"<p style='color:#777;font-size:12px'>Link valid until {frappe.utils.format_datetime(self.link_expires)}</p>"
		)
		attachments = []
		p = file_path(proof_url)
		if p:
			with open(p, "rb") as fh:
				attachments.append({"fname": f"{self.name}-rev{self.revision}-proof.pdf", "fcontent": fh.read()})
		emailed = False
		if not no_email:
			try:
				frappe.sendmail(recipients=recipients, cc=self.cc_emails,
					subject=_("Artwork proof for approval: {0} (rev {1})").format(self.title or self.name, self.revision),
					message=body, attachments=attachments, reference_doctype=self.doctype, reference_name=self.name)
				emailed = True
			except frappe.OutgoingEmailError:
				# no outgoing email account on this site: still open the proof, staff send the link themselves
				frappe.clear_messages()
		self.status = "Sent to Customer"
		self.sent_on = now_datetime()
		self.add_log("Sent to customer" if emailed else "Opened for approval (link not emailed)", recipients if emailed else "")
		self.flags.status_set = True
		self.save()
		return {"url": url, "emailed": emailed}

	@frappe.whitelist()
	def new_revision(self):
		self._fresh()
		if self.status not in ("Approved", "Sent to Customer", "Changes Requested"):
			frappe.throw(_("Only sent or approved proofs need a new revision."))
		was = self.status
		self.status = "Draft"
		for r in self.items:
			r.approved = 0
		self.add_log("Reopened", _("Reopened from {0}").format(was))
		self.save()

	@frappe.whitelist()
	def copy_customer_link(self, page="artwork_approval"):
		self._fresh()
		if page not in ("artwork_approval", "artwork_upload"):
			frappe.throw(_("Unknown page"))
		if not self.access_key or not self.link_expires or str(self.link_expires) < str(now_datetime()):
			self.ensure_access_key()
			self.save()
		return self.portal_url(page)

	@frappe.whitelist()
	def cancel_proof(self):
		self._fresh()
		self.status = "Cancelled"
		self.add_log("Cancelled")
		self.save()

	# ------------------------------------------------------------------ purchase orders
	@frappe.whitelist()
	def po_suppliers(self):
		"""Print suppliers on this proof, their items and any existing Purchase Orders."""
		out = {}
		for r in self.items:
			sup = print_supplier(frappe.get_cached_doc("Item", r.item))
			if not sup:
				continue
			out.setdefault(sup, {"supplier": sup, "items": [], "existing": []})["items"].append(r.item)
		for sup, d in out.items():
			d["existing"] = frappe.get_all("Purchase Order", filters={"artwork_proof": self.name, "supplier": sup,
				"docstatus": ["<", 2]}, pluck="name")
		missing = [r.item for r in self.items if not print_supplier(frappe.get_cached_doc("Item", r.item))]
		return {"suppliers": list(out.values()), "no_supplier": missing}

	@frappe.whitelist()
	def make_purchase_order(self, supplier):
		"""An unsaved Purchase Order for this supplier's print items, with the Sales Order quantities.
		Prices: staff type them in, or use Get Supplier Prices on the saved draft (Gizmos, from the supplier's price list)."""
		frappe.has_permission("Purchase Order", "create", throw=True)
		items = [r for r in self.items if print_supplier(frappe.get_cached_doc("Item", r.item)) == supplier]
		if not items:
			frappe.throw(_("No print items on this proof use supplier {0}.").format(supplier))
		company = (frappe.db.get_value("Project", self.project, "company") if self.project else None) \
			or frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
		schedule = (frappe.db.get_value("Sales Order", self.sales_order, "delivery_date") if self.sales_order else None) \
			or frappe.utils.add_days(nowdate(), 14)
		po = frappe.new_doc("Purchase Order")
		po.supplier = supplier
		po.company = company
		po.project = self.project
		po.artwork_proof = self.name
		po.transaction_date = nowdate()
		po.schedule_date = schedule
		so_qty = {}
		if self.sales_order:   # order what the customer ordered; staff can still change it
			for d in frappe.get_all("Sales Order Item", filters={"parent": self.sales_order}, fields=["item_code", "qty"]):
				so_qty[d.item_code] = so_qty.get(d.item_code, 0) + flt(d.qty)
		for r in items:
			it = frappe.db.get_value("Item", r.item, ["item_name", "description", "stock_uom"], as_dict=True) or {}
			po.append("items", {
				"item_code": r.item,
				"item_name": it.get("item_name"),
				"description": it.get("description") or it.get("item_name"),
				"uom": it.get("stock_uom"),
				"stock_uom": it.get("stock_uom"),
				"conversion_factor": 1,
				"schedule_date": schedule,
				"project": self.project,
				"sales_order": self.sales_order,
				"qty": so_qty.get(r.item, 0),
				"rate": 0,
			})
		try:  # let ERPNext fill currency, price list, warehouse etc. where it can
			po.set_missing_values()
			for d in po.items:
				d.qty = so_qty.get(d.item_code, 0)
				d.rate = 0
		except Exception:
			frappe.clear_messages()
		return po.as_dict()

	def attach_print_files_to_pos(self):
		"""Attach each supplier's approved print-ready PDFs to their Purchase Order(s)."""
		pos = frappe.get_all("Purchase Order", filters={"artwork_proof": self.name, "docstatus": ["<", 2]},
			fields=["name", "supplier"])
		for po in pos:
			attach_print_files(po.name, po.supplier, self)

	# ------------------------------------------------------------------ customer responses
	def customer_approve(self, by_name, comment=None):
		self.status = "Approved"
		self.responded_on = now_datetime()
		self.responded_by = by_name
		self.customer_comments = comment
		self.approved_revision = self.revision
		for r in self.items:
			r.approved = 1
		self.add_log("Approved by customer", comment, by=by_name)
		self.flags.ignore_permissions = True
		self.flags.server_action = True
		self.save()
		if frappe.get_meta("Item").has_field("approved_artwork"):
			for r in self.items:
				frappe.db.set_value("Item", r.item, {
					"approved_artwork": r.print_file,
					"approved_artwork_proof": self.name,
					"artwork_approved_on": nowdate(),
				}, update_modified=False)
		self.attach_print_files_to_pos()
		self._timeline(_("Artwork proof {0} rev {1} approved by {2}").format(self.name, self.revision, by_name))
		self._notify_staff(_("Artwork approved: {0}").format(self.title or self.name), comment)
		for fn in frappe.get_hooks("artwork_on_approve"):
			frappe.get_attr(fn)(self)

	def customer_request_changes(self, by_name, comment):
		self.status = "Changes Requested"
		self.responded_on = now_datetime()
		self.responded_by = by_name
		self.customer_comments = comment
		self.add_log("Changes requested", comment, by=by_name)
		self.flags.ignore_permissions = True
		self.flags.server_action = True
		self.flags.status_set = True
		self.save()
		self._timeline(_("Changes requested on artwork proof {0} rev {1}: {2}").format(self.name, self.revision, comment))
		self._notify_staff(_("Artwork changes requested: {0}").format(self.title or self.name), comment)
		for fn in frappe.get_hooks("artwork_on_changes_requested"):
			frappe.get_attr(fn)(self)

	def _timeline(self, text):
		if self.project and frappe.db.exists("Project", self.project):
			frappe.get_doc("Project", self.project).add_comment("Info", text)

	def _notify_staff(self, subject, comment=None):
		users = {self.owner, self.modified_by} - {"Guest", "Administrator", None}
		for u in users:
			frappe.get_doc({"doctype": "Notification Log", "subject": subject, "for_user": u, "type": "Alert",
				"document_type": self.doctype, "document_name": self.name,
				"email_content": escape_html(comment or "")}).insert(ignore_permissions=True)

	def _fresh(self):
		"""Actions work on the saved record, not whatever the browser sent."""
		if not self.is_new():
			self.reload()
		self.flags.server_action = True

	def _check_unlocked(self):
		if self.status in LOCKED_STATUSES:
			frappe.throw(_("Proof is {0}. Use New Revision to make changes.").format(self.status))


# ---------------------------------------------------------------------- purchase orders
def attach_print_files(po_name, supplier, proof):
	"""Copy approved print PDFs for this supplier's items onto the PO (skips ones already there)."""
	from artwork.processing import file_path

	existing = set(frappe.get_all("File", filters={"attached_to_doctype": "Purchase Order",
		"attached_to_name": po_name}, pluck="file_name"))
	for r in proof.items:
		if not r.print_file or print_supplier(frappe.get_cached_doc("Item", r.item)) != supplier:
			continue
		path = file_path(r.print_file)
		fname = f"{r.item}-print-ready-rev{cint(proof.revision) or 1}.pdf".replace("/", "-")
		if not path or fname in existing:
			continue
		with open(path, "rb") as fh:
			f = frappe.get_doc({"doctype": "File", "file_name": fname, "attached_to_doctype": "Purchase Order",
				"attached_to_name": po_name, "is_private": 1, "content": fh.read()})
		f.flags.ignore_file_validate = True
		f.insert(ignore_permissions=True)


def purchase_order_after_insert(doc, method=None):
	"""A PO raised from an already-approved proof gets the print files straight away."""
	if not doc.get("artwork_proof"):
		return
	proof = frappe.get_doc("Artwork Proof", doc.artwork_proof)
	if proof.status == "Approved":
		attach_print_files(doc.name, doc.supplier, proof)
	proof.add_comment("Info", _("Purchase Order {0} created for {1}").format(doc.name, doc.supplier))


# ---------------------------------------------------------------------- print items
def find_print_items(proof) -> list[str]:
	"""Print items on the job's Sales Order(s), plus print parts inside their default BOMs."""
	orders = set(filter(None, [proof.sales_order]))
	if proof.project:
		orders |= set(frappe.get_all("Sales Order", filters={"project": proof.project, "docstatus": ["<", 2]},
			pluck="name"))
	if not orders:
		return []
	groups = set(print_groups())
	include_bom = cint(settings().include_bom_items)
	found, seen = [], set()

	def visit(code, depth=0):
		if not code or code in seen or depth > 4:
			return
		seen.add(code)
		item = frappe.db.get_value("Item", code, ["item_group", "default_bom"], as_dict=True)
		if not item:
			return
		if item.item_group in groups:
			found.append(code)
		if include_bom:
			bom = item.default_bom or frappe.db.get_value("BOM", {"item": code, "is_default": 1, "docstatus": 1}, "name")
			if bom:
				for child in frappe.get_all("BOM Item", filters={"parent": bom, "parenttype": "BOM"},
						pluck="item_code", order_by="idx asc"):
					visit(child, depth + 1)

	for so in sorted(orders):
		for code in frappe.get_all("Sales Order Item", filters={"parent": so, "parenttype": "Sales Order"},
				pluck="item_code", order_by="idx asc"):
			visit(code)
	return found


# ---------------------------------------------------------------------- status
def computed_status(proof) -> str:
	"""Derived from the rows, except for states set by an action (sent / approved / cancelled…)."""
	current = proof.status or "Draft"
	if current in LOCKED_STATUSES or proof.flags.get("status_set"):
		return current
	rows = proof.items
	if not rows:
		base = "Draft"
	elif any(not r.artwork_file for r in rows):
		base = "Awaiting Artwork"
	elif any(r.processing_status in ("Queued", "Processing") for r in rows):
		base = "Processing"
	elif any(r.processing_status in ("Failed", "Needs Setup") for r in rows):
		base = "Needs Attention"
	elif any(r.processing_status == "Not Processed" for r in rows):
		base = "Draft"
	else:
		base = "Ready for Review"
	if current in ("Sent to Customer", "Changes Requested") and base == "Ready for Review":
		return current
	return base


def refresh_proof_status(name):
	if not name or not frappe.db.exists("Artwork Proof", name):
		return
	proof = frappe.get_doc("Artwork Proof", name)
	new = computed_status(proof)
	if new != proof.status:
		frappe.db.set_value("Artwork Proof", name, "status", new)
		sync_project_status(proof.project)
	frappe.publish_realtime("artwork_proof_progress", {"name": name, "status": new, "done": True},
		doctype="Artwork Proof", docname=name, after_commit=True)


def sync_project_status(project):
	if not project or not frappe.get_meta("Project").has_field("artwork_status"):
		return
	statuses = frappe.get_all("Artwork Proof", filters={"project": project, "status": ["!=", "Cancelled"]},
		pluck="status")
	label = ""
	if statuses and all(s == "Approved" for s in statuses):
		label = "Approved"
	elif statuses:
		labels = {PROJECT_LABEL.get(s, "In Proofing") for s in statuses if s != "Approved"}
		for candidate in ("Changes Requested", "Awaiting Artwork", "In Proofing", "Sent to Customer"):
			if candidate in labels:
				label = candidate
				break
	frappe.db.set_value("Project", project, "artwork_status", label, update_modified=False)
