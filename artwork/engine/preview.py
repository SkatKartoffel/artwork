"""Screen preview (PNG) and customer proof sheet (PDF)."""
from __future__ import annotations

import os
import tempfile

import numpy as np

from .compose import compose
from .util import mm


def _hex_rgb(h, default=(0, 0, 0)):
    try:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except Exception:
        return default


def _render(pdf_path, dpi, gray=False):
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        pix = doc[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB, alpha=False)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return a.astype(np.float32) / 255.0


def render_preview(tpl, art_layer, spot_layers, out_png, dpi=110):
    """spot_layers: [{layer(dict for compose), spot, rgb(hex), opacity}] painted in order."""
    from PIL import Image

    coverage = {}
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "art.pdf")
        compose(p, tpl, [art_layer])
        img = _render(p, dpi)
        for i, sl in enumerate(spot_layers):
            q = os.path.join(tmp, f"spot{i}.pdf")
            compose(q, tpl, [dict(sl["layer"], overprint=False)], gray_spots=[sl["spot"]])
            g = _render(q, dpi, gray=True)[..., 0]
            if g.shape != img.shape[:2]:
                continue
            coverage[sl["spot"]] = float(round(((1.0 - g) > 0.5).mean(), 6))
            cov = (1.0 - g)[..., None] * float(sl.get("opacity", 0.6))
            col = np.array(_hex_rgb(sl.get("rgb", "#00AEEF")), dtype=np.float32)
            img = img * (1 - cov) + col * cov
    Image.fromarray(np.clip(img * 255, 0, 255).astype(np.uint8)).save(out_png, optimize=True)
    return coverage


def clean(t):
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"), ("–", "-"), ("—", "-"),
                 ("×", "x"), ("·", "-"), ("…", "...")):
        t = t.replace(a, b)
    return t.encode("latin-1", "replace").decode("latin-1")

def wrap(t, width, size, font="helv"):
    import pymupdf

    words, lines, cur = clean(t).split(), [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if pymupdf.get_text_length(cand, fontname=font, fontsize=size) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or ["-"]



def proof_sheet(out_pdf, preview_png, details: list[tuple[str, str]], warnings: list[dict],
                legend: list[tuple[str, str]], brand: dict | None = None):
    """A3-landscape customer proof: preview, job details, layer legend, warnings."""
    import pymupdf

    brand = brand or {}
    ink = _hex_rgb(brand.get("colour", "#111111"))
    W, H = 1190.55, 841.89
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    M = 28
    # header bar
    page.draw_rect(pymupdf.Rect(0, 0, W, 64), color=None, fill=ink)
    logo = brand.get("logo_path")
    x_title = M
    if logo and os.path.exists(logo):
        try:
            page.insert_image(pymupdf.Rect(M, 12, M + 160, 52), filename=logo, keep_proportion=True)
            x_title = M + 175
        except Exception:
            pass
    page.insert_text((x_title, 42), (brand.get("company") or "PACKAGED SOUNDS").upper() + "  ·  ARTWORK PROOF",
                     fontsize=20, fontname="hebo", color=(1, 1, 1))
    ref = clean(str(dict(details).get("Proof", "")))
    if ref:
        page.insert_text((W - M - 260, 42), ref, fontsize=13, fontname="hebo", color=(1, 1, 1))

    # preview (left)
    side_w = 330
    area = pymupdf.Rect(M, 84, W - side_w - 2 * M, H - 70)
    page.draw_rect(area, color=(0.85, 0.85, 0.85), fill=(0.96, 0.96, 0.96), width=0.5)
    page.insert_image(area + (8, 8, -8, -8), filename=preview_png, keep_proportion=True)

    # right column
    x0 = W - side_w - M
    y = 92

    def heading(t):
        nonlocal y
        page.insert_text((x0, y), t.upper(), fontsize=10, fontname="hebo", color=ink)
        y += 6
        page.draw_line((x0, y), (W - M, y), color=(0.8, 0.8, 0.8), width=0.5)
        y += 13

    def row(label, value, size=8.5):
        nonlocal y
        page.insert_text((x0, y), clean(label), fontsize=size, fontname="hebo", color=(0.35, 0.35, 0.35))
        for ln in wrap(str(value or "-"), W - M - x0 - 105, size):
            page.insert_text((x0 + 105, y), ln, fontsize=size, fontname="helv")
            y += size * 1.3
        y += 3

    heading("Job")
    for k, v in details:
        row(k, v)
    y += 6
    heading("Layers")
    for label, hexcol in legend:
        page.draw_rect(pymupdf.Rect(x0, y - 8, x0 + 12, y + 2), color=(0.5, 0.5, 0.5), fill=_hex_rgb(hexcol), width=0.3)
        page.insert_text((x0 + 18, y), clean(label), fontsize=8.5, fontname="helv")
        y += 14
    y += 6
    heading(f"Preflight ({sum(1 for w in warnings if w['severity'] != 'Info')} issue(s))")
    sev_col = {"Error": (0.8, 0.1, 0.1), "Warning": (0.85, 0.5, 0.0), "Info": (0.3, 0.45, 0.6)}
    if not warnings:
        page.insert_text((x0, y), "No issues found.", fontsize=8.5, fontname="helv")
        y += 12
    for w in warnings:
        if y > H - 120:
            page.insert_text((x0, y), "… more in the job record", fontsize=8, fontname="helv")
            y += 12
            break
        page.draw_circle((x0 + 4, y - 3), 3.2, color=None, fill=sev_col.get(w["severity"], (0, 0, 0)))
        for ln in wrap(f"{w['severity']}: {w['message']}", W - M - x0 - 12, 8):
            page.insert_text((x0 + 12, y), ln, fontsize=8, fontname="helv")
            y += 10.5
        y += 3

    # footer
    foot = ("Please check this proof carefully: text, spelling, layout, barcodes and catalogue numbers. "
            "On-screen and printed proofs are not colour accurate. Coloured overlays show special finishes "
            "and are not printed as shown. The template (dieline) layer is for position only and will not print. "
            "Production starts only after written approval.")
    yy = H - 52
    for ln in wrap(foot, W - 2 * M, 8):
        page.insert_text((M, yy), ln, fontsize=8, fontname="helv", color=(0.3, 0.3, 0.3))
        yy += 10.5
    doc.save(out_pdf, deflate=True)
    return out_pdf


__all__ = ["render_preview", "proof_sheet", "mm"]
