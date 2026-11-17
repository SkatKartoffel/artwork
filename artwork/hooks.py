app_name = "artwork"
app_title = "Artwork"
app_publisher = "Packaged Sounds"
app_description = "Artwork proofing: supplier templates, spot layers, colour/ink fixes, preflight and customer approval"
app_email = "hello@packagedsounds.com"
app_license = "mit"

required_apps = ["erpnext"]

add_to_apps_screen = [
	{
		"name": "artwork",
		"logo": "/assets/artwork/images/artwork.svg",
		"title": "Artwork",
		"route": "/app/artwork",
	}
]

after_install = "artwork.install.after_install"
after_migrate = "artwork.install.after_migrate"

# Job Ticket (Project) and SKU (Item) integration
doctype_js = {
	"Project": "public/js/project.js",
	"Item": "public/js/item.js",
}
override_doctype_dashboards = {
	"Project": "artwork.dashboards.project_dashboard",
}

doc_events = {
	"Item": {"validate": "artwork.item_hooks.item_validate"},
	"Purchase Order": {
		"after_insert": "artwork.artwork.doctype.artwork_proof.artwork_proof.purchase_order_after_insert",
	},
}

# Other apps (e.g. milomrp) can react to customer decisions:
#   artwork_on_approve = ["milomrp.hooks_artwork.on_approve"]            # fn(proof_doc)
#   artwork_on_changes_requested = ["milomrp.hooks_artwork.on_changes"]  # fn(proof_doc)
