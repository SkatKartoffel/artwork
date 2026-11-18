"""Optional spellcheck of customer artwork: English and German only.

Text comes from two places:
  * live text in the PDF (exact), and
  * OCR of the rendered artwork (Tesseract) for text that is outlined or part of an image. OCR is less
    reliable, so only confidently read words are checked and they are reported separately.
A word is fine if it is English (US or British spelling) or German (including compounds). Nothing here
ever blocks a proof: findings are warnings for a person to look at.
"""

import os
import re
import shutil
import subprocess

EXTRA = """tracklist tracklisting remaster remasters remix remixes remixed vinyl gatefold digipak digisleeve
overdub overdubs mixdown bandcamp spotify youtube instagram facebook tiktok soundcloud www http https com
synth synths mellotron vocoder glockenspiel timpani flugelhorn rhodes wurlitzer moog hammond
arr feat prod rec vol nr inkl ltd gmbh inc llc rpm lp ep cd dj mc ft vs aka""".split()

# British spelling -> US spelling, tried when a word is not in the (US) English list
GB_RULES = [("isation", "ization"), ("ising", "izing"), ("ised", "ized"), ("ises", "izes"), ("iser", "izer"),
            ("ise", "ize"), ("ysed", "yzed"), ("ysing", "yzing"), ("yse", "yze"), ("our", "or"), ("ours", "ors"),
            ("oured", "ored"), ("ouring", "oring"), ("ourite", "orite"), ("ourites", "orites"), ("ourful", "orful"),
            ("ourable", "orable"), ("tre", "ter"), ("tres", "ters"), ("bre", "ber"), ("lled", "led"),
            ("lling", "ling"), ("ller", "ler"), ("llers", "lers"), ("ogue", "og"), ("ogues", "ogs"),
            ("ence", "ense"), ("ences", "enses"), ("mme", "m"), ("mmes", "ms"), ("ae", "e"), ("oe", "e")]
GB_WORDS = {"grey", "greys", "cheque", "cheques", "kerb", "tyre", "tyres", "plough", "sceptical", "jewellery",
            "aluminium", "whilst", "amongst", "learnt", "dreamt", "spelt", "burnt", "storey", "practise", "licence"}

WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüßÀ-ÖØ-öø-ÿ]+(?:['’][A-Za-z]+)?")
SKIP_RE = re.compile(r"(https?://|www\.|@|\.(com|de|co|org|net|uk)\b)", re.I)

_cache = {}


def _dicts():
    if "d" not in _cache:
        from spellchecker import SpellChecker
        en, de = SpellChecker(language="en", distance=1), SpellChecker(language="de", distance=1)
        en.word_frequency.load_words(EXTRA + list(GB_WORDS))
        _cache["d"] = (en, de)
    return _cache["d"]


def available() -> bool:
    try:
        import spellchecker  # noqa: F401
        return True
    except Exception:
        return False


def _english(w, en):
    if w in en:
        return True
    if w.endswith("'s") or w.endswith("’s"):
        return w[:-2] in en
    w = w.replace("’", "'")
    if w in en:
        return True
    for gb, us in GB_RULES:
        i = w.find(gb)
        while i != -1:
            if w[:i] + us + w[i + len(gb):] in en:
                return True
            i = w.find(gb, i + 1)
    return False


def _german(w, de, depth=0):
    if w in de or w.replace("ss", "ß") in de:
        return True
    if depth == 0:
        for a, b in (("ae", "ä"), ("oe", "ö"), ("ue", "ü")):
            if a in w and _german(w.replace(a, b), de, 1):
                return True
    if depth > 2 or len(w) < 7:
        return False
    # compound: known head (optionally + linking s/es/n/en) + known rest
    for i in range(3, len(w) - 2):
        head, rest = w[:i], w[i:]
        if head not in de:
            continue
        for link in ("", "s", "es", "n", "en"):
            if rest.startswith(link) and len(rest) - len(link) >= 3 and _german(rest[len(link):], de, depth + 1):
                return True
    return False


def known(word: str) -> bool:
    en, de = _dicts()
    w = word.lower().strip("'’")
    return _english(w, en) or _german(w, de)


def suggest(word: str):
    en, de = _dicts()
    w = word.lower()
    cands = [(c, sp) for sp in (en, de) for c in (sp.candidates(w) or []) if c != w]
    if not cands:
        return None
    best = max(cands, key=lambda cs: cs[1].word_usage_frequency(cs[0]) or 0)[0]
    return best.upper() if word.isupper() else best.capitalize() if word[:1].isupper() else best


# ---------------------------------------------------------------- text sources
def live_words(pdf_path: str, page_index: int = 0) -> list[str]:
    import pymupdf
    with pymupdf.open(pdf_path) as d:
        return [w[4] for w in d[page_index].get_text("words")]


def ocr_words(pdf_path: str, work_dir: str, page_index: int = 0, min_conf: float = 88.0, max_px: float = 14e6):
    """Words Tesseract reads confidently from the rendered page, upright and both sideways (spines).
    None when Tesseract (with English and German) is not installed."""
    exe = shutil.which("tesseract")
    if not exe:
        return None
    try:
        langs = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30).stdout.split()
    except Exception:
        return None
    use = "+".join(code for code in ("eng", "deu") if code in langs)
    if not use:
        return None
    import pymupdf
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    png = os.path.join(work_dir, "spell.png")
    with pymupdf.open(pdf_path) as d:
        page = d[page_index]
        area_in2 = (page.rect.width / 72) * (page.rect.height / 72)
        dpi = int(max(100, min(300, (max_px / max(area_in2, 0.01)) ** 0.5)))
        page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False).save(png)
    out = []
    for rot in (0, 90, 270):
        src = png
        if rot:
            src = os.path.join(work_dir, f"spell{rot}.png")
            with Image.open(png) as im:
                im.rotate(rot, expand=True).save(src)
        try:
            r = subprocess.run([exe, src, "stdout", "-l", use, "--psm", "11", "--dpi", str(dpi), "tsv"],
                               capture_output=True, text=True, timeout=300)
        except Exception:
            continue
        finally:
            if rot and os.path.exists(src):
                os.remove(src)
        for line in r.stdout.splitlines()[1:]:
            c = line.split("\t")
            if len(c) >= 12 and c[0] == "5":
                try:
                    conf = float(c[10])
                except ValueError:
                    continue
                if conf >= min_conf and c[11].strip():
                    out.append(c[11].strip())
    if os.path.exists(png):
        os.remove(png)
    return out


