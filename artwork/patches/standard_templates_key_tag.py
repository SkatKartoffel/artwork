def execute():
	"""Standard templates now tag their key, so proofs can leave it out (v0.7.2)."""
	from artwork.template_loader import load

	load(update_files=1)
