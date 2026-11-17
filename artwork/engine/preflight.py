"""Preflight checks on the customer's artwork (run before outlining / conversion)."""
from __future__ import annotations

import math

from .util import Report, mm


def run_preflight(art_pdf: str, art_geo: dict, tpl: dict, placement: tuple[float, float],
                  report: Report, settings: dict):
    import pymupdf

    min_dpi = float(settings.get("min_dpi") or 300)
    min_font = float(settings.get("min_font_pt") or 5)
    min_line = float(settings.get("min_line_pt") or 0.25)
    dx, dy = placement
    H = art_geo["height"]

    def to_tpl(r):  # pymupdf rect (y-down, art space) → template PDF coords
        return (r.x0 + dx, H - r.y1 + dy, r.x1 + dx, H - r.y0 + dy)

    bleed = tpl["bleed"]
    trim = tpl["trim"]
    safe = tpl["safe_pt"]

    def visible(b):
        return not (b[2] < bleed[0] or b[0] > bleed[2] or b[3] < bleed[1] or b[1] > bleed[3])

    with pymupdf.open(art_pdf) as doc:
        page = doc[0]

        # ---------------- fonts
        fonts = page.get_fonts(full=True)
        report.info["fonts"] = sorted({f[3] for f in fonts if f[3]})
        missing = sorted({f[3] for f in fonts if f[1] in ("n/a", "", None)})
        for fname in missing:
            report.error("FONT_NOT_EMBEDDED", f"Font '{fname}' is not embedded – the printer would substitute it. "
                         "Ask the customer to re-export with fonts embedded or outlined.")

        # ---------------- text: size, folds, safe zone
        small, fold_cross, near_edge = [], [], []
        folds = []
        if tpl.get("spine_width"):
            sx, sw = tpl["spine_centre"], tpl["spine_width"]
            folds = [sx - sw / 2, sx + sw / 2]
        safe_rect = (trim[0] + safe, trim[1] + safe, trim[2] - safe, trim[3] - safe)
        td = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
        for block in td.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if not txt:
                        continue
                    b = to_tpl(pymupdf.Rect(span["bbox"]))
                    if not visible(b):
                        continue
                    size = float(span.get("size") or 0)
                    if 0 < size < min_font - 0.01:
                        small.append((size, txt))
                    if any(b[0] + 0.5 < f < b[2] - 0.5 for f in folds):
                        fold_cross.append(txt)
                    elif (b[0] < safe_rect[0] or b[2] > safe_rect[2] or b[1] < safe_rect[1] or b[3] > safe_rect[3]):
                        near_edge.append(txt)
        if small:
            smallest = min(s for s, _ in small)
            sample = ", ".join(f"“{t[:25]}” ({s:.1f}pt)" for s, t in sorted(small)[:4])
            report.warn("FONT_TOO_SMALL", f"{len(small)} text item(s) below {min_font:g}pt (smallest {smallest:.1f}pt): {sample}")
        if fold_cross:
            report.warn("TEXT_CROSSES_FOLD", f"{len(fold_cross)} text item(s) cross a spine fold, e.g. “{fold_cross[0][:30]}”")
        if near_edge:
            report.warn("TEXT_OUTSIDE_SAFE", f"{len(near_edge)} text item(s) sit within {mm(safe):.0f}mm of the trim edge "
                        f"(or in the bleed), e.g. “{near_edge[0][:30]}”")

        # ---------------- images: effective resolution
        low, very_low, rgb_imgs = [], [], 0
        lowest = None
        for im in page.get_image_info(xrefs=True):
            a, b, c, d, _, _ = im.get("transform", (1, 0, 0, 1, 0, 0))
            disp_w, disp_h = math.hypot(a, b), math.hypot(c, d)
            if disp_w < 8.5 and disp_h < 8.5:  # < 3mm: ignore specks
                continue
            bb = to_tpl(pymupdf.Rect(im["bbox"]))
            if not visible(bb):
                continue
            ppi = min(im["width"] / (disp_w / 72), im["height"] / (disp_h / 72))
            if im.get("colorspace") == 3:
                rgb_imgs += 1
            lowest = ppi if lowest is None else min(lowest, ppi)
            if ppi < min_dpi / 2:
                very_low.append(ppi)
            elif ppi < min_dpi - 0.5:
                low.append(ppi)
        if very_low:
            report.error("IMAGE_LOW_RES", f"{len(very_low)} image(s) are well below {min_dpi:.0f}dpi "
                         f"(lowest {min(very_low):.0f}dpi) – will print soft/pixelated.")
        if low:
            report.warn("IMAGE_LOW_RES", f"Artwork is less than {min_dpi:.0f}dpi resolution: {len(low)} image(s) "
                        f"between {min(low):.0f} and {max(low):.0f}dpi.")
        if lowest is not None:
            report.info["lowest_image_dpi"] = round(lowest)
        if rgb_imgs:
            report.note("RGB_IMAGES", f"{rgb_imgs} RGB image(s) converted to CMYK.")

        # ---------------- hairlines
        thin = 0
        for dr in page.get_drawings():
            if dr.get("type") in ("s", "fs"):
                w = dr.get("width")
                if w is not None and w < min_line:
                    if visible(to_tpl(dr["rect"])):
                        thin += 1
        if thin:
            report.warn("HAIRLINES", f"{thin} line(s) thinner than {min_line:g}pt may not print reliably.")

    # ---------------- size & bleed against the template
    a_trim = art_geo["trim"]
    aw, ah = a_trim[2] - a_trim[0], a_trim[3] - a_trim[1]
    tw, th = trim[2] - trim[0], trim[3] - trim[1]
    if abs(aw - tw) > 2.84 or abs(ah - th) > 2.84:  # > 1mm
        report.warn("SIZE_MISMATCH", f"Artwork trim is {mm(aw):.1f} × {mm(ah):.1f}mm but the template trim is "
                    f"{mm(tw):.1f} × {mm(th):.1f}mm – check the correct template / product was chosen.")
    art_ext = art_geo["bleed"] if art_geo.get("has_trim") else [0, 0, art_geo["width"], art_geo["height"]]
    ext = (art_ext[0] + dx, art_ext[1] + dy, art_ext[2] + dx, art_ext[3] + dy)
    short = {
        "left": bleed[0] - ext[0], "bottom": bleed[1] - ext[1],
        "right": ext[2] - bleed[2], "top": ext[3] - bleed[3],
    }
    missing_sides = {k: -v for k, v in short.items() if v < -0.85}  # > 0.3mm short
    if missing_sides:
        need = mm(tpl["bleed_pt"])
        parts = ", ".join(f"{k} {mm(v):.1f}mm short" for k, v in missing_sides.items())
        sev = "Error" if any(mm(v) >= need - 0.3 for v in missing_sides.values()) else "Warning"
        report.add(sev, "BLEED", f"Artwork does not fill the {need:.0f}mm bleed ({parts}).")
