"""Turn whatever the customer sent into clean single-page PDFs.

Every normalised page has MediaBox [0 0 W H], no /Rotate, and its original
content wrapped in one Form XObject.  Trim/Bleed boxes are carried across in
the new coordinate system, so the rest of the engine never has to think about
rotation, CropBoxes or odd origins.
"""
from __future__ import annotations

import os

import pikepdf
from pikepdf import Array, Dictionary, Name

from .util import EngineError, pt, run_gs

RASTER_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".psd"}


def to_pdf(src: str, work: str, fit_size_pt: tuple[float, float] | None = None, gs=None) -> str:
    """Return a PDF path for any supported input file."""
    ext = os.path.splitext(src)[1].lower()
    if ext == ".pdf":
        return src
    if ext == ".ai":
        try:
            pikepdf.open(src).close()  # PDF-compatible Illustrator file
            return src
        except Exception:
            pass
    if ext in (".eps", ".ps", ".ai"):
        dst = os.path.join(work, "converted_eps.pdf")
        run_gs(["-sDEVICE=pdfwrite", "-dEPSCrop", "-dAutoRotatePages=/None",
                f"--permit-file-read={os.path.dirname(os.path.abspath(src))}/", "-o", dst, src], gs)
        return dst
    if ext in RASTER_EXT:
        return _raster_to_pdf(src, work, fit_size_pt)
    raise EngineError(f"Unsupported artwork file type '{ext}'. Supply PDF, AI, EPS, TIFF, JPG or PNG.")


def _raster_to_pdf(src, work, fit_size_pt):
    import pymupdf

    pix = pymupdf.Pixmap(src)
    w_px, h_px = pix.width, pix.height
    dpi = pix.xres if pix.xres and pix.xres >= 150 else None
    if dpi:
        w_pt, h_pt = w_px / dpi * 72, h_px / dpi * 72
    elif fit_size_pt:
        scale = max(fit_size_pt[0] / w_px, fit_size_pt[1] / h_px)
        w_pt, h_pt = w_px * scale, h_px * scale
    else:
        w_pt, h_pt = w_px / 300 * 72, h_px / 300 * 72
    doc = pymupdf.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    page.insert_image(page.rect, filename=src)
    dst = os.path.join(work, "converted_raster.pdf")
    doc.save(dst)
    return dst


def page_count(path: str) -> int:
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def _inherited(obj, key):
    node = obj
    while node is not None:
        if key in node:
            return node[key]
        node = node.get("/Parent")
    return None


def _box(v):
    a = [float(x) for x in v]
    return [min(a[0], a[2]), min(a[1], a[3]), max(a[0], a[2]), max(a[1], a[3])]


_PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "Tj", "TJ", "'", '"', "Do", "sh", "INLINE"}


def _ocg_state(spdf):
    """-> (names bottom-to-top as listed, set of names that are off by default)"""
    ocp = spdf.Root.get("/OCProperties")
    if ocp is None or not len(ocp.get("/OCGs", [])):
        return [], set()
    d = ocp.get("/D", {})
    off = {o.objgen for o in (d.get("/OFF") or [])}
    if d.get("/BaseState") == Name.OFF:
        on = {o.objgen for o in (d.get("/ON") or [])}
        off |= {o.objgen for o in ocp.OCGs if o.objgen not in on}
    order = [o for o in (d.get("/Order") or []) if isinstance(o, Dictionary)] or list(ocp.OCGs)
    listed = {o.objgen for o in order}
    order += [o for o in ocp.OCGs if o.objgen not in listed]
    names = [str(o.get("/Name")) for o in order]           # top of the layer panel first
    return names[::-1], {str(o.get("/Name")) for o in ocp.OCGs if o.objgen in off}


def _filter_layers(spdf, out, res, data: bytes, only_layer: str | None = None) -> bytes:
    """only_layer=None: drop content on layers (OCGs) that are switched off by default, e.g. a template's
    hidden notes layer.  only_layer="Guides": keep just that layer's content (plus graphics state set outside it)."""
    try:
        _, hidden_names = _ocg_state(spdf)
        if only_layer is None and not hidden_names:
            return data
        props = (res.get("/Properties") or {}) if res is not None else {}
        layer_of = {str(k): str(v.get("/Name")) for k, v in props.items() if isinstance(v, Dictionary) and "/Name" in v}
        tmp = out.make_stream(data)
        keep, depth, cur, cur_depth = [], 0, None, None
        for ins in pikepdf.parse_content_stream(tmp):
            op = str(ins.operator) if not isinstance(ins, pikepdf.ContentStreamInlineImage) else "INLINE"
            if op in ("BDC", "BMC"):
                depth += 1
                if cur_depth is None and op == "BDC" and len(ins.operands) == 2 and ins.operands[0] == Name.OC \
                        and str(ins.operands[1]) in layer_of:
                    cur, cur_depth = layer_of[str(ins.operands[1])], depth
                    continue
            elif op == "EMC":
                if cur_depth is not None and depth == cur_depth:
                    cur, cur_depth = None, None
                    depth -= 1
                    continue
                depth -= 1
            if only_layer is None:
                if cur is None or cur not in hidden_names:
                    keep.append(ins)
            elif cur == only_layer or (cur is None and op not in _PAINT_OPS):
                keep.append(ins)
        return pikepdf.unparse_content_stream(keep)
    except Exception:
        return data if only_layer is None else b""


