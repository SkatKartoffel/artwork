def execute():
	"""Rad and Voit templates no longer carry the supplier's name in their title text (v0.7.1)."""
	from artwork.template_loader import load

	load(update_files=1)
