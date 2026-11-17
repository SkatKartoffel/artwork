# Copyright (c) 2026, Packaged Sounds and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, escape_html, get_url, now_datetime, nowdate

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
	def validate(self):
		if not self.title:
			self.title = " – ".join(filter(None, [self.project_name or self.project, self.customer]))
		if not self.contact_email and self.customer:
			self.contact_email = frappe.db.get_value("Customer", self.customer, "email_id")

	def on_update(self):
		sync_project_status(self.project)

	def on_trash(self):
		if self.status == "Approved":
			frappe.throw(_("An approved proof can't be deleted. Cancel it instead."))

	def after_delete(self):
		sync_project_status(self.project)

	# ------------------------------------------------------------------ helpers
	def components(self, fields=None):
		return frappe.get_all(
			"Artwork Component",
			filters={"artwork_proof": self.name},
			fields=fields or ["name", "item", "item_name", "component_type", "processing_status",
				"error_count", "warning_count", "artwork_file", "print_file", "preview_image",
				"proof_file", "supplier_template", "finish", "approved"],
			order_by="creation asc",
		)

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

	# ------------------------------------------------------------------ actions
	@frappe.whitelist()
	def process_all(self):
		from artwork.processing import enqueue_component

		self._check_unlocked()
		n = 0
		for c in self.components(["name", "artwork_file", "processing_status"]):
			if c.artwork_file and c.processing_status not in ("Queued", "Processing"):
				enqueue_component(c.name)
				n += 1
		refresh_proof_status(self.name)
		return n

	@frappe.whitelist()
	def request_artwork(self, recipients=None, message=None):
		self._check_unlocked()
		recipients = recipients or self.contact_email
		if not recipients:
			frappe.throw(_("Set the customer email first."))
		self.ensure_access_key()
		s = frappe.get_cached_doc("Artwork Settings")
		url = self.portal_url("artwork_upload")
		rows = "".join(
			f"<li>{escape_html(c.component_type or '')} – {escape_html(c.item)} {escape_html(c.item_name or '')}</li>"
			for c in self.components()
		)
		body = (message or s.upload_email_intro or "") + (
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
	def send_to_customer(self, recipients=None, message=None):
		from artwork.processing import build_combined_proof, file_path

		self._check_unlocked()
		comps = self.components()
		if not comps:
			frappe.throw(_("Add at least one artwork component."))
		not_ready = [c.name for c in comps if c.processing_status != "Processed"]
		if not_ready:
			frappe.throw(_("These components are not processed yet: {0}").format(", ".join(not_ready)))
		if cint(frappe.db.get_single_value("Artwork Settings", "block_send_on_errors")):
			open_errors = frappe.get_all("Artwork Preflight Warning", filters={
				"parenttype": "Artwork Component", "parent": ["in", [c.name for c in comps]],
				"severity": "Error", "acknowledged": 0}, fields=["parent", "message"])
			if open_errors:
				lines = "".join(f"<li><b>{e.parent}</b>: {escape_html(e.message)}</li>" for e in open_errors[:10])
				frappe.throw(_("Preflight errors must be fixed or ticked as OK before sending:") + f"<ul>{lines}</ul>",
					title=_("Unresolved preflight errors"))
		recipients = recipients or self.contact_email
		if not recipients:
			frappe.throw(_("Set the customer email first."))

		self.revision = cint(self.revision) + 1
		self.ensure_access_key()
		self.db_update()
		for c in comps:
			frappe.db.set_value("Artwork Component", c.name, "approved", 0, update_modified=False)
		proof_url = build_combined_proof(self.name)
		self.reload()
		s = frappe.get_cached_doc("Artwork Settings")
		url = self.portal_url()
		body = (message or s.proof_email_intro or "") + (
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
		frappe.sendmail(recipients=recipients, cc=self.cc_emails,
			subject=_("Artwork proof for approval: {0} (rev {1})").format(self.title or self.name, self.revision),
			message=body, attachments=attachments, reference_doctype=self.doctype, reference_name=self.name)
		self.status = "Sent to Customer"
		self.sent_on = now_datetime()
		self.add_log("Sent to customer", recipients)
		self.save()
		return url

	@frappe.whitelist()
	def new_revision(self):
		if self.status not in ("Approved", "Sent to Customer", "Changes Requested"):
			frappe.throw(_("Only sent or approved proofs need a new revision."))
		was = self.status
		self.status = "Draft"
		self.add_log("Reopened", _("Reopened from {0}").format(was))
		self.save()
		for c in self.components(["name"]):
			frappe.db.set_value("Artwork Component", c.name, "approved", 0)
		refresh_proof_status(self.name)

	@frappe.whitelist()
	def copy_customer_link(self, page="artwork_approval"):
		if page not in ("artwork_approval", "artwork_upload"):
			frappe.throw(_("Unknown page"))
		if not self.access_key or not self.link_expires or self.link_expires < now_datetime():
			self.ensure_access_key()
			self.save()
		return self.portal_url(page)

	@frappe.whitelist()
	def cancel_proof(self):
		self.status = "Cancelled"
		self.add_log("Cancelled")
		self.save()

	# ------------------------------------------------------------------ customer responses
	def customer_approve(self, by_name, comment=None):
		self.status = "Approved"
		self.responded_on = now_datetime()
		self.responded_by = by_name
		self.customer_comments = comment
		self.approved_revision = self.revision
		self.add_log("Approved by customer", comment, by=by_name)
		self.flags.ignore_permissions = True
		self.save()
		item_meta = frappe.get_meta("Item")
		for c in self.components(["name", "item", "print_file"]):
			frappe.db.set_value("Artwork Component", c.name, "approved", 1)
			if item_meta.has_field("approved_artwork"):
				frappe.db.set_value("Item", c.item, {
					"approved_artwork": c.print_file,
					"approved_artwork_proof": self.name,
					"artwork_approved_on": nowdate(),
				}, update_modified=False)
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

	def _check_unlocked(self):
		if self.status in LOCKED_STATUSES:
			frappe.throw(_("Proof is {0}. Use New Revision to make changes.").format(self.status))


# ---------------------------------------------------------------------- status
def computed_status(proof) -> str:
	if proof.status in LOCKED_STATUSES:
		return proof.status
	comps = proof.components(["processing_status", "artwork_file"])
	if not comps:
		base = "Draft"
	elif any(not c.artwork_file for c in comps):
		base = "Awaiting Artwork"
	elif any(c.processing_status in ("Queued", "Processing") for c in comps):
		base = "Processing"
	elif any(c.processing_status == "Failed" for c in comps):
		base = "Needs Attention"
	elif any(c.processing_status == "Not Processed" for c in comps):
		base = "Draft"
	else:
		base = "Ready for Review"
	if proof.status in ("Sent to Customer", "Changes Requested") and base == "Ready for Review":
		return proof.status
	return base


def refresh_proof_status(name):
	if not name or not frappe.db.exists("Artwork Proof", name):
		return
	proof = frappe.get_doc("Artwork Proof", name)
	new = computed_status(proof)
	if new != proof.status:
		frappe.db.set_value("Artwork Proof", name, "status", new)
		sync_project_status(proof.project)
	frappe.publish_realtime("artwork_proof_status", {"name": name, "status": new},
		doctype="Artwork Proof", docname=name, after_commit=True)


def sync_project_status(project):
	if not project or not frappe.get_meta("Project").has_field("artwork_status"):
		return
	statuses = [s for s in frappe.get_all("Artwork Proof", filters={"project": project, "status": ["!=", "Cancelled"]},
		pluck="status")]
	if not statuses:
		label = ""
	elif all(s == "Approved" for s in statuses):
		label = "Approved"
	else:
		labels = {PROJECT_LABEL.get(s, "In Proofing") for s in statuses if s != "Approved"}
		for candidate in ("Changes Requested", "Awaiting Artwork", "In Proofing", "Sent to Customer"):
			if candidate in labels:
				label = candidate
				break
	frappe.db.set_value("Project", project, "artwork_status", label, update_modified=False)