def template_layers(src: str, page_index: int = 0) -> list[dict]:
    """Layers of a template that can be carried into the print file as they are: [{name, on}] in the
    template's layer-panel order (top first).  Empty when the template has no layers, or has anything outside
    them or in a printing (process) colour - such templates are flattened to one non-printing spot instead."""
    try:
        with pikepdf.open(src) as spdf:
            names, hidden = _ocg_state(spdf)
            if not names:
                return []
            page = spdf.pages[page_index]
            props = page.obj.get("/Resources", Dictionary()).get("/Properties") or {}
            layer_of = {str(k): str(v.get("/Name")) for k, v in props.items() if isinstance(v, Dictionary) and "/Name" in v}
            depth, cur_depth = 0, None
            for ins in pikepdf.parse_content_stream(page):
                op = str(ins.operator) if not isinstance(ins, pikepdf.ContentStreamInlineImage) else "INLINE"
                if op in ("BDC", "BMC"):
                    depth += 1
                    if cur_depth is None and op == "BDC" and len(ins.operands) == 2 and ins.operands[0] == Name.OC \
                            and str(ins.operands[1]) in layer_of:
                        cur_depth = depth
                elif op == "EMC":
                    if cur_depth == depth:
                        cur_depth = None
                    depth -= 1
                elif op in _PAINT_OPS and (cur_depth is None or op in ("Do", "sh", "INLINE")):
                    return []
                elif op in ("k", "K", "g", "G", "rg", "RG"):
                    v = [float(x) for x in ins.operands]
                    white = (op in ("k", "K") and not any(v)) or (op in ("g", "G", "rg", "RG") and all(x == 1 for x in v))
                    if not white:
                        return []
                elif op in ("cs", "CS") and str(ins.operands[0]) in ("/DeviceCMYK", "/DeviceRGB", "/DeviceGray"):
                    return []
            return [{"name": n, "on": n not in hidden} for n in names[::-1]]
    except Exception:
        return []


def normalise_page(src: str, page_index: int, dst: str, only_layer: str | None = None) -> dict:
    """Write page `page_index` of `src` to `dst` as an upright page at origin.

    Returns geometry {width, height, trim:[x0,y0,x1,y1], bleed:[...], has_trim}.
    """
    spdf = pikepdf.open(src)  # keep open until save: foreign stream data is copied lazily
    if True:
        if page_index >= len(spdf.pages):
            raise EngineError(f"Page {page_index + 1} requested but the file only has {len(spdf.pages)} page(s).")
        sp = spdf.pages[page_index]
        media = _box(_inherited(sp.obj, "/MediaBox") or [0, 0, 612, 792])
        crop = _inherited(sp.obj, "/CropBox")
        view = _box(crop) if crop is not None else media
        view = [max(view[0], media[0]), max(view[1], media[1]), min(view[2], media[2]), min(view[3], media[3])]
        rot = int(_inherited(sp.obj, "/Rotate") or 0) % 360
        trim = sp.obj.get("/TrimBox")
        bleed = sp.obj.get("/BleedBox")

        out = pikepdf.new()
        out.pages.append(sp)
        p = out.pages[0]
        res = p.obj.get("/Resources")
        if res is None:
            res = _inherited(p.obj, "/Resources") or Dictionary()
        p.contents_coalesce()
        data = p.obj.Contents.read_bytes() if "/Contents" in p.obj else b""
        data = _filter_layers(spdf, out, res, data, only_layer)

    x0, y0, x1, y1 = view
    w, h = x1 - x0, y1 - y0
    # matrix mapping original user space → upright page at origin
    if rot == 90:
        m = [0, -1, 1, 0, -y0, w + x0]
        W, H = h, w
    elif rot == 180:
        m = [-1, 0, 0, -1, w + x0, h + y0]
        W, H = w, h
    elif rot == 270:
        m = [0, 1, -1, 0, h + y0, -x0]
        W, H = h, w
    else:
        m = [1, 0, 0, 1, -x0, -y0]
        W, H = w, h

    def tx(box):
        b = _box(box)
        pts = [(b[0], b[1]), (b[2], b[3]), (b[0], b[3]), (b[2], b[1])]
        xs, ys = [], []
        for (px, py) in pts:
            xs.append(m[0] * px + m[2] * py + m[4])
            ys.append(m[1] * px + m[3] * py + m[5])
        r = [max(0, min(xs)), max(0, min(ys)), min(W, max(xs)), min(H, max(ys))]
        return r

    form = out.make_stream(data)
    form.Type = Name.XObject
    form.Subtype = Name.Form
    form.BBox = Array(media)
    form.Resources = res
    if "/Group" in p.obj:
        form.Group = p.obj.Group
        del p.obj["/Group"]

    p.obj.Contents = out.make_stream(
        ("q %s cm /Fm0 Do Q" % " ".join(f"{v:.4f}" for v in m)).encode()
    )
    p.obj.Resources = Dictionary(XObject=Dictionary(Fm0=form))
    for k in ("/Rotate", "/CropBox", "/ArtBox", "/Annots", "/TrimBox", "/BleedBox"):
        if k in p.obj:
            del p.obj[k]
    p.obj.MediaBox = Array([0, 0, W, H])
    geo = {"width": W, "height": H, "has_trim": trim is not None, "has_bleed": bleed is not None}
    geo["trim"] = tx(trim) if trim is not None else [0, 0, W, H]
    geo["bleed"] = tx(bleed) if bleed is not None else [0, 0, W, H]
    p.obj.TrimBox = Array(geo["trim"])
    p.obj.BleedBox = Array(geo["bleed"])
    out.save(dst)
    spdf.close()
    return geo


def blank_like(dst: str, width: float, height: float):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(width, height))
    pdf.save(dst)


__all__ = ["to_pdf", "normalise_page", "page_count", "pt"]
