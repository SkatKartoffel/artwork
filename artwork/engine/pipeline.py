"""End-to-end processing of one artwork component.

    result = process_component(job)

`job` is a plain dict (see `process_component` docstring) so the engine can be
used and tested without Frappe.
"""
from __future__ import annotations

import os
import shutil

from . import colour, geometry
from .compose import compose
from .normalise import normalise_page, page_count, to_pdf
from .preflight import run_preflight
from .preview import proof_sheet, render_preview
from .util import EngineError, Report, mm, pt
from .walker import list_spot_names, name_matches, run_mode

DEFAULT_TEMPLATE_ALIASES = ["Template", "Dieline", "Die Line", "Die", "Cut", "CutContour", "Cut Contour",
                            "Crease", "Fold", "Knife", "Stanze", "Stanzkontur", "Thru-cut", "Kiss Cut"]


def process_component(job: dict) -> dict:
    """Process one artwork component.

    job = {
      art_path, art_page=1, work_dir, out_basename,
      template: {path, page=1, spot_name='Template', alt_cmyk, display_rgb,
                 spine_centre_mm, spine_width_mm, trim_width_mm, trim_height_mm, bleed_mm, safe_margin_mm},
      template_aliases: [...],
      colour: {profile_path, identifier, tac_limit, intent=1},
      upgrades: [{label, spot_name, aliases, source: 'Artwork spot'|'Separate file'|'Artwork page',
                  file, page, alt_cmyk, display_rgb, opacity}],
      known_upgrades: [{spot_name, aliases}],      # every upgrade defined in the system
      options: {outline_fonts, keep_other_spots, spine_offset_mm, auto_detect_art_spine,
                min_dpi, min_font_pt, min_line_pt, preview_dpi, gs},
      proof: {details: [(label, value)], brand: {...}} | None,
    }
    """
    rep = Report()
    work = job["work_dir"]
    os.makedirs(work, exist_ok=True)
    opt = job.get("options") or {}
    gs = opt.get("gs")
    base = job.get("out_basename") or "artwork"
    W = lambda name: os.path.join(work, name)  # noqa: E731

    # ------------------------------------------------------------ template
    tcfg = job["template"]
    tgeo = normalise_page(tcfg["path"], int(tcfg.get("page") or 1) - 1, W("tpl_norm.pdf"))
    hint = geometry.box_centre(tgeo["trim"])[0] if tgeo["has_trim"] else tgeo["width"] / 2
    spine = geometry.find_spine(W("tpl_norm.pdf"), hint, tcfg.get("spine_width_mm"))
    tpl = geometry.resolve_template_geometry(tgeo, tcfg, spine)
    rep.info["template"] = {
        "page_mm": [round(mm(tpl["width"]), 2), round(mm(tpl["height"]), 2)],
        "trim_mm": [round(mm(tpl["trim"][2] - tpl["trim"][0]), 2), round(mm(tpl["trim"][3] - tpl["trim"][1]), 2)],
        "spine_centre_mm": round(mm(tpl["spine_centre"]), 2),
        "spine_width_mm": round(mm(tpl["spine_width"]), 2),
        "spine_source": tpl["spine_source"],
    }
    tspot = tcfg.get("spot_name") or "Template"
    run_mode(W("tpl_norm.pdf"), W("tpl_spot.pdf"), "spot", spot_name=tspot,
             spot_alt=tcfg.get("alt_cmyk") or (0, 1, 0, 0), threshold=0.01)

    # ------------------------------------------------------------ artwork
    bw, bh = tpl["bleed"][2] - tpl["bleed"][0], tpl["bleed"][3] - tpl["bleed"][1]
    art_pdf = to_pdf(job["art_path"], work, fit_size_pt=(bw, bh), gs=gs)
    npages = page_count(art_pdf)
    art_page = int(job.get("art_page") or 1)
    used_pages = {art_page} | {int(u.get("page") or 0) for u in job.get("upgrades", []) if u.get("source") == "Artwork page"}
    if npages > 1 and len(used_pages - {0}) < npages:
        rep.note("MULTI_PAGE", f"Artwork file has {npages} pages; only page(s) {sorted(used_pages - {0})} were used.")
    ageo = normalise_page(art_pdf, art_page - 1, W("art_norm.pdf"))

    # classify spot colours found in the artwork
    import pikepdf
    with pikepdf.open(W("art_norm.pdf")) as p:
        art_spots = list_spot_names(p)
    t_aliases = job.get("template_aliases") or DEFAULT_TEMPLATE_ALIASES
    die_spots = [s for s in art_spots if name_matches(s, t_aliases)]
    selected = job.get("upgrades") or []
    upgrade_spots: dict[int, list[str]] = {}
    unselected_upgrade_spots, other_spots = [], []
    for s in art_spots:
        if s in die_spots:
            continue
        hit = None
        for i, u in enumerate(selected):
            if u.get("source") == "Artwork spot" and name_matches(s, [u["spot_name"], *(u.get("aliases") or [])]):
                hit = i
                break
        if hit is not None:
            upgrade_spots.setdefault(hit, []).append(s)
        elif any(name_matches(s, [k["spot_name"], *(k.get("aliases") or [])]) for k in job.get("known_upgrades") or []):
            unselected_upgrade_spots.append(s)
        else:
            other_spots.append(s)
    rep.info["spots_in_artwork"] = art_spots
    if die_spots:
        rep.note("DIELINE_REMOVED", f"Customer dieline/template spot(s) {', '.join(die_spots)} removed from the printing art.")
    for s in unselected_upgrade_spots:
        rep.error("UNSELECTED_FINISH", f"Artwork contains finish spot '{s}' but that print upgrade is not selected on this "
                  "component. It was removed from the art – add the upgrade or confirm with the customer.")
    if other_spots:
        if opt.get("keep_other_spots"):
            rep.warn("SPOT_KEPT", f"Spot colour(s) kept as separate plates: {', '.join(other_spots)}.")
        else:
            rep.warn("SPOT_CONVERTED", f"Spot colour(s) converted to CMYK process: {', '.join(other_spots)}.")

    # ------------------------------------------------------------ alignment (spine)
    tw, th = tpl["trim"][2] - tpl["trim"][0], tpl["trim"][3] - tpl["trim"][1]
    trim_src = "TrimBox" if ageo["has_trim"] else "page"
    if die_spots:
        run_mode(W("art_norm.pdf"), W("art_die.pdf"), "extract", spot_name=tspot, targets=die_spots)
    if not ageo["has_trim"]:
        guess = geometry.infer_trim(W("art_die.pdf"), tw, th) if die_spots else None
        if guess:
            ageo["trim"], trim_src = guess, "customer dieline"
        else:
            b = tpl["bleed_pt"]
            if abs(ageo["width"] - (tw + 2 * b)) < 4.3 and abs(ageo["height"] - (th + 2 * b)) < 4.3:
                ageo["trim"], trim_src = [b, b, ageo["width"] - b, ageo["height"] - b], "page minus bleed"
    rep.info["artwork_trim_source"] = trim_src
    a_trim = ageo["trim"]
    art_cx, art_cy = geometry.box_centre(a_trim)
    method = f"artwork trim centre ({trim_src})"
    art_spine = None
    if die_spots and opt.get("auto_detect_art_spine", True):
        exp = mm(tpl["spine_width"]) if tpl["spine_width"] else None
        art_spine = geometry.find_spine(W("art_die.pdf"), art_cx, exp)
        if art_spine:
            art_cx = art_spine["centre"]
            method = "spine lines in customer's dieline"
    art_cx += pt(opt.get("spine_offset_mm") or 0)
    if opt.get("spine_offset_mm"):
        method += f" + manual offset {float(opt['spine_offset_mm']):+.2f}mm"
    t_cy = geometry.box_centre(tpl["trim"])[1]
    dx, dy = tpl["spine_centre"] - art_cx, t_cy - art_cy
    rep.info["alignment"] = {"method": method, "offset_x_mm": round(mm(dx), 2), "offset_y_mm": round(mm(dy), 2)}
    if art_spine and tpl["spine_width"] and abs(art_spine["width"] - tpl["spine_width"]) > pt(0.5):
        rep.warn("SPINE_WIDTH", f"Customer artwork spine is {mm(art_spine['width']):.1f}mm wide; the supplier template "
                 f"spine is {mm(tpl['spine_width']):.1f}mm. Spine content may not fit.")

    # ------------------------------------------------------------ preflight
    run_preflight(W("art_norm.pdf"), ageo, tpl, (dx, dy), rep, opt)

    # ------------------------------------------------------------ printing art
    strip = die_spots + unselected_upgrade_spots + [s for v in upgrade_spots.values() for s in v]
    if strip:
        run_mode(W("art_norm.pdf"), W("art_clean.pdf"), "remove", targets=strip)
    else:
        shutil.copy(W("art_norm.pdf"), W("art_clean.pdf"))
    col = job["colour"]
    limit = float(col.get("tac_limit") or 300)
    # Ghostscript: spots → CMYK alternates, fonts → outlines (colour values untouched)
    colour.flatten_for_print(W("art_clean.pdf"), W("art_flat.pdf"),
                             outline_fonts=bool(opt.get("outline_fonts")),
                             keep_spots=bool(opt.get("keep_other_spots")), gs=gs)
    before = colour.measure_tac(W("art_flat.pdf"), limit)
    # LittleCMS: RGB / Lab → output profile, then cap total ink
    stats = colour.convert_and_limit(W("art_flat.pdf"), W("art_final.pdf"), col["profile_path"], limit,
                                     intent=int(col.get("intent", 1)))
    after = colour.measure_tac(W("art_final.pdf"), limit)
    leftover = colour.residual_rgb(W("art_final.pdf"))
    if leftover:
        rep.warn("RGB_LEFT", f"{leftover} RGB/Lab element(s) could not be converted to {col.get('identifier')} "
                 "(usually gradient meshes). Check them in Acrobat Output Preview.")
    rep.info["ink"] = {"profile": col.get("identifier"), "limit": limit, "max_before": before["max"],
                       "max_after": after["max"], "fixups": stats}
    if before["max"] > limit + 1:
        rep.note("TAC_FIXED", f"Ink coverage reduced from {before['max']:.0f}% to {after['max']:.0f}% "
                 f"(limit {limit:.0f}% for {col.get('identifier')}).")
    if after["max"] > limit + 5 and after.get("over_fraction", 0) > 0.0005:
        rep.warn("TAC_HIGH", f"Some areas still reach {after['max']:.0f}% ink (limit {limit:.0f}%), usually from "
                 "transparency or overprint. Check in Acrobat Output Preview.")
    if opt.get("outline_fonts"):
        import pymupdf
        with pymupdf.open(W("art_final.pdf")) as d:
            left = [f[3] for f in d[0].get_fonts(full=True)]
        if left:
            rep.warn("OUTLINE_INCOMPLETE", f"{len(left)} font(s) could not be outlined: {', '.join(sorted(set(left))[:5])}")
        else:
            rep.note("OUTLINED", "All text converted to outlines.")

    # ------------------------------------------------------------ upgrade layers
    up_layers = []
    for i, u in enumerate(selected):
        label, sname = u.get("label") or u["spot_name"], u["spot_name"]
        out = W(f"up{i}.pdf")
        src_kind = u.get("source") or "Separate file"
        geo = ageo
        try:
            if src_kind == "Artwork spot":
                found = upgrade_spots.get(i)
                if not found:
                    rep.error("UPGRADE_MISSING", f"{label}: no matching spot colour found in the artwork "
                              f"(looked for {', '.join([sname, *(u.get('aliases') or [])][:6])}).")
                    continue
                run_mode(W("art_norm.pdf"), out, "extract", spot_name=sname,
                         spot_alt=u.get("alt_cmyk") or (0, 0, 0, 0.3), targets=found)
            else:
                if src_kind == "Artwork page":
                    src_pdf, pg = art_pdf, int(u.get("page") or 2)
                else:
                    if not u.get("file"):
                        rep.error("UPGRADE_MISSING", f"{label}: no mask file attached.")
                        continue
                    src_pdf, pg = to_pdf(u["file"], work, fit_size_pt=(ageo["width"], ageo["height"]), gs=gs), int(u.get("page") or 1)
                geo = normalise_page(src_pdf, pg - 1, W(f"up{i}_norm.pdf"))
                pre = W(f"up{i}_norm.pdf")
                if opt.get("outline_fonts"):
                    colour.outline_only(pre, W(f"up{i}_ol.pdf"), gs=gs)
                    pre = W(f"up{i}_ol.pdf")
                run_mode(pre, out, "spot", spot_name=sname, spot_alt=u.get("alt_cmyk") or (0, 0, 0, 0.3),
                         threshold=0.5)
        except EngineError as e:
            rep.error("UPGRADE_FAILED", f"{label}: {e}")
            continue
        off = (dx, dy)
        if abs(geo["width"] - ageo["width"]) > 1 or abs(geo["height"] - ageo["height"]) > 1:
            gx, gy = geometry.box_centre(geo["trim"])
            off = (tpl["spine_centre"] - gx, t_cy - gy)
            rep.warn("UPGRADE_SIZE", f"{label}: mask is {mm(geo['width']):.1f} × {mm(geo['height']):.1f}mm, artwork is "
                     f"{mm(ageo['width']):.1f} × {mm(ageo['height']):.1f}mm – centred on the spine, check position.")
        up_layers.append({"label": label, "spot": sname, "rgb": u.get("display_rgb") or "#00AEEF",
                          "opacity": float(u.get("opacity") or 0.55),
                          "layer": {"path": out, "offset": off, "label": f"{label} (spot: {sname})",
                                    "overprint": True, "clip": tpl["bleed"]}})

    # ------------------------------------------------------------ compose
    art_layer = {"path": W("art_final.pdf"), "offset": (dx, dy), "label": "Artwork (CMYK)", "clip": tpl["bleed"]}
    tpl_layer = {"path": W("tpl_spot.pdf"), "offset": (0, 0), "label": f"{tspot} (spot, non-printing)", "overprint": True}
    print_pdf = os.path.join(work, f"{base}_print.pdf")
    compose(print_pdf, tpl, [art_layer, *[u["layer"] for u in up_layers], tpl_layer],
            output_intent={"profile_path": col["profile_path"], "identifier": col.get("identifier") or "CMYK",
                           "info": col.get("identifier") or ""},
            title=base)

    preview_png = os.path.join(work, f"{base}_preview.png")
    tpl_prev = {"label": tspot, "spot": tspot, "rgb": tcfg.get("display_rgb") or "#E6007E", "opacity": 1.0,
                "layer": tpl_layer}
    coverage = render_preview(tpl, art_layer, [*up_layers, tpl_prev], preview_png, dpi=int(opt.get("preview_dpi") or 110))
    for u in up_layers:
        if coverage.get(u["spot"], 1) < 0.0001:
            rep.error("UPGRADE_EMPTY", f"{u['label']} layer is empty – nothing would be applied.")
    rep.info["coverage"] = coverage

    result = {"print_pdf": print_pdf, "preview_png": preview_png, **rep.as_dict()}
    if job.get("proof") is not None:
        proof_pdf = os.path.join(work, f"{base}_proof.pdf")
        legend = [("Artwork (CMYK)", "#FFFFFF")] + [(u["label"], u["rgb"]) for u in up_layers] + \
                 [(f"{tspot} – position only, not printed", tpl_prev["rgb"])]
        details = list(job["proof"].get("details") or [])
        details += [("Colour profile", f"{col.get('identifier')} (max {limit:.0f}% ink)"),
                    ("Fonts", "Converted to outlines" if opt.get("outline_fonts") else "Embedded")]
        proof_sheet(proof_pdf, preview_png, details, result["warnings"], legend, job["proof"].get("brand"))
        result["proof_pdf"] = proof_pdf
    return result
