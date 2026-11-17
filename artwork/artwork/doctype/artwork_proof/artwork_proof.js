// Copyright (c) 2026, Packaged Sounds and contributors

const ART_STATUS_COLOUR = {
	"Draft": "gray", "Awaiting Artwork": "orange", "Processing": "blue", "Needs Attention": "red",
	"Ready for Review": "purple", "Sent to Customer": "cyan", "Changes Requested": "yellow",
	"Approved": "green", "Cancelled": "gray",
};

frappe.ui.form.on("Artwork Proof", {
	setup(frm) {
		frm.set_query("project", () => ({ filters: { status: ["!=", "Cancelled"] } }));
	},

	onload(frm) {
		frappe.realtime.off("artwork_component_status");
		frappe.realtime.on("artwork_component_status", (d) => {
			if (d.artwork_proof === frm.doc.name) {
				frm.events.render_components(frm);
				if (["Processed", "Failed"].includes(d.status)) frm.reload_doc();
			}
		});
	},

	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.status), ART_STATUS_COLOUR[frm.doc.status] || "gray");
		frm.events.render_components(frm);
		if (frm.is_new()) return;
		const locked = ["Approved", "Cancelled"].includes(frm.doc.status);

		if (!locked) {
			frm.add_custom_button(__("Add Component"), () => frm.events.add_component(frm));
			frm.add_custom_button(__("Process All"), () =>
				frm.call("process_all").then((r) => {
					frappe.show_alert({ message: __("{0} component(s) queued", [r.message || 0]), indicator: "blue" });
					frm.reload_doc();
				}), __("Actions"));
			frm.add_custom_button(__("Request Artwork from Customer"), () =>
				frm.events.email_dialog(frm, "request_artwork", __("Request artwork")), __("Actions"));
			if (["Ready for Review", "Sent to Customer", "Changes Requested"].includes(frm.doc.status)) {
				frm.add_custom_button(__("Send Proof to Customer"), () =>
					frm.events.email_dialog(frm, "send_to_customer", __("Send proof")));
				frm.change_custom_button_type(__("Send Proof to Customer"), null, "primary");
			}
		}
		if (["Approved", "Sent to Customer", "Changes Requested"].includes(frm.doc.status)) {
			frm.add_custom_button(__("New Revision"), () =>
				frappe.confirm(__("Reopen this proof for changes? The customer will need to approve again."),
					() => frm.call("new_revision").then(() => frm.reload_doc())), __("Actions"));
		}
		frm.add_custom_button(__("Copy Approval Link"), () => frm.events.copy_link(frm, "artwork_approval"), __("Customer Links"));
		frm.add_custom_button(__("Copy Upload Link"), () => frm.events.copy_link(frm, "artwork_upload"), __("Customer Links"));
		if (!locked) {
			frm.add_custom_button(__("Cancel Proof"), () =>
				frappe.confirm(__("Cancel this proof?"), () => frm.call("cancel_proof").then(() => frm.reload_doc())),
				__("Actions"));
		}
		if (frm.doc.project) {
			frm.add_custom_button(__("Job Ticket"), () => frappe.set_route("Form", "Project", frm.doc.project), __("View"));
		}
		if (frm.doc.proof_pdf) {
			frm.add_custom_button(__("Combined Proof PDF"), () => window.open(frm.doc.proof_pdf), __("View"));
		}
	},

	add_component(frm) {
		frappe.model.with_doctype("Artwork Component", () => {
			const doc = frappe.model.get_new_doc("Artwork Component");
			doc.artwork_proof = frm.doc.name;
			frappe.set_route("Form", "Artwork Component", doc.name);
		});
	},

	copy_link(frm, page) {
		frm.call("copy_customer_link", { page }).then((r) => {
			frappe.utils.copy_to_clipboard(r.message);
			frm.reload_doc();
		});
	},

	email_dialog(frm, method, title) {
		const d = new frappe.ui.Dialog({
			title,
			fields: [
				{ fieldname: "recipients", fieldtype: "Data", label: __("To"), options: "Email", reqd: 1,
					default: frm.doc.contact_email },
				{ fieldname: "message", fieldtype: "Text Editor", label: __("Message (optional)"),
					description: __("Leave blank to use the standard text from Artwork Settings.") },
			],
			primary_action_label: __("Send"),
			primary_action(values) {
				d.hide();
				frm.call(method, values).then((r) => {
					frappe.show_alert({ message: __("Sent"), indicator: "green" });
					frm.reload_doc();
				});
			},
		});
		d.show();
	},

	render_components(frm) {
		const wrap = frm.get_field("components_html").$wrapper;
		if (frm.is_new()) {
			wrap.html(`<p class="text-muted">${__("Save first, then add components (one per printed SKU part).")}</p>`);
			return;
		}
		frappe.call("artwork.api.proof_summary", { proof: frm.doc.name }).then((r) => {
			const rows = r.message || [];
			if (!rows.length) {
				wrap.html(`<p class="text-muted">${__("No components yet. Use <b>Add Component</b> for each printed part (sleeve, labels, inserts…).")}</p>`);
				return;
			}
			const pill = (s) => {
				const c = { Processed: "green", Failed: "red", Processing: "blue", Queued: "blue" }[s] || "gray";
				return `<span class="indicator-pill ${c}">${__(s)}</span>`;
			};
			const html = rows.map((c) => {
				const thumb = c.preview_image
					? `<img src="${c.preview_image}" style="width:220px;max-height:140px;object-fit:contain;background:#f3f3f3;border-radius:6px">`
					: `<div style="width:220px;height:110px;background:#f3f3f3;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999">${c.artwork_file ? __("Not processed") : __("No artwork")}</div>`;
				const issues = (c.issues || []).map((w) =>
					`<div style="font-size:12px;${w.acknowledged ? "opacity:.5;text-decoration:line-through" : ""}">
						<span class="indicator ${w.severity === "Error" ? "red" : "orange"}"></span>${frappe.utils.escape_html(w.message)}</div>`).join("");
				return `<div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--border-color)">
					<a href="/app/artwork-component/${c.name}">${thumb}</a>
					<div style="flex:1;min-width:0">
						<div><a href="/app/artwork-component/${c.name}"><b>${frappe.utils.escape_html(c.component_type || "")}</b>
							&nbsp;${frappe.utils.escape_html(c.item)} · ${frappe.utils.escape_html(c.item_name || "")}</a>
							&nbsp;${pill(c.processing_status)} ${c.approved ? '<span class="indicator-pill green">Approved</span>' : ""}</div>
						<div class="text-muted small">${frappe.utils.escape_html(c.supplier_template || "")} · ${frappe.utils.escape_html(c.finish || "")}</div>
						<div style="margin-top:6px">${issues || '<span class="text-muted small">' + __("No preflight issues") + "</span>"}</div>
						<div style="margin-top:6px" class="small">
							${c.print_file ? `<a href="${c.print_file}" target="_blank">${__("Print PDF")}</a> · ` : ""}
							${c.proof_file ? `<a href="${c.proof_file}" target="_blank">${__("Proof sheet")}</a>` : ""}
						</div>
					</div></div>`;
			}).join("");
			wrap.html(html);
		});
	},
});