# ---------------------------------------------------------------- check
def _tokens(raw_words, min_len):
    for raw in raw_words:
        if SKIP_RE.search(raw) or any(ch.isdigit() for ch in raw):
            continue
        for m in WORD_RE.finditer(raw):
            t = m.group(0).strip("'’")
            if len(t) >= min_len:
                yield t


def check(raw_words, ignore=(), min_len=3):
    """-> {'checked': n, 'unknown': [words in reading order, unique]}"""
    skip = {i.lower() for i in ignore}
    seen, unknown, n = set(), [], 0
    for t in _tokens(raw_words, min_len):
        low = t.lower()
        if low in seen or low in skip:
            continue
        seen.add(low)
        n += 1
        if not known(t):
            unknown.append(t)
    return {"checked": n, "unknown": unknown}


def _fmt(words, limit=30):
    parts = []
    for i, w in enumerate(words[:limit]):
        s = suggest(w) if i < 20 and len(w) <= 18 else None
        parts.append(f"{w} ({s}?)" if s else w)
    more = f" … and {len(words) - limit} more" if len(words) > limit else ""
    return ", ".join(parts) + more


def run(pdf_path, work_dir, rep, ignore_words=(), page_index=0):
    """Spellcheck one artwork page and add the findings to the report."""
    if not available():
        rep.note("SPELL_UNAVAILABLE", "Spellcheck was ticked but the spellcheck package is not installed on the server "
                 "(bench pip install pyspellchecker).")
        return
    ignore = set()
    for chunk in ignore_words or ():
        ignore.update(m.group(0).lower() for m in WORD_RE.finditer(chunk or ""))

    live = check(live_words(pdf_path, page_index), ignore)
    foreign = live["checked"] >= 8 and len(live["unknown"]) > 0.5 * live["checked"]
    if foreign:
        rep.note("SPELL_LANGUAGE", "Spellcheck skipped for the live text: it does not look like English or German "
                 f"({len(live['unknown'])} of {live['checked']} words not recognised).")
    elif live["unknown"]:
        rep.warn("SPELLING", "Possible spelling mistakes (English/German check): " + _fmt(live["unknown"]))

    ocr = ocr_words(pdf_path, work_dir, page_index)
    if ocr is None:
        rep.note("SPELL_NO_OCR", "Outlined text and text inside images was not spellchecked: Tesseract OCR is not "
                 "installed on the server." + ("" if live["checked"] else " No live text was found in this file."))
        if live["checked"] and not live["unknown"] and not foreign:
            rep.note("SPELL_OK", f"Spellcheck: {live['checked']} words of live text checked, nothing found.")
        return
    already = {t.lower() for t in _tokens(live_words(pdf_path, page_index), 3)}
    read = check(ocr, ignore | already, min_len=4)
    if read["checked"] >= 8 and len(read["unknown"]) > 0.5 * read["checked"]:
        rep.note("SPELL_OCR_LANGUAGE", "Spellcheck skipped for outlined/image text: it does not look like English or "
                 f"German, or could not be read reliably ({len(read['unknown'])} of {read['checked']} words not recognised).")
    elif read["unknown"]:
        rep.warn("SPELLING_OCR", "Possible spelling mistakes in outlined or image text (read by OCR, so less reliable "
                 "– check against the artwork): " + _fmt(read["unknown"]))
    total = live["checked"] + read["checked"]
    if not total:
        rep.note("SPELL_NONE", "Spellcheck: no readable text found in the artwork.")
    elif not live["unknown"] and not read["unknown"]:
        rep.note("SPELL_OK", f"Spellcheck: {total} words checked ({live['checked']} live, {read['checked']} read from "
                 "outlines/images), nothing found.")
