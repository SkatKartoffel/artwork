"""Shared get_context logic for the customer portal pages."""
import frappe
from frappe import _


def build_context(context, page):
	context.no_cache = 1
	context.show_sidebar = False
	context.no_breadcrumbs = True
	s = frappe.get_cached_doc("Artwork Settings")
	context.company = s.company_name or "Packaged Sounds"
	context.brand_colour = s.brand_colour or "#111111"
	context.page = page
	proof = frappe.form_dict.get("proof")
	key = frappe.form_dict.get("key")
	context.title = _("Artwork")
	context.csrf = frappe.sessions.get_csrf_token() if frappe.session.user != "Guest" else ""
	try:
		from artwork.api import portal_context

		doc, rows = portal_context(proof, key)
	except (frappe.PermissionError, frappe.DoesNotExistError) as e:
		context.error = str(e) or _("This link is not valid.")
		frappe.clear_messages()
		return context
	context.proof = frappe._dict(name=doc.name, title=doc.title, status=doc.status, revision=doc.revision,
		project_name=doc.project_name or doc.project, has_combined=bool(doc.proof_pdf),
		responded_by=(doc.responded_by or "").split(" (")[0], responded_on=doc.responded_on)
	context.key = key
	context.components = rows
	context.file_base = f"/api/method/artwork.api.portal_file?proof={doc.name}&key={key}"
	context.title = f"{context.company} · {doc.title or doc.name}"
	return context
