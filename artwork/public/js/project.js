// Artwork app: Job Ticket (Project) integration
frappe.ui.form.on("Project", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("New Artwork Proof"), () => {
			frappe.new_doc("Artwork Proof", { project: frm.doc.name });
		}, __("Artwork"));
		frm.add_custom_button(__("Artwork Proofs"), () => {
			frappe.set_route("List", "Artwork Proof", { project: frm.doc.name });
		}, __("Artwork"));
		if (frm.doc.artwork_status) {
			const c = { "Approved": "green", "Changes Requested": "yellow", "Sent to Customer": "cyan",
				"In Proofing": "blue", "Awaiting Artwork": "orange" }[frm.doc.artwork_status] || "gray";
			frm.dashboard.add_indicator(__("Artwork: {0}", [__(frm.doc.artwork_status)]), c);
		}
	},
});
