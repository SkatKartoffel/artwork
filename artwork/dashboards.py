def _add(data, label, items):
	data.setdefault("transactions", [])
	for t in data["transactions"]:
		if t.get("label") == label:
			t["items"] = list(dict.fromkeys([*t["items"], *items]))
			return data
	data["transactions"].append({"label": label, "items": items})
	return data


def project_dashboard(data):
	data.setdefault("non_standard_fieldnames", {})
	data["non_standard_fieldnames"]["Artwork Component"] = "project"
	return _add(data, "Artwork", ["Artwork Proof", "Artwork Component"])


def item_dashboard(data):
	data.setdefault("non_standard_fieldnames", {})
	data["non_standard_fieldnames"]["Artwork Component"] = "item"
	return _add(data, "Artwork", ["Artwork Component"])
