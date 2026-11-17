def project_dashboard(data):
	data.setdefault("transactions", [])
	for t in data["transactions"]:
		if t.get("label") == "Artwork":
			if "Artwork Proof" not in t["items"]:
				t["items"].append("Artwork Proof")
			return data
	data["transactions"].append({"label": "Artwork", "items": ["Artwork Proof"]})
	return data
