import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

ROLES = ("Artwork Manager", "Artwork User")

CUSTOM_FIELDS = {
	"Project": [
		dict(fieldname="artwork_status", label="Artwork Status", fieldtype="Select",
			options="\nAwaiting Artwork\nIn Proofing\nSent to Customer\nChanges Requested\nApproved",
			insert_after="status", read_only=1, in_standard_filter=1, in_list_view=1, allow_on_submit=1,
			no_copy=1, translatable=0),
	],
	"Item": [
		dict(fieldname="artwork_section", label="Artwork / Print Spec", fieldtype="Section Break", insert_after="description",
			collapsible=1, description="Print parts (Item Group: Print) – used when this item appears on an Artwork Proof."),
		dict(fieldname="artwork_supplier", label="Print Supplier", fieldtype="Link", options="Supplier",
			insert_after="artwork_section"),
		dict(fieldname="artwork_product_size", label="Product Size", fieldtype="Link", options="Artwork Product Size",
			insert_after="artwork_supplier"),
		dict(fieldname="artwork_print_type", label="Print Type", fieldtype="Link", options="Artwork Print Type",
			insert_after="artwork_product_size"),
		dict(fieldname="artwork_template", label="Matched Supplier Template", fieldtype="Link",
			options="Artwork Supplier Template", insert_after="artwork_print_type", read_only=1, no_copy=1,
			description="Picked automatically from Supplier + Product Size + Print Type."),
		dict(fieldname="artwork_finish", label="Finish", fieldtype="Link", options="Artwork Finish",
			insert_after="artwork_template", description="Gloss / Matt → ISO Coated v2 300%; Reverse Board / Uncoated → Euroscale Uncoated v2"),
		dict(fieldname="artwork_outline_fonts", label="Convert Fonts to Outlines", fieldtype="Check", default="1",
			insert_after="artwork_finish"),
		dict(fieldname="artwork_cb", fieldtype="Column Break", insert_after="artwork_outline_fonts"),
		dict(fieldname="approved_artwork", label="Approved Print-Ready Artwork", fieldtype="Attach",
			insert_after="artwork_cb", read_only=1, no_copy=1),
		dict(fieldname="approved_artwork_proof", label="Approved On Proof", fieldtype="Link", options="Artwork Proof",
			insert_after="approved_artwork", read_only=1, no_copy=1),
		dict(fieldname="artwork_approved_on", label="Artwork Approved On", fieldtype="Date",
			insert_after="approved_artwork_proof", read_only=1, no_copy=1),
		dict(fieldname="artwork_upgrades", label="Print Upgrades (Spot UV, foil…)", fieldtype="Table",
			options="Artwork Item Upgrade", insert_after="artwork_approved_on"),
	],
}

PRODUCT_SIZES = ['7"', '10"', '12"']
PRINT_TYPES = [
	("Outer Sleeve – Spineless", 1),
	("Outer Sleeve – 3mm Spine", 0),
	("Outer Sleeve – 5mm Spine", 0),
	("Gatefold – Outer", 0),
	("Gatefold – Inner", 1),
	("Printed Inner Sleeve", 1),
	("Centre Label", 1),
	("Sticker", 1),
	("Insert", 1),
	("Poster", 1),
	("Obi Strip", 1),
]

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
	for name, no_spine in PRINT_TYPES:
		if not frappe.db.exists("Artwork Print Type", name):
			frappe.get_doc({"doctype": "Artwork Print Type", "print_type_name": name, "no_spine": no_spine}).insert(
				ignore_permissions=True)


def seed_masters():
	seed_sizes_and_types()
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
