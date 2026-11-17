import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = ("Artwork Manager", "Artwork User")

CUSTOM_FIELDS = {
	"Purchase Order": [
		dict(fieldname="artwork_proof", label="Artwork Proof", fieldtype="Link", options="Artwork Proof",
			insert_after="supplier", read_only=1, no_copy=1, search_index=1),
	],
	"Project": [
		dict(fieldname="artwork_status", label="Artwork Status", fieldtype="Select",
			options="\nAwaiting Artwork\nIn Proofing\nSent to Customer\nChanges Requested\nApproved",
			insert_after="status", read_only=1, in_standard_filter=1, in_list_view=1, allow_on_submit=1,
			no_copy=1, translatable=0),
	],
	"Item": [
		dict(fieldname="is_print_item", label="Print Item", fieldtype="Check", insert_after="item_group",
			read_only=1, hidden=1, description="Set automatically from the Item Group (Print and its sub-groups)."),
		dict(fieldname="artwork_tab", label="Print Spec", fieldtype="Tab Break", insert_after="description",
			depends_on="eval:doc.is_print_item"),
		dict(fieldname="artwork_section", label="Print Spec", fieldtype="Section Break", insert_after="artwork_tab",
			depends_on="eval:doc.is_print_item",
			description="Print parts only. Fills in the Description, the supplier template and the packing component on save."),
		dict(fieldname="artwork_supplier", label="Print Supplier", fieldtype="Link", options="Supplier",
			insert_after="artwork_section",
			description="Filled in from the supplier on the Purchasing tab if left blank."),
		dict(fieldname="artwork_product_size", label="Product Size", fieldtype="Link", options="Artwork Product Size",
			insert_after="artwork_supplier"),
		dict(fieldname="artwork_print_type", label="Print Type", fieldtype="Link", options="Artwork Print Type",
			insert_after="artwork_product_size"),
		dict(fieldname="artwork_variant", label="Variant", fieldtype="Autocomplete", insert_after="artwork_print_type",
			description="Only needed when the supplier has more than one template for this size and print type, e.g. 1 Hole Front."),
		dict(fieldname="artwork_stock", label="Board / Paper Stock", fieldtype="Link", options="Artwork Stock",
			insert_after="artwork_variant"),
		dict(fieldname="artwork_print_colours", label="Print Colours", fieldtype="Link", options="Artwork Print Colour",
			insert_after="artwork_stock"),
		dict(fieldname="artwork_cb1", fieldtype="Column Break", insert_after="artwork_print_colours"),
		dict(fieldname="artwork_finish", label="Finish", fieldtype="Link", options="Artwork Finish",
			insert_after="artwork_cb1",
			description="Gloss / Matt → ISO Coated v2 300%; Reverse Board / Uncoated → Euroscale Uncoated v2"),
		dict(fieldname="artwork_coating", label="Lamination / Varnish", fieldtype="Link", options="Artwork Coating",
			insert_after="artwork_finish"),
		dict(fieldname="artwork_outline_fonts", label="Convert Fonts to Outlines", fieldtype="Check", default="1",
			insert_after="artwork_coating"),
		dict(fieldname="artwork_template", label="Matched Supplier Template", fieldtype="Link",
			options="Artwork Supplier Template", insert_after="artwork_outline_fonts", read_only=1, no_copy=1,
			description="Picked automatically from Print Supplier + Product Size + Print Type."),
		dict(fieldname="artwork_upgrades_sb", fieldtype="Section Break", insert_after="artwork_template",
			depends_on="eval:doc.is_print_item"),
		dict(fieldname="artwork_upgrades", label="Print Upgrades (Spot UV, foil…)", fieldtype="Table",
			options="Artwork Item Upgrade", insert_after="artwork_upgrades_sb"),
		dict(fieldname="artwork_notes", label="Notes for Supplier", fieldtype="Small Text", insert_after="artwork_upgrades",
			description="Added to the end of the Description."),
		dict(fieldname="artwork_approved_sb", label="Approved Artwork", fieldtype="Section Break",
			insert_after="artwork_notes", collapsible=1, depends_on="eval:doc.is_print_item"),
		dict(fieldname="approved_artwork", label="Approved Print-Ready Artwork", fieldtype="Attach",
			insert_after="artwork_approved_sb", read_only=1, no_copy=1),
		dict(fieldname="artwork_cb2", fieldtype="Column Break", insert_after="approved_artwork"),
		dict(fieldname="approved_artwork_proof", label="Approved On Proof", fieldtype="Link", options="Artwork Proof",
			insert_after="artwork_cb2", read_only=1, no_copy=1),
		dict(fieldname="artwork_approved_on", label="Artwork Approved On", fieldtype="Date",
			insert_after="approved_artwork_proof", read_only=1, no_copy=1),
	],
}

