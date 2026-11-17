// Copyright (c) 2026, Packaged Sounds and contributors

frappe.ui.form.on("Artwork Supplier Template", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.template_file) {
			frm.add_custom_button(__("Detect Geometry"), () =>
				frm.call("detect_geometry").then((r) => {
					const d = r.message || {};
					frappe.msgprint({
						title: __("Template geometry"),
						indicator: d.spine_centre ? "green" : "orange",
						message: `${__("Page")}: ${d.page[0]} × ${d.page[1]} mm<br>
							${__("Trim")}: ${d.trim[0] || "?"} × ${d.trim[1] || "?"} mm<br>
							${__("Spine")}: ${d.spine_centre ? `${__("centre")} ${d.spine_centre} mm, ${__("width")} ${d.spine_width} mm`
								: __("no fold lines found – enter Spine Centre Override if this product has a spine")}`,
					});
					frm.reload_doc();
				}));
			frm.add_custom_button(__("Open Template"), () => window.open(frm.doc.template_file));
		}
		if (frm.doc.template_preview) {
			frm.set_intro(`<img src="${frm.doc.template_preview}" style="max-width:100%;max-height:260px">`, "blue");
		}
	},
});
