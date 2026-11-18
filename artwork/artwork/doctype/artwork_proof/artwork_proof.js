// Copyright (c) 2026, Packaged Sounds and contributors

const ART_STATUS_COLOUR = {
	"Draft": "gray", "Awaiting Artwork": "orange", "Processing": "blue", "Needs Attention": "red",
	"Ready for Review": "purple", "Sent to Customer": "cyan", "Changes Requested": "yellow",
	"Approved": "green", "Cancelled": "gray",
};
const ROW_COLOUR = { Processed: "green", Failed: "red", "Needs Setup": "orange", Processing: "blue", Queued: "blue" };

frappe.ui.form.on("Artwork Proof", {
	setup(frm) {
		frm.set_query("project", () => ({ filters: { status: ["!=", "Cancelled"] } }));
		frm.set_query("item", "items", () => ({ query: "artwork.api.print_item_query" }));
	},

	onload(frm) {
		frappe.realtime.off("artwork_proof_progress");
		frappe.realtime.on("artwork_proof_progress", (d) => {
			if (d.name !== frm.doc.name) return;
			if (d.done || ["Processed", "Failed", "Needs Setup"].includes(d.status)) {
				if (!frm.is_dirty()) frm.reload_doc();
			} else if (d.item) {
				frm.events.render_items(frm, { [d.item]: d.status });
			}
		});
	},

	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.status), ART_STATUS_COLOUR[frm.doc.status] || "gray");
		frm.events.render_items(frm);
		if (frm.is_new()) return;
		const locked = ["Approved", "Cancelled"].includes(frm.doc.status);
		const act = (label, method, args, group) => frm.add_custom_button(label, () => frm.events.run(frm, method, args), group);

		if (!locked) {
			act(__("Get Print Items from Sales Order"), "get_print_items", {}, __("Actions"));
			act(__("Process All Artwork"), "process", {}, __("Actions"));
			frm.add_custom_button(__("Request Artwork from Customer"), () =>
				frm.events.email_dialog(frm, "request_artwork", __("Request artwork")), __("Actions"));
			if (["Ready for Review", "Sent to Customer", "Changes Requested"].includes(frm.doc.status)) {
				frm.add_custom_button(__("Send Proof to Customer"), () =>
					frm.events.email_dialog(frm, "send_to_customer", __("Send proof")));
				frm.change_custom_button_type(__("Send Proof to Customer"), null, "primary");
				// no email: just open the proof for approval and copy the link to send by hand
				frm.add_custom_button(__("Open for Approval (no email)"), async () => {
					await frm.events.run(frm, "send_to_customer", { no_email: 1 });
					frm.events.copy_link(frm, "artwork_approval");
				}, __("Customer Links"));
			}
			frm.add_custom_button(__("Cancel Proof"), () =>
				frappe.confirm(__("Cancel this proof?"), () => frm.events.run(frm, "cancel_proof")), __("Actions"));
		}
		if (["Approved", "Sent to Customer", "Changes Requested"].includes(frm.doc.status)) {
			frm.add_custom_button(__("New Revision"), () =>
				frappe.confirm(__("Reopen this proof for changes? The customer will need to approve again."),
					() => frm.events.run(frm, "new_revision")), __("Actions"));
		}
		frm.add_custom_button(__("Copy Approval Link"), () => frm.events.copy_link(frm, "artwork_approval"), __("Customer Links"));
		frm.add_custom_button(__("Copy Upload Link"), () => frm.events.copy_link(frm, "artwork_upload"), __("Customer Links"));
		if (frm.doc.items && frm.doc.items.length) {
			frm.add_custom_button(__("Purchase Order"), () => frm.events.create_po(frm), __("Create"));
		}
		if (frm.doc.project) frm.add_custom_button(__("Job Ticket"), () => frappe.set_route("Form", "Project", frm.doc.project), __("View"));
		if (frm.doc.proof_pdf) frm.add_custom_button(__("Combined Proof PDF"), () => window.open(frm.doc.proof_pdf), __("View"));
	},

	// Save pending edits first, then run a server action on the saved record.
	async run(frm, method, args = {}) {
		if (frm.is_dirty()) await frm.save();
		const r = await frm.call(method, args);
		const msg = {
			get_print_items: (m) => (m && m.length ? __("Added {0}", [m.join(", ")]) : __("No new print items found on the Sales Order")),
			process: (m) => __("{0} item(s) queued for processing", [m]),
		}[method];
		if (msg) frappe.show_alert({ message: msg(r.message), indicator: "blue" });
		await frm.reload_doc();
	},

	// One Purchase Order per print supplier, opened unsaved so staff add quantities and prices.
	async create_po(frm) {
		if (frm.is_dirty()) await frm.save();
		const d = (await frm.call("po_suppliers")).message;
		if (!d.suppliers.length) {
			frappe.msgprint(__("Set a <b>Print Supplier</b> on the print items first: {0}", [d.no_supplier.join(", ")]));
			return;
		}
		if (d.no_supplier.length) {
			frappe.show_alert({ message: __("No Print Supplier set on {0} – not included", [d.no_supplier.join(", ")]), indicator: "orange" });
		}
		const open = async (supplier) => {
			const src = (await frm.call("make_purchase_order", { supplier })).message;
			frappe.model.with_doctype("Purchase Order", () => {
				const skip = new Set(["name", "idx", "doctype", "owner", "creation", "modified", "modified_by", "docstatus",
					"parent", "parentfield", "parenttype", "__islocal", "__unsaved"]);
				const po = frappe.model.get_new_doc("Purchase Order");
				Object.keys(src).forEach((k) => {
					if (!skip.has(k) && !Array.isArray(src[k]) && src[k] !== null && src[k] !== undefined) po[k] = src[k];
				});
				(src.items || []).forEach((it) => {
					const row = frappe.model.add_child(po, "items");
					Object.keys(it).forEach((k) => { if (!skip.has(k) && it[k] !== null && it[k] !== undefined) row[k] = it[k]; });
				});
				frappe.set_route("Form", "Purchase Order", po.name);
				frappe.show_alert({ message: __("Check the quantities and save, then use Get Supplier Prices to fill in prices and set-ups"), indicator: "blue" });
			});
		};
		const go = (s) => s.existing.length
			? frappe.confirm(__("{0} already has {1} for this proof. Create another?", [s.supplier, s.existing.join(", ")]),
				() => open(s.supplier))
			: open(s.supplier);
		if (d.suppliers.length === 1) return go(d.suppliers[0]);
		frappe.prompt({
			fieldname: "supplier", fieldtype: "Select", label: __("Supplier"), reqd: 1,
			options: d.suppliers.map((s) => s.supplier),
			description: d.suppliers.map((s) => `<b>${frappe.utils.escape_html(s.supplier)}</b>: ${s.items.join(", ")}`
				+ (s.existing.length ? ` (${__("already")}: ${s.existing.join(", ")})` : "")).join("<br>"),
		}, (v) => go(d.suppliers.find((s) => s.supplier === v.supplier)), __("Create Purchase Order for"), __("Create"));
	},

	copy_link(frm, page) {
		// called by name, not with the form's copy of the record, so it is safe while the form is reloading
		frappe.call("run_doc_method", {
			dt: frm.doctype, dn: frm.docname, method: "copy_customer_link", args: { page },
		}).then((r) => {
			frappe.utils.copy_to_clipboard(r.message);
			if (!frm.is_dirty()) frm.reload_doc();
		});
	},

	email_dialog(frm, method, title) {
		const d = new frappe.ui.Dialog({
			title,
			fields: [
				{ fieldname: "recipients", fieldtype: "Data", label: __("To"), options: "Email", reqd: 1, default: frm.doc.contact_email },
				{ fieldname: "message", fieldtype: "Text Editor", label: __("Message (optional)"),
					description: __("Leave blank to use the standard text from Artwork Settings.") },
			],
			primary_action_label: __("Send"),
			primary_action(values) {
				d.hide();
				frm.events.run(frm, method, values).then(() => frappe.show_alert({ message: __("Sent"), indicator: "green" }));
			},
		});
		d.show();
	},

	render_items(frm, live = {}) {
		const wrap = frm.get_field("items_html").$wrapper;
		const rows = frm.doc.items || [];
		if (!rows.length) {
			wrap.html(`<p class="text-muted">${frm.doc.sales_order
				? __("No print items yet. Use <b>Actions → Get Print Items from Sales Order</b>, or add them to the table above.")
				: __("Link the Job Ticket's Sales Order, or add print items to the table above.")}</p>`);
			return;
		}
		const esc = frappe.utils.escape_html;
		const html = rows.map((r) => {
			const status = live[r.item] || r.processing_status || "Not Processed";
			const issues = (frm.doc.warnings || []).filter((w) => w.row_ref === r.name && w.severity !== "Info");
			const thumb = r.preview_image
				? `<a href="${r.preview_image}" target="_blank"><img src="${r.preview_image}?v=${encodeURIComponent(r.processed_on || "")}"
					style="width:260px;max-height:150px;object-fit:contain;background:#f3f3f3;border-radius:6px"></a>`
				: `<div style="width:260px;height:120px;background:#f3f3f3;border-radius:6px;display:flex;align-items:center;justify-content:center;color:#999;text-align:center;padding:8px">
					${r.artwork_file ? esc(__(status)) : __("Drop the customer's artwork into the row above")}</div>`;
			const issueHtml = issues.map((w) => `<div class="small" style="${w.acknowledged ? "opacity:.45;text-decoration:line-through" : ""}">
				<span class="indicator ${w.severity === "Error" ? "red" : "orange"}"></span>${esc(w.message)}</div>`).join("");
			const files = [
				r.print_file ? `<a href="${r.print_file}" target="_blank">${__("Print PDF")}</a>` : "",
				r.proof_file ? `<a href="${r.proof_file}" target="_blank">${__("Proof sheet")}</a>` : "",
				r.artwork_file ? `<a href="${r.artwork_file}" target="_blank">${__("Customer file")}</a>` : "",
			].filter(Boolean).join(" · ");
			const err = ["Failed", "Needs Setup"].includes(status) && r.processing_error
				? `<div class="small text-danger">${esc(r.processing_error)}</div>` : "";
			const canReprocess = r.artwork_file && !["Queued", "Processing"].includes(status)
				&& !["Approved", "Cancelled"].includes(frm.doc.status) && !frm.is_new();
			return `<div style="display:flex;gap:16px;padding:12px 0;border-bottom:1px solid var(--border-color)">
				${thumb}
				<div style="flex:1;min-width:0">
					<div><a href="/app/item/${encodeURIComponent(r.item || "")}"><b>${esc(r.item || "")}</b></a> · ${esc(r.item_name || "")}
						&nbsp;<span class="indicator-pill ${ROW_COLOUR[status] || "gray"}">${esc(__(status))}</span>
						${r.approved ? '<span class="indicator-pill green">' + __("Approved") + "</span>" : ""}</div>
					<div class="text-muted small">${esc(r.supplier_template || __("no template set on Item"))} · ${esc(r.finish || __("no finish set on Item"))}</div>
					${err}
					<div style="margin-top:6px">${issueHtml || (status === "Processed" ? '<span class="text-muted small">' + __("No preflight issues") + "</span>" : "")}</div>
					<div class="small" style="margin-top:6px">${files}
						${canReprocess ? `${files ? " · " : ""}<a href="#" data-reprocess="${r.name}">${__("Re-process")}</a>` : ""}</div>
				</div></div>`;
		}).join("");
		wrap.html(html);
		wrap.find("[data-reprocess]").on("click", (e) => {
			e.preventDefault();
			frm.events.run(frm, "process", { row: $(e.currentTarget).attr("data-reprocess") });
		});
	},
});

frappe.ui.form.on("Artwork Proof Item", {
	// Save straight away so processing starts as soon as artwork is dropped in.
	artwork_file(frm) { if (!frm.is_new()) frm.save(); },
	items_remove(frm) { frm.events.render_items(frm); },
});

frappe.ui.form.on("Artwork Proof Mask", {
	mask_file(frm) { if (!frm.is_new()) frm.save(); },
});