PRODUCT_SIZES = ['7"', '10"', '12"']
PRINT_TYPES = [  # (name, no spine, packing component used by milomrp's packing-time maths)
	("Outer Sleeve – Spineless", 1, "Outer Sleeve"),
	("Outer Sleeve – 3mm Spine", 0, "Outer Sleeve"),
	("Outer Sleeve – 4mm Spine", 0, "Outer Sleeve"),
	("Outer Sleeve – 5mm Spine", 0, "Outer Sleeve"),
	("Outer Sleeve – 6mm Spine", 0, "Outer Sleeve"),
	("Picture Disc", 1, ""),
	("Obi Card", 1, "Insert"),
	("Gatefold – Outer", 0, "Outer Sleeve"),
	("Gatefold – Inner", 1, ""),
	("Printed Inner Sleeve", 1, "Inner Sleeve"),
	("Centre Label", 1, ""),
	("Sticker", 1, "Sticker"),
	("Sticker – Difficult", 1, "Difficult Sticker"),
	("Insert", 1, "Insert"),
	("Poster", 1, "Insert"),
	("Obi Strip", 1, "Insert"),
]

STOCKS = ["350gsm GC1 Board", "300gsm GC1 Board", "350gsm Reverse Board", "300gsm Uncoated Board",
	"170gsm Silk Paper", "150gsm Gloss Paper", "120gsm Uncoated Paper", "Self-adhesive Paper"]
PRINT_COLOURS = ["4/0 CMYK", "4/4 CMYK", "4/1 CMYK + Black", "1/0 Black", "1/1 Black", "Unprinted"]
COATINGS = ["Gloss Lamination", "Matt Lamination", "Soft-touch Lamination", "Gloss Varnish", "Matt Varnish",
	"Aqueous Coating"]

PROFILES = [
	{"profile_name": "ISO Coated v2 300% (ECI)", "max_ink": 300, "output_condition": "ISO Coated v2 300% (ECI)",
		"description": "Gloss and matt coated board. Download ISOcoated_v2_300_eci.icc from eci.org and attach it."},
	{"profile_name": "Euroscale Uncoated v2", "max_ink": 260, "output_condition": "Euroscale Uncoated v2",
		"description": "Uncoated / reverse board. Attach EuroscaleUncoated.icc (ships with Adobe Creative Cloud)."},
]
FINISHES = [
	("Gloss", "ISO Coated v2 300% (ECI)"),
	("Matt", "ISO Coated v2 300% (ECI)"),
	("Reverse Board", "Euroscale Uncoated v2"),
	("Uncoated", "Euroscale Uncoated v2"),
]
UPGRADES = [
	("Spot UV", "Spot UV", "UV, Spot Varnish, Varnish, Gloss UV, UV Varnish, Spot Gloss", "#29ABE2", 45, "30,0,0,0", "Artwork spot"),
	("Hot Foil Gold", "Foil Gold", "Gold Foil, Hot Foil Gold, Hotfoil Gold, Gold", "#D4AF37", 80, "0,20,70,20", "Artwork spot"),
	("Hot Foil Silver", "Foil Silver", "Silver Foil, Hot Foil Silver, Hotfoil Silver, Silver", "#B8BEC4", 80, "0,0,0,30", "Artwork spot"),
	("Emboss", "Emboss", "Embossing, Blind Emboss", "#8E6CEF", 40, "0,30,0,0", "Separate file"),
	("Deboss", "Deboss", "Debossing, Blind Deboss", "#6C8EEF", 40, "30,15,0,0", "Separate file"),
	("White Ink", "White", "White Ink, Opaque White, Underprint White", "#F2F2F2", 80, "0,0,0,8", "Artwork spot"),
]


