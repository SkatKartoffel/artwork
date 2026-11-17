frappe.listview_settings["Artwork Component"] = {
	add_fields: ["processing_status", "error_count", "approved"],
	get_indicator(doc) {
		if (doc.approved) return [__("Approved"), "green", "approved,=,1"];
		if (doc.processing_status === "Processed" && doc.error_count) return [__("Has Errors"), "red", "error_count,>,0"];
		const c = { Processed: "green", Failed: "red", Processing: "blue", Queued: "blue" }[doc.processing_status] || "gray";
		return [__(doc.processing_status), c, "processing_status,=," + doc.processing_status];
	},
};
