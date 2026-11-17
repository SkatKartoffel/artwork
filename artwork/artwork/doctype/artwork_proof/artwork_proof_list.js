frappe.listview_settings["Artwork Proof"] = {
	add_fields: ["status", "revision"],
	get_indicator(doc) {
		const c = {
			"Draft": "gray", "Awaiting Artwork": "orange", "Processing": "blue", "Needs Attention": "red",
			"Ready for Review": "purple", "Sent to Customer": "cyan", "Changes Requested": "yellow",
			"Approved": "green", "Cancelled": "gray",
		}[doc.status] || "gray";
		return [__(doc.status), c, "status,=," + doc.status];
	},
};
