"""Assemble the print-ready PDF from processed layers."""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

from .colour import add_output_intent


def compose(out_path: str, tpl: dict, layers: list[dict], *, output_intent: dict | None = None,
            gray_spots: list[str] | None = None, title: str = "", keep_open=False):
    """layers: [{path, offset:(dx,dy), label, overprint, clip:[x0,y0,x1,y1]|None}]

    Layer order is paint order (first = bottom).  Every layer becomes an
    Optional Content Group so it shows up as a named layer in Acrobat / RIPs.
    """
    out = pikepdf.new()
    W, H = tpl["width"], tpl["height"]
    out.add_blank_page(page_size=(W, H))
    page = out.pages[0]
    xobjs, props, ocgs, parts = Dictionary(), Dictionary(), [], []
    srcs = []
    for i, layer in enumerate(layers):
        src = pikepdf.open(layer["path"])
        srcs.append(src)
        out.pages.append(src.pages[0])
        tmp = out.pages[-1]
        tmp.contents_coalesce()
        data = tmp.obj.Contents.read_bytes() if "/Contents" in tmp.obj else b""
        form = out.make_stream(data)
        form.Type = Name.XObject
        form.Subtype = Name.Form
        form.BBox = Array([float(v) for v in tmp.obj.MediaBox])
        form.Resources = tmp.obj.get("/Resources", Dictionary())
        del out.pages[-1]
        xobjs[f"/Fx{i}"] = out.make_indirect(form)
        ocg = out.make_indirect(Dictionary(Type=Name.OCG, Name=pikepdf.String(layer.get("label") or f"Layer {i + 1}")))
        ocgs.append(ocg)
        props[f"/OC{i}"] = ocg
        dx, dy = layer.get("offset", (0, 0))
        s = f"/OC /OC{i} BDC q "
        if layer.get("overprint"):
            s += "/GSop gs "
        clip = layer.get("clip")
        if clip:
            s += f"{clip[0]:.4f} {clip[1]:.4f} {clip[2] - clip[0]:.4f} {clip[3] - clip[1]:.4f} re W n "
        s += f"1 0 0 1 {dx:.4f} {dy:.4f} cm /Fx{i} Do Q EMC\n"
        parts.append(s)

    page.obj.Resources = Dictionary(
        XObject=xobjs,
        Properties=props,
        ExtGState=Dictionary(GSop=Dictionary(Type=Name.ExtGState, OP=True, op=True, OPM=1)),
    )
    page.obj.Contents = out.make_stream("".join(parts).encode())
    page.obj.TrimBox = Array([round(v, 4) for v in tpl["trim"]])
    page.obj.BleedBox = Array([round(v, 4) for v in tpl["bleed"]])
    out.Root.OCProperties = Dictionary(
        OCGs=Array(ocgs),
        D=Dictionary(Name=pikepdf.String("Layers"), Order=Array(ocgs), ON=Array(ocgs), OFF=Array()),
    )
    if gray_spots:
        wanted = {f"/{n}" for n in gray_spots}
        from .walker import iter_separations
        for obj in iter_separations(out):
            if len(obj) == 4 and str(obj[1]) in wanted:
                obj[2] = Name.DeviceGray
                obj[3] = Dictionary(FunctionType=2, Domain=[0, 1], C0=[1], C1=[0], N=1)
    if output_intent:
        add_output_intent(out, output_intent["profile_path"], output_intent["identifier"],
                          output_intent.get("info", ""))
    out.docinfo["/Title"] = title or "Artwork"
    out.docinfo["/Creator"] = "Packaged Sounds Artwork (Frappe)"
    out.docinfo["/Trapped"] = Name("/False")
    out.save(out_path)
    for s in srcs:
        s.close()
    return out_path
