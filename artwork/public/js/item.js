// Artwork app: print spec on the SKU (Item)
frappe.ui.form.on("Item", {
	setup(frm) {
		frm.set_query("artwork_template", () => ({ filters: { enabled: 1 } }));
		frm.set_query("upgrade", "artwork_upgrades", () => ({ filters: { enabled: 1 } }));
	},
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.approved_artwork) {
			frm.add_custom_button(__("Approved Artwork"), () => window.open(frm.doc.approved_artwork), __("Artwork"));
			frm.dashboard.add_indicator(__("Artwork approved {0}",
				[frappe.datetime.str_to_user(frm.doc.artwork_approved_on || "")]), "green");
		}
		frm.add_custom_button(__("Artwork Proofs"), () =>
			frappe.call("artwork.api.item_proofs", { item: frm.doc.name }).then((r) => {
				const rows = r.message || [];
				if (!rows.length) return frappe.msgprint(__("This item isn't on any artwork proof yet."));
				frappe.msgprint({
					title: __("Artwork proofs for {0}", [frm.doc.name]),
					message: rows.map((p) => `<div><a href="/app/artwork-proof/${p.name}">${p.name}</a> · rev ${p.revision || 0}
						· ${frappe.utils.escape_html(p.status)} · ${frappe.utils.escape_html(p.title || "")}</div>`).join(""),
				});
			}), __("Artwork"));
	},
});

frappe.ui.form.on("Artwork Item Upgrade", {
	upgrade(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.upgrade) return;
		frappe.db.get_value("Artwork Print Upgrade", row.upgrade, "default_source").then((r) => {
			if (r.message && r.message.default_source) frappe.model.set_value(cdt, cdn, "source", r.message.default_source);
		});
	},
});
