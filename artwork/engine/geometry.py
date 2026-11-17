"""Template / spine geometry.

All coordinates here are PDF points with origin bottom-left on a normalised page.
"""
from __future__ import annotations

from .util import pt


def vertical_lines(pdf_path: str, min_len_frac: float = 0.25):
    """Return [(x, length)] of long vertical strokes on page 1."""
    import pymupdf

    out = []
    with pymupdf.open(pdf_path) as doc:
        page = doc[0]
        H = page.rect.height
        min_len = H * min_len_frac
        for d in page.get_drawings():
            for it in d.get("items", []):
                kind = it[0]
                if kind == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.x - p2.x) < 0.35 and abs(p1.y - p2.y) >= min_len:
                        out.append(((p1.x + p2.x) / 2, abs(p1.y - p2.y)))
                elif kind == "re":
                    r = it[1]
                    if r.height >= min_len:
                        if r.width < 0.75:  # hairline drawn as a thin filled rect
                            out.append((r.x0 + r.width / 2, r.height))
                        elif d.get("type") in ("s", "fs") or d.get("color") is not None:
                            out.append((r.x0, r.height))
                            out.append((r.x1, r.height))
                elif kind == "qu":
                    q = it[1]
                    for a, b in ((q.ul, q.ll), (q.ur, q.lr)):
                        if abs(a.x - b.x) < 0.35 and abs(a.y - b.y) >= min_len:
                            out.append(((a.x + b.x) / 2, abs(a.y - b.y)))
    return out


def horizontal_lines(pdf_path: str, min_len_frac: float = 0.25):
    """Return [(y_pdf, length)] of long horizontal strokes on page 1 (PDF y-up coords)."""
    import pymupdf

    out = []
    with pymupdf.open(pdf_path) as doc:
        page = doc[0]
        Wd, Hd = page.rect.width, page.rect.height
        min_len = Wd * min_len_frac
        for d in page.get_drawings():
            for it in d.get("items", []):
                if it[0] == "l":
                    p1, p2 = it[1], it[2]
                    if abs(p1.y - p2.y) < 0.35 and abs(p1.x - p2.x) >= min_len:
                        out.append((Hd - (p1.y + p2.y) / 2, abs(p1.x - p2.x)))
                elif it[0] == "re":
                    r = it[1]
                    if r.width >= min_len:
                        if r.height < 0.75:
                            out.append((Hd - (r.y0 + r.height / 2), r.width))
                        else:
                            out.append((Hd - r.y0, r.width))
                            out.append((Hd - r.y1, r.width))
    return out


def infer_trim(pdf_path: str, trim_w: float, trim_h: float, tol: float = 8.5):
    """Find the rectangle in a dieline whose size best matches the template trim (pt)."""
    xs = [c["x"] for c in cluster(vertical_lines(pdf_path))]
    ys = [c["x"] for c in cluster(horizontal_lines(pdf_path))]

    def best_pair(vals, target):
        best = None
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                err = abs((vals[j] - vals[i]) - target)
                if err <= tol and (best is None or err < best[0]):
                    best = (err, vals[i], vals[j])
        return best

    bx, by = best_pair(sorted(xs), trim_w), best_pair(sorted(ys), trim_h)
    if bx and by:
        return [bx[1], by[1], bx[2], by[2]]
    return None


def cluster(lines, tol=0.75):
    lines = sorted(lines)
    clusters = []
    for x, ln in lines:
        if clusters and x - clusters[-1]["x"] <= tol:
            c = clusters[-1]
            c["xs"].append(x)
            c["x"] = sum(c["xs"]) / len(c["xs"])
            c["len"] = max(c["len"], ln)
        else:
            clusters.append({"x": x, "xs": [x], "len": ln})
    return clusters


def find_spine(pdf_path: str, centre_hint_pt: float, expected_width_mm: float | None = None,
               min_mm: float = 1.0, max_mm: float = 60.0):
    """Find the pair of fold lines forming the spine.

    Picks long vertical lines, then the pair whose midpoint is closest to the
    centre hint (and whose gap matches the expected spine width when given).
    Returns dict(centre, left, right, width) in points, or None.
    """
    cl = cluster(vertical_lines(pdf_path))
    if len(cl) < 2:
        return None
    longest = max(c["len"] for c in cl)
    cl = [c for c in cl if c["len"] >= 0.85 * longest] or cl
    best = None
    for i in range(len(cl)):
        for j in range(i + 1, len(cl)):
            a, b = cl[i]["x"], cl[j]["x"]
            gap = b - a
            if not (pt(min_mm) <= gap <= pt(max_mm)):
                continue
            mid = (a + b) / 2
            score = abs(mid - centre_hint_pt)
            if expected_width_mm:
                score += 3 * abs(gap - pt(expected_width_mm))
            else:
                score += 0.05 * gap  # prefer the innermost plausible pair
            if best is None or score < best[0]:
                best = (score, a, b)
    if not best:
        return None
    _, a, b = best
    return {"centre": (a + b) / 2, "left": a, "right": b, "width": b - a}


def box_centre(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def resolve_template_geometry(tgeo: dict, cfg: dict, spine: dict | None) -> dict:
    """Combine the template PDF boxes, detected spine and the template record.

    cfg keys (all optional, mm): spine_centre_mm (from left page edge),
    spine_width_mm, trim_width_mm, trim_height_mm, bleed_mm, safe_margin_mm.
    """
    W, H = tgeo["width"], tgeo["height"]
    bleed = pt(cfg.get("bleed_mm") or 3)
    if cfg.get("spine_centre_mm"):
        sx = pt(cfg["spine_centre_mm"])
        src = "template record"
    elif spine:
        sx = spine["centre"]
        src = "detected from template lines"
    elif tgeo.get("has_trim"):
        sx = box_centre(tgeo["trim"])[0]
        src = "template TrimBox centre"
    else:
        sx = W / 2
        src = "template page centre"
    spine_w = pt(cfg["spine_width_mm"]) if cfg.get("spine_width_mm") else (spine["width"] if spine else 0)

    if cfg.get("trim_width_mm") and cfg.get("trim_height_mm"):
        tw, th = pt(cfg["trim_width_mm"]), pt(cfg["trim_height_mm"])
        cy = box_centre(tgeo["trim"])[1] if tgeo.get("has_trim") else H / 2
        trim = [sx - tw / 2, cy - th / 2, sx + tw / 2, cy + th / 2]
    elif tgeo.get("has_trim"):
        trim = list(tgeo["trim"])
    else:
        trim = [bleed, bleed, W - bleed, H - bleed]
    bl = [max(0, trim[0] - bleed), max(0, trim[1] - bleed), min(W, trim[2] + bleed), min(H, trim[3] + bleed)]
    return {
        "width": W, "height": H, "trim": trim, "bleed": bl, "bleed_pt": bleed,
        "spine_centre": sx, "spine_width": spine_w, "spine_source": src,
        "safe_pt": pt(cfg.get("safe_margin_mm") or 3),
    }