def after_install():
	ensure_roles()
	ensure_print_group()
	create_custom_fields(CUSTOM_FIELDS, update=True)
	seed_masters()
	seed_settings()
	frappe.db.commit()


def after_migrate():
	ensure_roles()
	hide_packing_component_on_print_items()
	ensure_print_group()
	seed_settings()  # fills only settings that have never been set (e.g. ones added in an update)
	create_custom_fields(CUSTOM_FIELDS, update=True)


def ensure_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def seed_sizes_and_types():
	for size in PRODUCT_SIZES:
		if not frappe.db.exists("Artwork Product Size", size):
			frappe.get_doc({"doctype": "Artwork Product Size", "size_name": size}).insert(ignore_permissions=True)
	for name, no_spine, packing in PRINT_TYPES:
		if not frappe.db.exists("Artwork Print Type", name):
			frappe.get_doc({"doctype": "Artwork Print Type", "print_type_name": name, "no_spine": no_spine,
				"packing_component": packing}).insert(ignore_permissions=True)
		elif packing and not frappe.db.get_value("Artwork Print Type", name, "packing_component"):
			frappe.db.set_value("Artwork Print Type", name, "packing_component", packing)


def seed_spec_lists():
	for dt, field, values in (("Artwork Stock", "stock_name", STOCKS),
			("Artwork Print Colour", "print_colour_name", PRINT_COLOURS), ("Artwork Coating", "coating_name", COATINGS)):
		for v in values:
			if not frappe.db.exists(dt, v):
				frappe.get_doc({"doctype": dt, field: v}).insert(ignore_permissions=True)


def seed_masters():
	seed_sizes_and_types()
	seed_spec_lists()
	for p in PROFILES:
		if not frappe.db.exists("Artwork Colour Profile", p["profile_name"]):
			doc = frappe.get_doc({"doctype": "Artwork Colour Profile", **p})
			doc.flags.ignore_mandatory = True
			doc.insert(ignore_permissions=True)
	for name, profile in FINISHES:
		if not frappe.db.exists("Artwork Finish", name):
			frappe.get_doc({"doctype": "Artwork Finish", "finish_name": name, "colour_profile": profile}).insert(
				ignore_permissions=True)
	for name, spot, aliases, colour, opacity, alt, source in UPGRADES:
		if not frappe.db.exists("Artwork Print Upgrade", name):
			frappe.get_doc({"doctype": "Artwork Print Upgrade", "upgrade_name": name, "spot_name": spot,
				"aliases": aliases, "display_colour": colour, "preview_opacity": opacity,
				"alternate_cmyk": alt, "default_source": source}).insert(ignore_permissions=True)


def seed_settings():
	s = frappe.get_single("Artwork Settings")
	for df in s.meta.fields:
		if df.default and s.get(df.fieldname) in (None, ""):
			s.set(df.fieldname, df.default)
	s.flags.ignore_mandatory = True
	s.save(ignore_permissions=True)


def ensure_print_group():
	if frappe.db.exists("Item Group", "Print"):
		return
	parent = frappe.db.get_value("Item Group", {"is_group": 1, "parent_item_group": ["in", ["", None]]}, "name") \
		or ("All Item Groups" if frappe.db.exists("Item Group", "All Item Groups") else None)
	if not parent:
		return
	# Not a group node: ERPNext's Item form only lets you pick non-group item groups.
	frappe.get_doc({"doctype": "Item Group", "item_group_name": "Print", "parent_item_group": parent,
		"is_group": 0}).insert(ignore_permissions=True)


def hide_packing_component_on_print_items():
	"""milomrp's Item.packing_component: hidden on print items (set from their Print Template instead)."""
	if not frappe.get_meta("Item").has_field("packing_component"):
		return
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	make_property_setter("Item", "packing_component", "depends_on", "eval:!doc.is_print_item", "Code",
		validate_fields_for_doctype=False)
