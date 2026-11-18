"""PDF content-stream walker.

Rewrites page / form content streams in one of four modes:

* ``spot``    – recolour *everything* into one Separation (spot) colour.  Used for
                supplier templates (→ "Template" spot) and customer-supplied
                upgrade masks (→ "Spot UV", "Hot Foil Gold" …).  Raster images are
                turned into 1-bit stencil masks so the result stays a clean plate.
* ``extract`` – keep only marks painted in the target Separation(s), drop all
                other marks.  Used to lift an upgrade / dieline spot out of the
                customer's artwork into its own layer.
* ``remove``  – the inverse of ``extract``: drop marks painted in the target
                Separation(s) and leave everything else untouched.
* ``tac``     – cap Total Area Coverage of DeviceCMYK / ICC-CMYK colour
                (vector colour operators, CMYK images, indexed palettes and simple
                shading functions).

In ``spot`` and ``extract`` mode every ExtGState is forced to overprint so the
resulting plate never knocks out the artwork beneath it.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np
import pikepdf
from pikepdf import Array, Dictionary, Name, Operator

DEVICE_CS = {
    "/DeviceGray": ("gray", 1), "/G": ("gray", 1), "/CalGray": ("gray", 1),
    "/DeviceRGB": ("rgb", 3), "/RGB": ("rgb", 3), "/CalRGB": ("rgb", 3),
    "/DeviceCMYK": ("cmyk", 4), "/CMYK": ("cmyk", 4),
    "/Pattern": ("pattern", 0),
}
PAINT_FILL = {"f", "F", "f*"}
PAINT_STROKE = {"S", "s"}
PAINT_BOTH = {"B", "B*", "b", "b*"}
TEXT_SHOW = {"Tj", "TJ", "'", '"'}


# --------------------------------------------------------------------------- helpers
def norm_name(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower().lstrip("/"))


COLOUR_LIBRARIES = ("pantone", "pms", "hks", "ral", "toyo", "dic")


def name_matches(name, aliases) -> bool:
    """Exact match on normalised names; longer aliases (≥6 chars) may also
    match inside a name ("SpotUV_gloss" ← "spotuv"), but never inside a
    colour-library name such as "PANTONE 871 C Gold"."""
    n = norm_name(name)
    if not n:
        return False
    library = n.startswith(COLOUR_LIBRARIES)
    for a in aliases or []:
        a = norm_name(a)
        if not a:
            continue
        if n == a or (not library and len(a) >= 6 and a in n):
            return True
    return False


def make_spot_cs(pdf: pikepdf.Pdf, name: str, alt_cmyk=(0, 1, 0, 0)):
    fn = Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0],
                    C1=[float(v) for v in alt_cmyk], N=1)
    return pdf.make_indirect(
        Array([Name.Separation, Name("/" + name), Name.DeviceCMYK, pdf.make_indirect(fn)])
    )


def add_resource(resources, category: str, obj, prefix: str) -> Name:
    if category not in resources:
        resources[category] = Dictionary()
    d = resources[category]
    try:
        if obj.is_indirect:
            for k, v in d.items():
                if getattr(v, "is_indirect", False) and v.objgen == obj.objgen:
                    return Name(k)
    except Exception:
        pass
    i = 0
    while f"/{prefix}{i}" in d:
        i += 1
    d[f"/{prefix}{i}"] = obj
    return Name(f"/{prefix}{i}")


def page_resources(page_obj):
    node = page_obj
    while node is not None:
        if "/Resources" in node:
            res = node.Resources
            if node is not page_obj:
                page_obj.Resources = res
            return page_obj.Resources
        node = node.get("/Parent")
    page_obj.Resources = Dictionary()
    return page_obj.Resources


def cs_info(cs):
    """Return (family, ncomponents, extra) for a resolved colour space object."""
    try:
        if isinstance(cs, Name):
            fam = DEVICE_CS.get(str(cs))
            return (fam[0], fam[1], None) if fam else ("unknown", 0, None)
        if isinstance(cs, Array) and len(cs):
            fam = str(cs[0])
            if fam == "/ICCBased":
                n = int(cs[1].get("/N", 3))
                return ({1: "gray", 3: "rgb", 4: "cmyk"}.get(n, "unknown"), n, None)
            if fam in DEVICE_CS:
                f = DEVICE_CS[fam]
                return (f[0], f[1], None)
            if fam == "/Lab":
                return ("lab", 3, None)
            if fam == "/Separation":
                return ("sep", 1, str(cs[1]))
            if fam == "/DeviceN":
                names = [str(x) for x in cs[1]]
                return ("devn", len(names), names)
            if fam in ("/Indexed", "/I"):
                return ("indexed", 1, cs_info(cs[1]))
            if fam == "/Pattern":
                return ("pattern", 0, None)
    except Exception:
        pass
    return ("unknown", 0, None)


def resolve_cs(resources, name):
    s = str(name)
    if s in DEVICE_CS:
        return Name(s)
    try:
        csd = resources.get("/ColorSpace")
        if csd is not None and s in csd:
            return csd[s]
    except Exception:
        pass
    return Name(s)


def default_vals(info):
    fam, n, _ = info
    return {"gray": [0.0], "rgb": [0.0, 0.0, 0.0], "cmyk": [0.0, 0.0, 0.0, 1.0],
            "sep": [1.0], "devn": [1.0] * max(n, 1), "lab": [0.0, 0.0, 0.0],
            "indexed": [0.0]}.get(fam, [])


def darkness(info, vals) -> float:
    fam = info[0]
    try:
        v = [float(x) for x in vals if not isinstance(x, Name)]
        if fam == "gray":
            return 1 - v[0]
        if fam == "rgb":
            return 1 - (0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2])
        if fam == "cmyk":
            return min(1.0, max(v))
        if fam == "sep":
            return 0.0 if norm_name(info[2]) == "none" else v[0]
        if fam == "devn":
            return min(1.0, max(v))
        if fam == "lab":
            return 1 - v[0] / 100.0
    except Exception:
        pass
    return 1.0  # patterns, indexed, unknown → treat as solid


def cap_tac(c, m, y, k, limit):
    """Scalar TAC limiter (all 0..1, limit as fraction e.g. 3.0 for 300%)."""
    total = c + m + y + k
    if total <= limit + 1e-6:
        return c, m, y, k
    excess = total - limit
    d = min(min(c, m, y), 1 - k, excess / 2)  # GCR: swap neutral CMY for K
    if d > 0:
        c, m, y, k = c - d, m - d, y - d, k + d
    cmy = c + m + y
    if c + m + y + k > limit and cmy > 0:
        f = max(0.0, limit - k) / cmy
        c, m, y = c * f, m * f, y * f
    # the limit is an absolute maximum: round the way the values are written (5 decimals) without going over
    k = round(k, 5)
    c, m, y = (math.floor(v * 1e5 + 1e-9) / 1e5 for v in (c, m, y))
    over = c + m + y + k - limit
    if over > 1e-9:
        big = max(range(3), key=lambda i: (c, m, y)[i])
        v = [c, m, y]
        v[big] = max(0.0, math.floor((v[big] - over) * 1e5 + 1e-9) / 1e5)
        c, m, y = v
    short = limit - (c + m + y + k)          # ... and without falling short of it either: exactly the limit
    if short > 1e-9:
        v = [c, m, y]
        for i in sorted(range(3), key=lambda i: -v[i]):
            if v[i] + short <= 1.0:
                v[i] = round(v[i] + short, 5)
                break
        c, m, y = v
    return c, m, y, k


def quantise_tac(a: np.ndarray, limit: float) -> np.ndarray:
    """Capped CMYK floats (0..1) -> 8-bit, guaranteeing no pixel adds up to more than the limit.
    Plain rounding can push a pixel up to 0.8% over (300% -> 301%)."""
    a = np.clip(a, 0, 1)
    q = np.round(a * 255).astype(np.int32)
    max_sum = int(math.floor(limit * 255 + 1e-6))
    at_limit = a.sum(-1) >= limit - 1e-6          # pixels that were capped should land exactly on the limit
    for _ in range(4):
        short = np.where(at_limit, max_sum - q.sum(-1), 0)
        low = short > 0
        if not low.any():
            break
        room = np.where(q[..., :3] < 255, q[..., :3], -1)
        idx = room.argmax(-1)
        cur = np.take_along_axis(q[..., :3], idx[..., None], -1)[..., 0]
        add = np.where(low & (cur < 255), np.minimum(short, 255 - cur), 0)
        np.put_along_axis(q[..., :3], idx[..., None], (cur + add)[..., None], -1)
    for _ in range(6):
        over = q.sum(-1) - max_sum
        bad = over > 0
        if not bad.any():
            break
        idx = q[..., :3].argmax(-1)
        take = np.where(bad, np.minimum(over, np.take_along_axis(q[..., :3], idx[..., None], -1)[..., 0]), 0)
        np.put_along_axis(q[..., :3], idx[..., None], np.take_along_axis(q[..., :3], idx[..., None], -1) - take[..., None], -1)
    return q.astype(np.uint8)


def cap_tac_array(a: np.ndarray, limit: float) -> np.ndarray:
    """Vectorised TAC limiter; a is (..., 4) float 0..1."""
    a = a.astype(np.float64).copy()
    total = a.sum(-1)
    over = total > limit + 1e-6
    if not over.any():
        return a
    excess = np.where(over, total - limit, 0)
    g = a[..., :3].min(-1)
    d = np.minimum(np.minimum(g, 1 - a[..., 3]), excess / 2)
    d = np.where(over, np.clip(d, 0, None), 0)
    a[..., :3] -= d[..., None]
    a[..., 3] += d
    cmy = a[..., :3].sum(-1)
    total = cmy + a[..., 3]
    still = (total > limit) & (cmy > 0)
    f = np.where(still, np.clip(limit - a[..., 3], 0, None) / np.where(cmy > 0, cmy, 1), 1)
    a[..., :3] *= f[..., None]
    return a


def _num(x):
    return round(float(x), 5)


def I(operands, op):  # noqa: E743 - tiny constructor helper
    return pikepdf.ContentStreamInstruction(list(operands), Operator(op))


# --------------------------------------------------------------------------- walker
class _State:
    __slots__ = ("fill_cs", "fill_info", "fill_vals", "stroke_cs", "stroke_info", "stroke_vals", "tr")

    def __init__(self):
        self.fill_cs = Name.DeviceGray
        self.fill_info = ("gray", 1, None)
        self.fill_vals = [0.0]
        self.stroke_cs = Name.DeviceGray
        self.stroke_info = ("gray", 1, None)
        self.stroke_vals = [0.0]
        self.tr = 0

    def copy(self):
        s = _State()
        for k in self.__slots__:
            v = getattr(self, k)
            setattr(s, k, list(v) if isinstance(v, list) else v)
        return s


class Walker:
    def __init__(self, pdf: pikepdf.Pdf, mode: str, *, spot_name=None, spot_alt=(0, 1, 0, 0),
                 targets=None, threshold=0.5, tac_limit=3.0, converter=None):
        assert mode in ("spot", "extract", "remove", "tac")
        self.pdf = pdf
        self.mode = mode
        self.targets = [norm_name(t) for t in (targets or [])]
        self.threshold = threshold
        self.tac = tac_limit
        self.spot = make_spot_cs(pdf, spot_name, spot_alt) if spot_name else None
        self.stats = Counter()
        self.max_tac = 0.0
        self.max_in = 0.0
        self._forms = {}
        self._form_src = {}
        self._images = {}
        self._egs = {}
        self._shadings = set()
        self._patterns = set()
        self._indexed = set()
        self.conv = converter if mode == "tac" else None

    # ---- ink limit, keeping track of the highest total actually written -----------
    def _cap(self, c, m, y, k):
        self.max_in = max(self.max_in, c + m + y + k)
        r = cap_tac(c, m, y, k, self.tac)
        self.max_tac = max(self.max_tac, sum(r))
        return r

    def _quant(self, arr):
        q = quantise_tac(arr, self.tac)
        if q.size:
            self.max_tac = max(self.max_tac, float(q.reshape(-1, q.shape[-1]).astype(np.int32).sum(-1).max()) / 255.0)
        return q

    # ---- target tests ------------------------------------------------------
    def _is_target_info(self, info):
        fam, _, extra = info
        if fam == "sep":
            return norm_name(extra) in self.targets
        if fam == "devn":
            return bool(extra) and all(norm_name(n) in self.targets for n in extra)
        return False

    def _keep(self, is_target):
        return is_target if self.mode == "extract" else not is_target

    # ---- public -------------------------------------------------------------
    def run(self):
        if self.mode == "extract" and self.spot is not None:
            self._rename_targets()
        for page in self.pdf.pages:
            res = page_resources(page.obj)
            instrs = pikepdf.parse_content_stream(page)
            prefix = []
            if self.mode == "spot":
                n = add_resource(res, "/ColorSpace", self.spot, "CSspot")
                prefix = [I([n], "cs"), I([1], "scn"), I([n], "CS"), I([1], "SCN")]
            if self.conv is not None:
                self._fix_group(page.obj)
            out = prefix + self._walk(instrs, res, _State())
            page.obj.Contents = self.pdf.make_stream(pikepdf.unparse_content_stream(out))
        self._neutralise_spots()
        return self.stats

    def _neutralise_spots(self):
        """Point leftover colourants at /None so RIPs don't list empty plates."""
        if self.mode == "tac":
            return
        keep = {norm_name(self.spot[1])} if self.spot is not None else set()
        for arr in iter_separations(self.pdf, include_devicen=True):
            try:
                if self.spot is not None and arr.objgen == self.spot.objgen:
                    continue
                if arr[0] == Name.Separation:
                    n = norm_name(arr[1])
                    drop = (n in self.targets) if self.mode == "remove" else (n not in keep)
                    if drop and n != "none":
                        arr[1] = Name("/None")
                        self.stats["plates neutralised"] += 1
                else:
                    names = [norm_name(x) for x in arr[1]]
                    if self.mode == "remove":
                        drop = any(n in self.targets for n in names)
                    else:
                        drop = not all(n in keep for n in names)
                    if drop:
                        arr[1] = Array([Name("/None")] * len(arr[1]))
            except Exception:
                continue

    def _rename_targets(self):
        """Point every matching Separation at the canonical spot name."""
        canon = self.spot[1]
        for obj in iter_separations(self.pdf):
            if len(obj) == 4:
                if norm_name(obj[1]) in self.targets and obj[1] != canon:
                    self.stats[f"renamed {obj[1]} -> {canon}"] += 1
                    obj[1] = canon
        self.targets = list(set(self.targets) | {norm_name(canon)})

    # ---- core walk ----------------------------------------------------------
    def _walk(self, instrs, res, state: _State):
        out = []
        stack = []
        mode = self.mode
        if mode == "tac":
            try:
                csd = res.get("/ColorSpace") if res is not None else None
                if csd is not None and "/DefaultCMYK" in csd:
                    del csd["/DefaultCMYK"]
            except Exception:
                pass
        for ins in instrs:
            if isinstance(ins, pikepdf.ContentStreamInlineImage):
                out.extend(self._inline_image(ins, res, state))
                continue
            op = str(ins.operator)
            ops = list(ins.operands)

            if op == "q":
                stack.append(state.copy()); out.append(ins); continue
            if op == "Q":
                if stack:
                    state = stack.pop()
                out.append(ins); continue

            # ---------------- colour operators
            if op in ("cs", "CS"):
                cs = resolve_cs(res, ops[0])
                info = cs_info(cs)
                if op == "cs":
                    state.fill_cs, state.fill_info, state.fill_vals = cs, info, default_vals(info)
                else:
                    state.stroke_cs, state.stroke_info, state.stroke_vals = cs, info, default_vals(info)
                if mode == "spot":
                    out.extend(self._spot_set(res, op == "cs", state)); continue
                if self.conv is not None:
                    if info[0] in ("rgb", "lab"):
                        out.append(I([Name.DeviceCMYK], op)); continue
                    if info[0] == "indexed":
                        self._fix_indexed(cs)
                        new_info = cs_info(cs)
                        if op == "cs":
                            state.fill_info = new_info
                        else:
                            state.stroke_info = new_info
                out.append(ins); continue

            if op in ("sc", "scn", "SC", "SCN"):
                fill = op in ("sc", "scn")
                vals = [o if isinstance(o, Name) else float(o) for o in ops]
                if fill:
                    state.fill_vals = vals
                else:
                    state.stroke_vals = vals
                info = state.fill_info if fill else state.stroke_info
                if mode == "spot":
                    out.extend(self._spot_set(res, fill, state)); continue
                if mode == "tac" and vals and isinstance(vals[-1], Name):
                    self._pattern(res, vals[-1])
                    out.append(ins); continue
                if self.conv is not None and info[0] in ("rgb", "lab") and len(vals) == 3:
                    cs_obj = state.fill_cs if fill else state.stroke_cs
                    cmyk = self._cap(*self.conv.colour(info[0], vals, cs_obj))
                    self.stats[f"{info[0].upper()} colours converted"] += 1
                    out.append(I([_num(v) for v in cmyk], op)); continue
                if mode == "tac" and info[0] == "cmyk" and len(vals) == 4 and not any(isinstance(v, Name) for v in vals):
                    capped = self._cap(*vals)
                    if capped != tuple(vals):
                        self.stats["vector colours capped"] += 1
                    cs_now = state.fill_cs if fill else state.stroke_cs
                    if isinstance(cs_now, Array):      # ICC-tagged CMYK: write plain CMYK so the numbers are what prints
                        self.stats["ICC-tagged CMYK colours untagged"] += 1
                        out.append(I([_num(v) for v in capped], "k" if fill else "K")); continue
                    out.append(I([_num(v) for v in capped], op)); continue
                out.append(ins); continue

            if op in ("g", "G", "rg", "RG", "k", "K"):
                fill = op.islower()
                cs = {"g": Name.DeviceGray, "rg": Name.DeviceRGB, "k": Name.DeviceCMYK}[op.lower()]
                info = cs_info(cs)
                vals = [float(o) for o in ops]
                if fill:
                    state.fill_cs, state.fill_info, state.fill_vals = cs, info, vals
                else:
                    state.stroke_cs, state.stroke_info, state.stroke_vals = cs, info, vals
                if mode == "spot":
                    out.extend(self._spot_set(res, fill, state)); continue
                if self.conv is not None and op in ("rg", "RG"):
                    cmyk = self._cap(*self.conv.colour("rgb", vals))
                    self.stats["RGB colours converted"] += 1
                    out.append(I([_num(v) for v in cmyk], "k" if fill else "K")); continue
                if mode == "tac" and op in ("k", "K"):
                    capped = self._cap(*vals)
                    if capped != tuple(vals):
                        self.stats["vector colours capped"] += 1
                    out.append(I([_num(v) for v in capped], op)); continue
                out.append(ins); continue

            # ---------------- painting operators (extract / remove only)
            if mode in ("extract", "remove") and (op in PAINT_FILL or op in PAINT_STROKE or op in PAINT_BOTH):
                kf = self._keep(self._is_target_info(state.fill_info))
                ks = self._keep(self._is_target_info(state.stroke_info))
                out.append(I([], self._paint_op(op, kf, ks))); continue

            if op == "Tr":
                state.tr = int(ops[0]); out.append(ins); continue

            if mode in ("extract", "remove") and op in TEXT_SHOW:
                kf = self._keep(self._is_target_info(state.fill_info))
                ks = self._keep(self._is_target_info(state.stroke_info))
                tr = state.tr
                has_f, has_s, clip = tr in (0, 2, 4, 6), tr in (1, 2, 5, 6), tr >= 4
                f, s = has_f and kf, has_s and ks
                new = {(True, False): 0, (False, True): 1, (True, True): 2, (False, False): 3}[(f, s)] + (4 if clip else 0)
                if new != tr:
                    out += [I([new], "Tr"), ins, I([tr], "Tr")]
                else:
                    out.append(ins)
                continue

            if op == "sh":
                out.extend(self._shading(ins, res, state)); continue
            if op == "Do":
                out.extend(self._do(ins, res, state)); continue
            if op == "gs" and mode in ("spot", "extract"):
                out.append(I([self._op_egs(res, ops[0])], "gs")); continue

            out.append(ins)
        return out

    @staticmethod
    def _paint_op(op, kf, ks):
        if op in PAINT_FILL:
            return op if kf else "n"
        if op in PAINT_STROKE:
            return op if ks else "n"
        fill_only = {"B": "f", "B*": "f*", "b": "f", "b*": "f*"}[op]
        stroke_only = {"B": "S", "B*": "S", "b": "s", "b*": "s"}[op]
        if kf and ks:
            return op
        if kf:
            return fill_only
        if ks:
            return stroke_only
        return "n"

    # ---- spot mode colour mapping ---------------------------------------------
    def _spot_tint(self, info, vals):
        d = darkness(info, vals)
        if self.threshold is None:
            return max(0.0, min(1.0, d))
        return 1.0 if d >= self.threshold else 0.0

    def _spot_set(self, res, fill, state):
        n = add_resource(res, "/ColorSpace", self.spot, "CSspot")
        info = state.fill_info if fill else state.stroke_info
        vals = state.fill_vals if fill else state.stroke_vals
        t = self._spot_tint(info, vals)
        self.stats["colours mapped to spot"] += 1
        if fill:
            return [I([n], "cs"), I([_num(t)], "scn")]
        return [I([n], "CS"), I([_num(t)], "SCN")]

    # ---- ExtGState: force overprint ---------------------------------------------
    def _op_egs(self, res, name):
        try:
            egs = res.ExtGState[str(name)]
        except Exception:
            return name
        key = egs.objgen if egs.is_indirect else None
        if key and key in self._egs:
            return add_resource(res, "/ExtGState", self._egs[key], "GSop")
        new = Dictionary()
        for k, v in egs.items():
            if k in ("/SMask", "/BM", "/CA", "/ca"):
                continue
            new[k] = v
        new.OP = True
        new.op = True
        new.OPM = 1
        new = self.pdf.make_indirect(new)
        if key:
            self._egs[key] = new
        return add_resource(res, "/ExtGState", new, "GSop")

    # ---- shadings -------------------------------------------------------------
    def _shading(self, ins, res, state):
        try:
            sh = res.Shading[str(ins.operands[0])]
            info = cs_info(sh.get("/ColorSpace", Name.DeviceGray))
        except Exception:
            return [ins]
        if self.mode == "spot":
            n = add_resource(res, "/ColorSpace", self.spot, "CSspot")
            self.stats["shadings → solid spot"] += 1
            return [I([], "q"), I([n], "cs"), I([1], "scn"),
                    I([-20000, -20000, 40000, 40000], "re"), I([], "f"), I([], "Q")]
        if self.mode in ("extract", "remove"):
            return [ins] if self._keep(self._is_target_info(info)) else []
        if self.mode == "tac":
            self._tac_shading(sh)
        return [ins]

    def _tac_shading(self, sh):
        key = sh.objgen if sh.is_indirect else id(sh)
        if key in self._shadings:
            return
        self._shadings.add(key)
        cs = sh.get("/ColorSpace", Name.DeviceGray)
        info = cs_info(cs)
        if info[0] == "cmyk":
            self._cap_function(sh.get("/Function"))
            if isinstance(cs, Array):
                sh["/ColorSpace"] = Name.DeviceCMYK
                self.stats["ICC-tagged CMYK gradients untagged"] += 1
        elif info[0] in ("rgb", "lab") and self.conv is not None:
            self._convert_shading(sh, cs, info[0])

    def _convert_shading(self, sh, cs, family):
        """Resample the shading's colour function into the output CMYK profile."""
        stype = int(sh.get("/ShadingType", 0))
        fn = sh.get("/Function")
        if fn is None or stype not in (2, 3, 4, 5, 6, 7):
            self.stats["gradients left in RGB (mesh / function-based)"] += 1
            return
        try:
            dom = [float(x) for x in sh.get("/Domain", [0, 1])] if stype in (2, 3) else [0.0, 1.0]
            if stype in (4, 5, 6, 7):
                # parametric mesh: the function's own domain
                f0 = fn[0] if isinstance(fn, Array) else fn
                dom = [float(x) for x in f0.get("/Domain", [0, 1])]
            ts = np.linspace(dom[0], dom[1], 256)
            cols = np.array([eval_fn(fn, t)[:3] for t in ts], dtype=np.float64)
            cmyk = self.conv.rgb_many(cols, cs) if family == "rgb" else self.conv.lab_many(cols)
            cmyk = cap_tac_array(cmyk, self.tac)
            st = pikepdf.Stream(self.pdf, self._quant(cmyk).tobytes())
            st.FunctionType = 0
            st.Domain = Array(dom)
            st.Range = Array([0, 1] * 4)
            st.Size = Array([256])
            st.BitsPerSample = 8
            sh.Function = self.pdf.make_indirect(st)
            sh.ColorSpace = Name.DeviceCMYK
            if "/Background" in sh:
                bg = [float(x) for x in sh.Background]
                sh.Background = Array([_num(v) for v in self.conv.colour(family, bg, cs)])
            self.stats["gradients converted"] += 1
        except Exception as e:
            self.stats[f"gradients left in RGB ({type(e).__name__})"] += 1

    def _pattern(self, res, name):
        try:
            pat = res.Pattern[str(name)]
        except Exception:
            return
        key = pat.objgen if pat.is_indirect else id(pat)
        if key in self._patterns:
            return
        self._patterns.add(key)
        try:
            ptype = int(pat.get("/PatternType", 0))
            if ptype == 2 and "/Shading" in pat:
                self._tac_shading(pat.Shading)
            elif ptype == 1 and isinstance(pat, pikepdf.Stream):
                pres = pat.get("/Resources")
                if pres is None:
                    pat.Resources = Dictionary()
                    pres = pat.Resources
                new = self._walk(pikepdf.parse_content_stream(pat), pres, _State())
                pat.write(pikepdf.unparse_content_stream(new))
        except Exception as e:
            self.stats[f"pattern skipped ({type(e).__name__})"] += 1

    def _fix_group(self, obj):
        try:
            g = obj.get("/Group")
            if g is not None and "/CS" in g and (cs_info(g.CS)[0] == "rgb" or (cs_info(g.CS)[0] == "cmyk" and isinstance(g.CS, Array))):
                g.CS = Name.DeviceCMYK
        except Exception:
            pass

    def _fix_indexed(self, cs):
        """Convert an Indexed colour space's RGB/Lab palette to CMYK in place."""
        try:
            key = cs.objgen if cs.is_indirect else id(cs)
            if key in self._indexed or self.conv is None:
                return
            self._indexed.add(key)
            base = cs[1]
            fam = cs_info(base)[0]
            if fam not in ("rgb", "lab"):
                return
            lut = cs[3]
            raw = bytes(lut.read_bytes() if isinstance(lut, pikepdf.Stream) else lut)
            hival = int(cs[2])
            arr = np.frombuffer(raw, dtype=np.uint8)[: (hival + 1) * 3].reshape(-1, 3).astype(np.float64)
            if fam == "rgb":
                cmyk = self.conv.rgb_many(arr / 255.0, base)
            else:
                cmyk = self.conv.lab_many(np.stack([arr[:, 0] / 2.55, arr[:, 1] - 128, arr[:, 2] - 128], -1))
            cmyk = cap_tac_array(cmyk, self.tac)
            cs[1] = Name.DeviceCMYK
            cs[3] = pikepdf.String(self._quant(cmyk).tobytes())
            self.stats["indexed palettes converted"] += 1
        except Exception as e:
            self.stats[f"indexed palette skipped ({type(e).__name__})"] += 1

    def _cap_function(self, fn):
        if fn is None:
            return
        if isinstance(fn, Array):
            for f in fn:
                self._cap_function(f)
            return
        try:
            ft = int(fn.FunctionType)
            if ft == 2:
                for key in ("/C0", "/C1"):
                    if key in fn and len(fn[key]) == 4:
                        v = [float(x) for x in fn[key]]
                        c = self._cap(*v)
                        if c != tuple(v):
                            fn[key] = Array([_num(x) for x in c])
                            self.stats["shading colours capped"] += 1
            elif ft == 3:
                self._cap_function(fn.get("/Functions"))
        except Exception:
            pass

    # ---- XObjects -------------------------------------------------------------
    def _do(self, ins, res, state):
        name = str(ins.operands[0])
        try:
            xo = res.XObject[name]
        except Exception:
            return [ins]
        sub = str(xo.get("/Subtype", ""))
        if sub == "/Form":
            return self._form(ins, name, xo, res, state)
        if sub == "/Image":
            return self._image(ins, xo, res, state)
        return [ins]

    def _form(self, ins, name, xo, res, state):
        key_state = ()
        if self.mode in ("extract", "remove"):
            key_state = (self._is_target_info(state.fill_info), self._is_target_info(state.stroke_info))
        og = xo.objgen
        if (og, key_state) in self._forms:
            target = self._forms[(og, key_state)]
        else:
            if og not in self._form_src:
                self._form_src[og] = xo.read_bytes()
                target = xo
            else:  # same form, different inherited colour state → work on a copy
                target = pikepdf.Stream(self.pdf, self._form_src[og])
                for k, v in xo.items():
                    if k not in ("/Length", "/Filter", "/DecodeParms"):
                        target[k] = v
                target = self.pdf.make_indirect(target)
            if self.conv is not None:
                self._fix_group(target)
            fres = target.get("/Resources")
            if fres is None:
                target.Resources = res
                fres = target.Resources
            instrs = pikepdf.parse_content_stream(target)
            new = self._walk(instrs, fres, state.copy())
            target.write(pikepdf.unparse_content_stream(new))
            self._forms[(og, key_state)] = target
        if target.objgen != xo.objgen:
            return [I([add_resource(res, "/XObject", target, "FmW")], "Do")]
        return [ins]

    def _image(self, ins, xo, res, state):
        is_mask = bool(xo.get("/ImageMask", False))
        if self.mode in ("extract", "remove"):
            if is_mask:
                tgt = self._is_target_info(state.fill_info)
            else:
                tgt = self._is_target_info(cs_info(xo.get("/ColorSpace", Name.DeviceGray)))
            return [ins] if self._keep(tgt) else []
        if self.mode == "spot":
            if is_mask:
                return [ins]  # painted with current (already spot-mapped) fill colour
            stencil = self._stencil_for(xo)
            if stencil is None:
                self.stats["images dropped (undecodable)"] += 1
                return []
            n = add_resource(res, "/ColorSpace", self.spot, "CSspot")
            sn = add_resource(res, "/XObject", stencil, "ImSt")
            return [I([], "q"), I([n], "cs"), I([1], "scn"), I([sn], "Do"), I([], "Q")]
        if self.mode == "tac":
            self._tac_image(xo)
        return [ins]

    def _inline_image(self, ins, res, state):
        if self.mode == "tac":
            return self._tac_inline(ins, res)
        iim = ins.iimage
        if self.mode in ("extract", "remove"):
            if iim.image_mask:
                tgt = self._is_target_info(state.fill_info)
            else:
                cs = iim.obj.get("/CS", iim.obj.get("/ColorSpace", Name.DeviceGray))
                if isinstance(cs, Name):
                    cs = resolve_cs(res, cs)
                tgt = self._is_target_info(cs_info(cs))
            return [ins] if self._keep(tgt) else []
        # spot mode
        if iim.image_mask:
            return [ins]
        try:
            pil = iim.as_pil_image()
        except Exception:
            self.stats["inline images dropped"] += 1
            return []
        stencil = self._stencil_from_pil(pil, None)
        n = add_resource(res, "/ColorSpace", self.spot, "CSspot")
        sn = add_resource(res, "/XObject", stencil, "ImSt")
        return [I([], "q"), I([n], "cs"), I([1], "scn"), I([sn], "Do"), I([], "Q")]

    def _tac_inline(self, ins, res):
        """Small inline images: convert RGB/Lab ones to a CMYK image XObject."""
        if self.conv is None:
            return [ins]
        iim = ins.iimage
        try:
            if iim.image_mask:
                return [ins]
            cs = iim.obj.get("/CS", iim.obj.get("/ColorSpace", Name.DeviceGray))
            if isinstance(cs, Name) and str(cs) not in DEVICE_CS:
                cs = resolve_cs(res, cs)
            if cs_info(cs)[0] not in ("rgb", "lab"):
                return [ins]
            data = self.conv.image(iim.as_pil_image(), cs)
            self.max_in = max(self.max_in, float(np.asarray(data, dtype=np.int32).sum(-1).max()) / 255.0)
            capped = self._quant(cap_tac_array(data / 255.0, self.tac))
            import zlib
            st = pikepdf.Stream(self.pdf, zlib.compress(capped.tobytes(), 6))
            st.Filter = Name.FlateDecode
            st.Type, st.Subtype = Name.XObject, Name.Image
            st.Width, st.Height = capped.shape[1], capped.shape[0]
            st.ColorSpace = Name.DeviceCMYK
            st.BitsPerComponent = 8
            n = add_resource(res, "/XObject", self.pdf.make_indirect(st), "ImCv")
            self.stats["inline images converted"] += 1
            return [I([n], "Do")]
        except Exception as e:
            self.stats[f"inline images left in RGB ({type(e).__name__})"] += 1
            return [ins]

    # ---- image helpers ----------------------------------------------------------
    def _stencil_for(self, xo):
        og = xo.objgen
        if og in self._images:
            return self._images[og]
        try:
            pil = pikepdf.PdfImage(xo).as_pil_image()
        except Exception:
            self._images[og] = None
            return None
        alpha = None
        if "/SMask" in xo:
            try:
                alpha = pikepdf.PdfImage(xo.SMask).as_pil_image().convert("L").resize(pil.size)
            except Exception:
                alpha = None
        st = self._stencil_from_pil(pil, alpha)
        self._images[og] = st
        self.stats["images → stencil"] += 1
        return st

    def _stencil_from_pil(self, pil, alpha):
        if pil.mode == "CMYK":
            a = np.asarray(pil, dtype=np.float32) / 255.0
            dark = a.max(-1)
        else:
            a = np.asarray(pil.convert("L"), dtype=np.float32) / 255.0
            dark = 1 - a
        thr = self.threshold if self.threshold is not None else 0.5
        paint = dark >= max(thr, 0.01)
        if alpha is not None:
            paint &= (np.asarray(alpha, dtype=np.float32) / 255.0) > 0.5
        bits = np.packbits(~paint, axis=1)  # stencil: 0 = paint
        st = pikepdf.Stream(self.pdf, bits.tobytes())
        st.Type = Name.XObject
        st.Subtype = Name.Image
        st.Width, st.Height = pil.size[0], pil.size[1]
        st.ImageMask = True
        st.BitsPerComponent = 1
        return self.pdf.make_indirect(st)

    def _tac_image(self, xo):
        og = xo.objgen
        if og in self._images:
            return
        self._images[og] = True
        cs = xo.get("/ColorSpace", Name.DeviceGray)
        info = cs_info(cs)
        try:
            if self.conv is not None and info[0] == "indexed":
                self._fix_indexed(cs)
                info = cs_info(cs)
            if self.conv is not None and info[0] in ("rgb", "lab") and not xo.get("/ImageMask", False):
                pil = pikepdf.PdfImage(xo).as_pil_image()
                data = self.conv.image(pil, cs)
                self.max_in = max(self.max_in, float(np.asarray(data, dtype=np.int32).sum(-1).max()) / 255.0)
                capped = self._quant(cap_tac_array(data / 255.0, self.tac))
                import zlib
                xo.write(zlib.compress(capped.tobytes(), 6), filter=Name.FlateDecode)
                xo.ColorSpace = Name.DeviceCMYK
                xo.BitsPerComponent = 8
                for k in ("/Decode", "/DecodeParms", "/Intent"):
                    if k in xo:
                        del xo[k]
                if "/Mask" in xo and isinstance(xo.Mask, Array):  # colour-key mask no longer valid
                    del xo["/Mask"]
                self.stats[f"{info[0].upper()} images converted"] += 1
                return
            if info[0] == "indexed" and info[2] and info[2][0] == "cmyk":
                lut = cs[3]
                raw = bytes(lut.read_bytes() if isinstance(lut, pikepdf.Stream) else lut)
                arr = np.frombuffer(raw, dtype=np.uint8)[: (len(raw) // 4) * 4].reshape(-1, 4) / 255.0
                capped = cap_tac_array(arr, self.tac)
                if (np.round(arr * 255).sum(-1) > math.floor(self.tac * 255 + 1e-6)).any():
                    cs[3] = pikepdf.String(self._quant(capped).tobytes())
                    self.stats["indexed palettes capped"] += 1
                return
            if info[0] == "cmyk" and isinstance(cs, Array) and not xo.get("/ImageMask", False):
                # ICC-tagged CMYK image: untag it, otherwise viewers and RIPs convert it again and the ink total moves
                xo.ColorSpace = Name.DeviceCMYK
                if "/Intent" in xo:
                    del xo["/Intent"]
                self.stats["ICC-tagged CMYK images untagged"] += 1
            if info[0] != "cmyk" or int(xo.get("/BitsPerComponent", 8)) != 8:
                return
            w, h = int(xo.Width), int(xo.Height)
            filters = xo.get("/Filter")
            simple = filters is None or (str(filters) == "/FlateDecode") or (
                isinstance(filters, Array) and [str(f) for f in filters] == ["/FlateDecode"])
            if simple:
                data = np.frombuffer(xo.read_bytes(), dtype=np.uint8)[: w * h * 4].reshape(h, w, 4)
                decode = xo.get("/Decode")
                if decode is not None and [float(x) for x in decode][:2] == [1.0, 0.0]:
                    data = 255 - data
            else:
                data = np.asarray(pikepdf.PdfImage(xo).as_pil_image(), dtype=np.uint8)
                if data.shape != (h, w, 4):
                    return
            arr = data / 255.0
            self.max_in = max(self.max_in, float(data.astype(np.int32).sum(-1).max()) / 255.0)
            if (data.astype(np.int32).sum(-1) <= math.floor(self.tac * 255 + 1e-6)).all():
                self.max_tac = max(self.max_tac, float(data.astype(np.int32).sum(-1).max()) / 255.0)
                return
            capped = self._quant(cap_tac_array(arr, self.tac))
            import zlib
            xo.write(zlib.compress(capped.tobytes(), 6), filter=Name.FlateDecode)
            if "/Decode" in xo:
                del xo["/Decode"]
            self.stats["CMYK images capped"] += 1
        except Exception as e:  # never let one odd image kill the job
            self.stats[f"image TAC skipped ({type(e).__name__})"] += 1


# --------------------------------------------------------------------------- utilities
def iter_separations(pdf: pikepdf.Pdf, include_devicen=False):
    """Yield every /Separation (and optionally /DeviceN) colour-space array,
    including direct ones nested inside resource dictionaries."""
    seen = set()
    out = []

    def visit(o, depth=0):
        if depth > 40:
            return
        try:
            if getattr(o, "is_indirect", False):
                if o.objgen in seen:
                    return
                seen.add(o.objgen)
            if isinstance(o, Array):
                if len(o) >= 2 and isinstance(o[0], Name) and (
                        o[0] == Name.Separation or (include_devicen and o[0] == Name.DeviceN)):
                    out.append(o)
                for v in o:
                    if isinstance(v, (Array, Dictionary, pikepdf.Stream)):
                        visit(v, depth + 1)
            elif isinstance(o, (Dictionary, pikepdf.Stream)):
                for k in o.keys():
                    if k in ("/Parent", "/P"):
                        continue
                    v = o[k]
                    if isinstance(v, (Array, Dictionary, pikepdf.Stream)):
                        visit(v, depth + 1)
        except Exception:
            return

    visit(pdf.Root)
    for obj in pdf.objects:
        if isinstance(obj, (Array, Dictionary, pikepdf.Stream)):
            visit(obj)
    return out


def list_spot_names(pdf: pikepdf.Pdf):
    names = set()
    for obj in iter_separations(pdf, include_devicen=True):
        try:
            if obj[0] == Name.Separation:
                names.add(str(obj[1])[1:])
            else:
                for n in obj[1]:
                    names.add(str(n)[1:])
        except Exception:
            continue
    names -= {"Cyan", "Magenta", "Yellow", "Black", "None", "All"}
    return sorted(names)


def run_mode(src, dst, mode, **kw):
    with pikepdf.open(src) as pdf:
        stats = Walker(pdf, mode, **kw).run()
        pdf.save(dst)
    return stats


def eval_fn(fn, t):
    """Evaluate a 1-input PDF function (types 0, 2, 3 or an array of them)."""
    if isinstance(fn, Array):
        return [eval_fn(f, t)[0] for f in fn]
    ft = int(fn.FunctionType)
    dom = [float(x) for x in fn.get("/Domain", [0, 1])]
    t = min(max(float(t), dom[0]), dom[1])
    if ft == 2:
        c0 = [float(x) for x in fn.get("/C0", [0])]
        c1 = [float(x) for x in fn.get("/C1", [1])]
        n = float(fn.get("/N", 1))
        f = t ** n if t > 0 else 0.0
        return [a + f * (b - a) for a, b in zip(c0, c1)]
    if ft == 3:
        fns = fn.Functions
        bounds = [float(x) for x in fn.get("/Bounds", [])]
        enc = [float(x) for x in fn.Encode]
        k = 0
        while k < len(bounds) and t >= bounds[k]:
            k += 1
        lo = dom[0] if k == 0 else bounds[k - 1]
        hi = dom[1] if k == len(bounds) else bounds[k]
        e0, e1 = enc[2 * k], enc[2 * k + 1]
        tt = e0 if hi == lo else e0 + (t - lo) * (e1 - e0) / (hi - lo)
        return eval_fn(fns[k], tt)
    if ft == 0:
        size = [int(x) for x in fn.Size]
        if len(size) != 1:
            raise NotImplementedError("multi-input sampled function")
        bps = int(fn.BitsPerSample)
        rng = [float(x) for x in fn.Range]
        nout = len(rng) // 2
        raw = fn.read_bytes()
        if bps == 8:
            samples = np.frombuffer(raw, dtype=np.uint8)[: size[0] * nout].reshape(size[0], nout) / 255.0
        elif bps == 16:
            samples = np.frombuffer(raw, dtype=">u2")[: size[0] * nout].reshape(size[0], nout) / 65535.0
        else:
            raise NotImplementedError(f"{bps}-bit samples")
        enc = [float(x) for x in fn.get("/Encode", [0, size[0] - 1])]
        dec = [float(x) for x in fn.get("/Decode", rng)]
        e = enc[0] + (t - dom[0]) * (enc[1] - enc[0]) / ((dom[1] - dom[0]) or 1)
        e = min(max(e, 0), size[0] - 1)
        i0 = int(np.floor(e))
        i1 = min(i0 + 1, size[0] - 1)
        f = e - i0
        sv = samples[i0] * (1 - f) + samples[i1] * f
        return [dec[2 * j] + sv[j] * (dec[2 * j + 1] - dec[2 * j]) for j in range(nout)]
    raise NotImplementedError(f"function type {ft}")
