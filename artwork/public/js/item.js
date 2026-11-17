// Artwork app: SKU (Item) integration
frappe.ui.form.on("Item", {
	setup(frm) {
		frm.set_query("default_artwork_template", () => ({ filters: { enabled: 1 } }));
	},
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.approved_artwork) {
			frm.add_custom_button(__("Approved Artwork"), () => window.open(frm.doc.approved_artwork), __("Artwork"));
			frm.dashboard.add_indicator(__("Artwork approved {0}", [frappe.datetime.str_to_user(frm.doc.artwork_approved_on || "")]), "green");
		}
		frm.add_custom_button(__("Artwork History"), () =>
			frappe.set_route("List", "Artwork Component", { item: frm.doc.name }), __("Artwork"));
	},
});
