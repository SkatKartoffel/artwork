"""Colour conversion, ink limiting and font outlining."""
from __future__ import annotations


import numpy as np
import pikepdf
from pikepdf import Array, Dictionary, Name

from .util import run_gs
from .walker import Walker

COMMON_GS = [
    "-sDEVICE=pdfwrite",
    "-dCompatibilityLevel=1.6",
    "-dAutoRotatePages=/None",
    "-dDownsampleColorImages=false",
    "-dDownsampleGrayImages=false",
    "-dDownsampleMonoImages=false",
    "-dAutoFilterColorImages=false",
    "-dAutoFilterGrayImages=false",
    "-sColorImageFilter=FlateEncode",
    "-sGrayImageFilter=FlateEncode",
    "-dPassThroughJPEGImages=false",
    "-dPassThroughJPXImages=false",
    "-dPreserveOverprintSettings=true",
    "-dPreserveHalftoneInfo=false",
    "-dEmbedAllFonts=true",
    "-dSubsetFonts=true",
]


def outline_only(src, dst, gs=None):
    run_gs([*COMMON_GS, "-sColorConversionStrategy=LeaveColorUnchanged", "-dNoOutputFonts",
            "-o", dst, src], gs)
    return dst


def limit_ink(src, dst, tac_percent: float):
    with pikepdf.open(src) as pdf:
        stats = Walker(pdf, "tac", tac_limit=tac_percent / 100.0).run()
        pdf.save(dst)
    return dict(stats)


def measure_tac(pdf_path, limit_percent=None, dpi=60):
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        pix = doc[0].get_pixmap(colorspace=pymupdf.csCMYK, dpi=dpi, alpha=False)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 4)
    tac = a.astype(np.float32).sum(-1) / 255.0 * 100.0
    res = {"max": float(round(tac.max(), 1)), "p99": float(round(np.percentile(tac, 99), 1))}
    if limit_percent:
        res["over_fraction"] = float(round((tac > limit_percent + 2).mean(), 5))
    return res


def add_output_intent(pdf: pikepdf.Pdf, profile_path: str, identifier: str, info: str = ""):
    with open(profile_path, "rb") as fh:
        icc = pdf.make_stream(fh.read())
    icc.N = 4
    intent = Dictionary(
        Type=Name.OutputIntent,
        S=Name("/GTS_PDFX"),
        OutputConditionIdentifier=pikepdf.String(identifier),
        OutputCondition=pikepdf.String(identifier),
        Info=pikepdf.String(info or identifier),
        RegistryName=pikepdf.String("http://www.color.org"),
        DestOutputProfile=icc,
    )
    pdf.Root.OutputIntents = Array([pdf.make_indirect(intent)])


class ColourConverter:
    """RGB / Lab → output CMYK profile using LittleCMS (via Pillow's ImageCms).

    Ghostscript's pdfwrite ignores -sOutputICCProfile, so the real conversion
    into the press profile happens here.
    """

    def __init__(self, profile_path: str, intent: int = 1, bpc: bool = True):
        from PIL import ImageCms

        self.cms = ImageCms
        self.out = ImageCms.getOpenProfile(profile_path)
        self.intent = int(intent)
        flags = 0
        if bpc and self.intent in (1, 0):
            flags = getattr(getattr(ImageCms, "Flags", None), "BLACKPOINTCOMPENSATION", 0x2000)
        self.flags = flags
        self.srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        self.lab = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
        self._transforms = {}
        self._profiles = {}
        self._cache = {}

    # ---- profiles -------------------------------------------------------------
    def source_profile(self, cs):
        """Embedded ICC profile for an [/ICCBased stm] RGB colour space, else sRGB."""
        try:
            if isinstance(cs, Array) and str(cs[0]) == "/ICCBased":
                stm = cs[1]
                key = stm.objgen if stm.is_indirect else id(stm)
                if key not in self._profiles:
                    import io

                    try:
                        p = self.cms.ImageCmsProfile(io.BytesIO(bytes(stm.read_bytes())))
                        if p.profile.xcolor_space.strip() != "RGB":
                            p = None
                    except Exception:
                        p = None
                    self._profiles[key] = p
                if self._profiles[key] is not None:
                    return key, self._profiles[key]
        except Exception:
            pass
        return "srgb", self.srgb

    def _transform(self, key, src, mode):
        k = (key, mode)
        if k not in self._transforms:
            self._transforms[k] = self.cms.buildTransform(src, self.out, mode, "CMYK",
                                                          renderingIntent=self.intent, flags=self.flags)
        return self._transforms[k]

    # ---- conversions -------------------------------------------------------------
    def rgb_many(self, arr, cs=None) -> np.ndarray:
        """arr (N,3) floats 0..1 → (N,4) floats 0..1."""
        from PIL import Image

        arr = np.clip(np.asarray(arr, dtype=np.float64).reshape(-1, 3), 0, 1)
        key, prof = self.source_profile(cs)
        img = Image.fromarray(np.round(arr * 255).astype(np.uint8).reshape(1, -1, 3), "RGB")
        out = self.cms.applyTransform(img, self._transform(key, prof, "RGB"))
        return np.asarray(out, dtype=np.float64).reshape(-1, 4) / 255.0

    def lab_many(self, arr) -> np.ndarray:
        from PIL import Image

        a = np.asarray(arr, dtype=np.float64).reshape(-1, 3)
        enc = np.stack([np.clip(a[:, 0], 0, 100) * 2.55, np.clip(a[:, 1] + 128, 0, 255),
                        np.clip(a[:, 2] + 128, 0, 255)], -1)
        img = Image.fromarray(np.round(enc).astype(np.uint8).reshape(1, -1, 3), "LAB")
        out = self.cms.applyTransform(img, self._transform("lab", self.lab, "LAB"))
        return np.asarray(out, dtype=np.float64).reshape(-1, 4) / 255.0

    def colour(self, family, vals, cs=None):
        key = (family, tuple(round(float(v), 4) for v in vals),
               cs.objgen if getattr(cs, "is_indirect", False) else None)
        if key not in self._cache:
            conv = self.rgb_many([vals], cs) if family == "rgb" else self.lab_many([vals])
            self._cache[key] = tuple(float(v) for v in conv[0])
        return self._cache[key]

    def image(self, pil, cs=None) -> np.ndarray:
        """PIL image (RGB/RGBA/P/LAB/…) → (h, w, 4) uint8 CMYK."""
        if pil.mode == "LAB":
            out = self.cms.applyTransform(pil, self._transform("lab", self.lab, "LAB"))
        else:
            if pil.mode != "RGB":
                pil = pil.convert("RGB")
            key, prof = self.source_profile(cs)
            out = self.cms.applyTransform(pil, self._transform(key, prof, "RGB"))
        return np.asarray(out, dtype=np.uint8)


