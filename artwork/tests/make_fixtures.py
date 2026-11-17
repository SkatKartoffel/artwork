"""Generate synthetic sleeve artwork for engine tests (python3 make_fixtures.py OUTDIR)."""
import os
import sys

import numpy as np
import pikepdf
import pymupdf

MM = 72 / 25.4
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def add_raw(path, ops: bytes, colorspaces=None):
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    pg = pdf.pages[0]
    if colorspaces:
        res = pg.obj.Resources
        if "/ColorSpace" not in res:
            res.ColorSpace = pikepdf.Dictionary()
        for k, (name, alt) in colorspaces.items():
            fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=list(alt), N=1)
            res.ColorSpace[k] = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/" + name),
                                               pikepdf.Name.DeviceCMYK, pdf.make_indirect(fn)])
    pg.contents_add(pikepdf.Stream(pdf, ops))
    pdf.save(path)


def template(out):
    # 12" wide-spine jacket: back 314 + spine 5 + front 314 = 633 x 314mm, template page 653 x 334
    W, H = 653 * MM, 334 * MM
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    tx0, ty0 = 10 * MM, 10 * MM
    trim = pymupdf.Rect(tx0, ty0, tx0 + 633 * MM, ty0 + 314 * MM)
    p.draw_rect(trim + (-3 * MM, -3 * MM, 3 * MM, 3 * MM), color=(0, 1, 1), width=0.5, dashes="[4 2] 0")  # bleed
    p.draw_rect(trim, color=(1, 0, 1), width=0.5)  # trim
    for x in (314, 319):
        X = tx0 + x * MM
        p.draw_line((X, ty0), (X, ty0 + 314 * MM), color=(1, 0, 1), width=0.5)  # spine folds
    for r in (pymupdf.Rect(tx0 + 3 * MM, ty0 + 3 * MM, tx0 + 311 * MM, ty0 + 311 * MM),
              pymupdf.Rect(tx0 + 322 * MM, ty0 + 3 * MM, tx0 + 630 * MM, ty0 + 311 * MM)):
        p.draw_rect(r, color=(0, 0.6, 0), width=0.3, dashes="[2 2] 0")  # safe area
    p.insert_text((tx0 + 140 * MM, ty0 + 160 * MM), "BACK", fontsize=30, color=(1, 0, 1))
    p.insert_text((tx0 + 460 * MM, ty0 + 160 * MM), "FRONT", fontsize=30, color=(1, 0, 1))
    p.insert_text((10, 20), "SUPPLIER X – 12in WIDE SPINE 5mm – TEMPLATE", fontsize=12, color=(0, 0, 0))
    doc.save(out)


def artwork(out):
    # customer artboard: trim 633x314 + 3mm bleed, but with 12mm extra slug on the right and no TrimBox,
    # so only the customer's own 'Dieline' spot reveals where the spine is.
    W, H = (639 + 12) * MM, 320 * MM
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    p.insert_font(fontname="dj", fontfile=FONT)
    # RGB background gradient image, deliberately low res (~100 dpi)
    h_px, w_px = 126, 252
    yy, xx = np.mgrid[0:h_px, 0:w_px]
    img = np.stack([xx / w_px * 255, 80 + yy / h_px * 120, 200 - xx / w_px * 150], -1).astype(np.uint8)
    pix = pymupdf.Pixmap(pymupdf.csRGB, w_px, h_px, img.tobytes(), False)
    p.insert_image(pymupdf.Rect(0, 0, 639 * MM / 2, H), pixmap=pix)
    # hi-res RGB photo on the front (~400dpi)
    big = (np.random.default_rng(1).random((800, 800, 3)) * 255).astype(np.uint8)
    p.insert_image(pymupdf.Rect(370 * MM, 60 * MM, 420 * MM, 110 * MM), pixmap=pymupdf.Pixmap(pymupdf.csRGB, 800, 800, big.tobytes(), False))
    # text
    p.insert_text((370 * MM, 140 * MM), "BAND NAME", fontsize=48, fontname="dj", color=(1, 1, 1))
    p.insert_text((40 * MM, 290 * MM), "© 2026 Label · Manufactured in the UK", fontsize=4, fontname="dj", color=(0, 0, 0))
    p.insert_text((2 * MM, 160 * MM), "EDGE TEXT", fontsize=10, fontname="dj", color=(0, 0, 0))
    p.insert_text((313 * MM, 200 * MM), "CROSSING", fontsize=9, fontname="dj", color=(0, 0, 0))
    p.draw_line((30 * MM, 250 * MM), (200 * MM, 250 * MM), color=(0, 0, 0), width=0.1)  # hairline
    doc.save(out)
    T = 3 * MM  # trim origin inside the artboard
    spine_l, spine_r = T + 314 * MM, T + 319 * MM
    ops = []
    ops.append(b"q 1 1 1 1 k %.2f %.2f %.2f %.2f re f Q" % (spine_l, 0, 5 * MM, H))  # 400% rich black spine
    ops.append(b"q 0.8 0.7 0.7 0.9 k %.2f %.2f %.2f %.2f re f Q" % (380 * MM, 20 * MM, 120 * MM, 25 * MM))  # 310%
    ops.append(b"q /CSp cs 1 scn %.2f %.2f %.2f %.2f re f Q" % (450 * MM, 200 * MM, 60 * MM, 60 * MM))  # Pantone
    ops.append(b"q /CSuv cs 1 scn %.2f %.2f %.2f %.2f re f Q" % (370 * MM, 60 * MM, 50 * MM, 50 * MM))  # spot UV over photo
    # customer's own dieline (spot) incl. spine folds, trim rect
    ops.append(b"q /CSd CS 1 SCN 0.5 w %.2f %.2f %.2f %.2f re S %.2f 0 m %.2f %.2f l S %.2f 0 m %.2f %.2f l S Q"
               % (T, T, 633 * MM, 314 * MM, spine_l, spine_l, H, spine_r, spine_r, H))
    add_raw(out, b"\n".join(ops), {"/CSp": ("PANTONE 021 C", (0, 0.53, 1, 0)),
                                   "/CSuv": ("Spot UV", (0.2, 0, 0, 0)),
                                   "/CSd": ("Dieline", (0, 1, 0, 0))})


def foil_mask(out):
    W, H = (639 + 12) * MM, 320 * MM
    doc = pymupdf.open()
    p = doc.new_page(width=W, height=H)
    p.insert_font(fontname="dj", fontfile=FONT)
    p.insert_text((370 * MM, 140 * MM), "BAND NAME", fontsize=48, fontname="dj", color=(0, 0, 0))
    p.draw_rect(pymupdf.Rect(30 * MM, 30 * MM, 120 * MM, 60 * MM), color=None, fill=(0, 0, 0))
    # raster logo in the mask -> must become a stencil
    a = np.full((200, 200), 255, np.uint8)
    yy, xx = np.mgrid[0:200, 0:200]
    a[(xx - 100) ** 2 + (yy - 100) ** 2 < 80 ** 2] = 0
    p.insert_image(pymupdf.Rect(150 * MM, 30 * MM, 190 * MM, 70 * MM), pixmap=pymupdf.Pixmap(pymupdf.csGRAY, 200, 200, a.tobytes(), False))
    doc.save(out)


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
    os.makedirs(d, exist_ok=True)
    template(os.path.join(d, "template.pdf"))
    artwork(os.path.join(d, "artwork.pdf"))
    foil_mask(os.path.join(d, "foil_mask.pdf"))
    print("fixtures in", d)
