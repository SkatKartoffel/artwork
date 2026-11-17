// Copyright (c) 2026, Packaged Sounds and contributors

frappe.ui.form.on("Artwork Component", {
	setup(frm) {
		frm.set_query("supplier_template", () => ({
			filters: Object.assign({ enabled: 1 }, frm.doc.supplier ? { supplier: frm.doc.supplier } : {}),
		}));
		frm.set_query("upgrade", "upgrades", () => ({ filters: { enabled: 1 } }));
		frm.set_query("artwork_proof", () => ({ filters: { status: ["not in", ["Approved", "Cancelled"]] } }));
	},

	onload(frm) {
		frappe.realtime.off("artwork_component_status");
		frappe.realtime.on("artwork_component_status", (d) => {
			if (d.name !== frm.doc.name) return;
			if (["Processed", "Failed"].includes(d.status)) {
				frm.reload_doc();
				frappe.show_alert({ message: __("Artwork {0}", [__(d.status).toLowerCase()]),
					indicator: d.status === "Processed" ? "green" : "red" });
			} else {
				frm.page.set_indicator(__(d.status), "blue");
			}
		});
	},

	refresh(frm) {
		const colour = { Processed: "green", Failed: "red", Processing: "blue", Queued: "blue" }[frm.doc.processing_status] || "gray";
		if (!frm.is_new()) frm.page.set_indicator(__(frm.doc.processing_status), colour);
		frm.events.render_preview(frm);
		if (frm.is_new()) return;
		if (frm.doc.artwork_file) {
			frm.add_custom_button(frm.doc.processing_status === "Processed" ? __("Re-process") : __("Process Artwork"), () =>
				frm.call("process").then(() => {
					frappe.show_alert({ message: __("Queued for processing"), indicator: "blue" });
					frm.reload_doc();
				}));
			if (frm.doc.processing_status !== "Processed") {
				frm.change_custom_button_type(__("Process Artwork"), null, "primary");
			}
		}
		if (frm.doc.print_file) frm.add_custom_button(__("Print-Ready PDF"), () => window.open(frm.doc.print_file), __("Open"));
		if (frm.doc.proof_file) frm.add_custom_button(__("Proof Sheet"), () => window.open(frm.doc.proof_file), __("Open"));
		if (frm.doc.artwork_file) frm.add_custom_button(__("Customer Artwork"), () => window.open(frm.doc.artwork_file), __("Open"));
		frm.add_custom_button(__("Artwork Proof"), () => frappe.set_route("Form", "Artwork Proof", frm.doc.artwork_proof), __("Open"));
	},

	item(frm) {
		if (!frm.doc.item) return;
		frappe.call("artwork.api.item_defaults", { item: frm.doc.item }).then((r) => {
			const d = r.message || {};
			if (d.supplier && !frm.doc.supplier) frm.set_value("supplier", d.supplier);
			if (d.default_artwork_template && !frm.doc.supplier_template) frm.set_value("supplier_template", d.default_artwork_template);
			if (d.default_artwork_finish && !frm.doc.finish) frm.set_value("finish", d.default_artwork_finish);
		});
	},

	supplier_template(frm) {
		if (!frm.doc.supplier_template) return;
		frappe.db.get_value("Artwork Supplier Template", frm.doc.supplier_template, ["supplier", "component_type"]).then((r) => {
			const v = r.message || {};
			if (v.supplier && frm.doc.supplier !== v.supplier) frm.set_value("supplier", v.supplier);
			if (v.component_type && !frm.doc.component_type) frm.set_value("component_type", v.component_type);
		});
	},

	render_preview(frm) {
		const w = frm.get_field("preview_html").$wrapper;
		if (!frm.doc.preview_image) {
			const msg = frm.doc.processing_status === "Failed"
				? `<span class="text-danger">${frappe.utils.escape_html(frm.doc.processing_error || "")}</span>`
				: frm.doc.artwork_file ? __("Not processed yet.") : __("Attach the customer's artwork to create a proof.");
			w.html(`<div class="text-muted" style="padding:20px;background:var(--subtle-fg);border-radius:8px">${msg}</div>`);
			return;
		}
		w.html(`<a href="${frm.doc.preview_image}" target="_blank">
			<img src="${frm.doc.preview_image}?v=${encodeURIComponent(frm.doc.processed_on || "")}"
				style="max-width:100%;max-height:520px;border-radius:8px;background:#eee"></a>
			<div class="text-muted small" style="margin-top:6px">${__("Coloured overlays show spot layers (finishes); magenta is the non-printing supplier template.")}</div>`);
	},
});

frappe.ui.form.on("Artwork Component Upgrade", {
	upgrade(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.upgrade) return;
		frappe.db.get_value("Artwork Print Upgrade", row.upgrade, "default_source").then((r) => {
			if (r.message && r.message.default_source) frappe.model.set_value(cdt, cdn, "source", r.message.default_source);
		});
	},
});