def flatten_for_print(src, dst, *, outline_fonts=False, keep_spots=False, gs=None):
    """Ghostscript pass that leaves colour values alone but (optionally) turns
    non-finish spot colours into their CMYK alternates and fonts into outlines."""
    args = [*COMMON_GS, "-sProcessColorModel=DeviceCMYK", "-sColorConversionStrategy=LeaveColorUnchanged",
            f"-dPreserveSeparation={'true' if keep_spots else 'false'}"]
    if outline_fonts:
        args.append("-dNoOutputFonts")
    run_gs([*args, "-o", dst, src], gs)
    return dst


def convert_and_limit(src, dst, profile_path, tac_percent, intent=1):
    """RGB/Lab → profile CMYK (LittleCMS) and TAC capping, in one content-stream pass."""
    conv = ColourConverter(profile_path, intent)
    with pikepdf.open(src) as pdf:
        w = Walker(pdf, "tac", tac_limit=tac_percent / 100.0, converter=conv)
        stats = w.run()
        pdf.save(dst)
    out = dict(stats)
    out["__max_tac__"] = round(w.max_tac * 100.0, 2)
    out["__max_in__"] = round(w.max_in * 100.0, 2)        # highest total ink found before limiting      # highest total ink written, read from the file's own values
    return out


def residual_rgb(pdf_path) -> int:
    """Count RGB / Lab colour still present (operators, colour spaces in use,
    images, shadings) after conversion. 0 means fully CMYK."""
    from .walker import cs_info, page_resources, resolve_cs

    count = 0
    seen = set()

    def scan(stream_owner, res):
        nonlocal count
        try:
            instrs = pikepdf.parse_content_stream(stream_owner)
        except Exception:
            return
        for ins in instrs:
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                continue
            op = str(ins.operator)
            if op in ("rg", "RG"):
                count += 1
            elif op in ("cs", "CS") and res is not None:
                if cs_info(resolve_cs(res, ins.operands[0]))[0] in ("rgb", "lab"):
                    count += 1
            elif op == "Do" and res is not None:
                try:
                    xo = res.XObject[str(ins.operands[0])]
                except Exception:
                    continue
                key = xo.objgen
                if key in seen:
                    continue
                seen.add(key)
                if xo.get("/Subtype") == Name.Image:
                    if cs_info(xo.get("/ColorSpace", Name.DeviceGray))[0] in ("rgb", "lab"):
                        count += 1
                elif xo.get("/Subtype") == Name.Form:
                    scan(xo, xo.get("/Resources", res))
            elif op == "sh" and res is not None:
                try:
                    if cs_info(res.Shading[str(ins.operands[0])].ColorSpace)[0] in ("rgb", "lab"):
                        count += 1
                except Exception:
                    pass

    with pikepdf.open(pdf_path) as pdf:
        for page in pdf.pages:
            scan(page, page_resources(page.obj))
    return count
