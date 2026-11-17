// Artwork app: print spec on the SKU (Item)
const PRINT_SPEC_FIELDS = ["artwork_supplier", "artwork_product_size", "artwork_print_type", "artwork_stock",
	"artwork_print_colours", "artwork_finish", "artwork_coating", "artwork_notes"];

const artworkPreview = frappe.utils.debounce((frm) => {
	if (!frm.doc.is_print_item) return;
	frappe.call("artwork.api.print_spec_preview", { doc: frm.doc }).then((r) => {
		const d = r.message || {};
		if ((frm.doc.artwork_template || null) !== (d.artwork_template || null)) frm.set_value("artwork_template", d.artwork_template || null);
		if (frm.fields_dict.packing_component && d.packing_component !== undefined
			&& (frm.doc.packing_component || "") !== (d.packing_component || "")) {
			frm.set_value("packing_component", d.packing_component || "");
		}
		if (d.description && d.description !== frm.doc.description) frm.set_value("description", d.description);
		const d2 = frm.doc;
		if (d2.artwork_supplier && d2.artwork_product_size && d2.artwork_print_type && !d.artwork_template) {
			frappe.show_alert({ message: __("No Supplier Template yet for {0} · {1} · {2}",
				[d2.artwork_supplier, d2.artwork_product_size, d2.artwork_print_type]), indicator: "orange" });
		}
	});
}, 400);

const itemEvents = {
	setup(frm) {
		frm.set_query("upgrade", "artwork_upgrades", () => ({ filters: { enabled: 1 } }));
		["artwork_stock", "artwork_print_colours", "artwork_coating"].forEach((f) =>
			frm.set_query(f, () => ({ filters: { enabled: 1 } })));
	},

	// Show/hide the Print Spec (and milomrp's Packing Component) as soon as the group changes.
	item_group(frm) {
		if (!frm.doc.item_group || !frm.fields_dict.is_print_item) return;
		frappe.call("artwork.api.is_print_group", { item_group: frm.doc.item_group }).then((r) => {
			frm.set_value("is_print_item", r.message ? 1 : 0);
			artworkPreview(frm);
		});
	},

	refresh(frm) {
		if (frm.is_new() || !frm.doc.is_print_item) return;
		if (frm.doc.approved_artwork) {
			frm.add_custom_button(__("Approved Artwork"), () => window.open(frm.doc.approved_artwork), __("Artwork"));
			frm.dashboard.add_indicator(__("Artwork approved {0}",
				[frappe.datetime.str_to_user(frm.doc.artwork_approved_on || "")]), "green");
		}
		if (frm.doc.artwork_template) {
			frm.add_custom_button(__("Supplier Template"), () =>
				frappe.set_route("Form", "Artwork Supplier Template", frm.doc.artwork_template), __("Artwork"));
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
};
PRINT_SPEC_FIELDS.forEach((f) => { itemEvents[f] = (frm) => artworkPreview(frm); });
frappe.ui.form.on("Item", itemEvents);

frappe.ui.form.on("Artwork Item Upgrade", {
	upgrade(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		artworkPreview(frm);
		if (!row.upgrade) return;
		frappe.db.get_value("Artwork Print Upgrade", row.upgrade, "default_source").then((r) => {
			if (r.message && r.message.default_source) frappe.model.set_value(cdt, cdn, "source", r.message.default_source);
		});
	},
	artwork_upgrades_remove(frm) { artworkPreview(frm); },
});
