"""Engine regression test – runs without Frappe.

    python -m artwork.tests.test_engine            (from the app root)

Needs Ghostscript on PATH. Uses Ghostscript's bundled CMYK profile as a stand-in
for ISO Coated v2 so it can run anywhere.
"""
import glob
import os
import subprocess
import tempfile
import unittest

from artwork.engine import process_component
from artwork.tests.make_fixtures import artwork, foil_mask, template


def _stand_in_icc():
    for p in glob.glob("/usr/share/color/icc/ghostscript/default_cmyk.icc") + \
            glob.glob("/usr/share/ghostscript/*/iccprofiles/default_cmyk.icc"):
        return p
    raise unittest.SkipTest("No CMYK ICC profile found")


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.fx = os.path.join(cls.tmp, "fx")
        os.makedirs(cls.fx)
        template(os.path.join(cls.fx, "template.pdf"))
        artwork(os.path.join(cls.fx, "artwork.pdf"))
        foil_mask(os.path.join(cls.fx, "foil_mask.pdf"))
        cls.result = process_component({
            "art_path": os.path.join(cls.fx, "artwork.pdf"),
            "work_dir": os.path.join(cls.tmp, "out"),
            "out_basename": "TEST",
            "template": {"path": os.path.join(cls.fx, "template.pdf"), "spine_width_mm": 5,
                         "trim_width_mm": 633, "trim_height_mm": 314},
            "colour": {"profile_path": _stand_in_icc(), "identifier": "ISO Coated v2 300% (ECI)", "tac_limit": 300},
            "upgrades": [
                {"label": "Spot UV", "spot_name": "Spot UV", "aliases": ["UV"], "source": "Artwork spot"},
                {"label": "Hot Foil Gold", "spot_name": "Foil Gold", "source": "Separate file",
                 "file": os.path.join(cls.fx, "foil_mask.pdf")},
            ],
            "options": {"outline_fonts": True},
            "proof": {"details": [("Proof", "TEST")]},
        })

    def codes(self):
        return {w["code"] for w in self.result["warnings"]}

    def test_alignment_uses_customer_dieline(self):
        al = self.result["info"]["alignment"]
        self.assertIn("dieline", al["method"])
        self.assertAlmostEqual(al["offset_x_mm"], 7.0, places=1)
        self.assertAlmostEqual(self.result["info"]["template"]["spine_centre_mm"], 326.5, places=1)

    def test_preflight(self):
        for code in ("IMAGE_LOW_RES", "FONT_TOO_SMALL", "TEXT_CROSSES_FOLD", "HAIRLINES", "SPOT_CONVERTED"):
            self.assertIn(code, self.codes())
        self.assertNotIn("SIZE_MISMATCH", self.codes())

    def test_ink_capped(self):
        ink = self.result["info"]["ink"]
        self.assertGreater(ink["max_before"], 300)
        self.assertLessEqual(ink["max_after"], 301)

    def test_ink_limit_is_absolute(self):
        """8-bit rounding must never push a pixel over the limit (300% must not read as 301%)."""
        import numpy as np

        from artwork.engine.walker import cap_tac, cap_tac_array, quantise_tac
        rng = np.random.default_rng(7)
        a = rng.random((50000, 4))
        a[:50] = 1.0
        for lim in (3.0, 2.6):
            q = quantise_tac(cap_tac_array(a, lim), lim).astype(int)
            self.assertLessEqual(q.sum(-1).max(), int(lim * 255 + 1e-6))
            for row in a[:2000]:
                self.assertLessEqual(sum(round(v, 5) for v in cap_tac(*row, lim)), lim + 1e-9)

    def test_plates_and_overprint(self):
        out = os.path.join(self.tmp, "seps")
        os.makedirs(out, exist_ok=True)
        subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=tiffsep", "-dOverprint=/enable",
                        "-r10", "-o", os.path.join(out, "s.tif"), self.result["print_pdf"]], check=True)
        plates = sorted(os.path.basename(p)[2:-5] for p in glob.glob(os.path.join(out, "s(*).tif")))
        self.assertEqual(plates, ["Black", "Cyan", "Foil Gold", "Magenta", "Spot UV", "Template", "Yellow"])

    def test_colour_converted_with_profile(self):
        fix = self.result["info"]["ink"]["fixups"]
        self.assertGreater(fix.get("RGB colours converted", 0), 0)
        self.assertGreater(fix.get("RGB images converted", 0), 0)
        self.assertNotIn("RGB_LEFT", self.codes())

    def test_converter_honours_profile(self):
        """The LittleCMS conversion must match Ghostscript's raster output for the same
        ICC profile (raster devices honour -sOutputICCProfile; pdfwrite does not)."""
        import numpy as np
        import pymupdf
        from PIL import Image
        from artwork.engine.colour import ColourConverter

        icc = _stand_in_icc()
        swatch = os.path.join(self.tmp, "swatch.pdf")
        d = pymupdf.open()
        d.new_page(width=72, height=72).draw_rect(pymupdf.Rect(0, 0, 72, 72), fill=(0.2, 0.4, 0.8), color=None)
        d.save(swatch)
        tif = os.path.join(self.tmp, "swatch.tif")
        subprocess.run(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=tiff32nc",
                        f"--permit-file-read={os.path.dirname(icc)}/", f"-sOutputICCProfile={icc}",
                        "-r10", "-o", tif, swatch], check=True)
        ref = np.asarray(Image.open(tif))[5, 5].astype(float)
        mine = np.array(ColourConverter(icc).colour("rgb", [0.2, 0.4, 0.8])) * 255
        self.assertLess(np.abs(ref - mine).max(), 6, f"gs {ref} vs converter {mine}")

    def test_outputs_exist(self):
        for k in ("print_pdf", "preview_png", "proof_pdf"):
            self.assertTrue(os.path.getsize(self.result[k]) > 1000, k)


class TestSpineless(unittest.TestCase):
    """12" spineless flat: centre fold + glue-flap lines must NOT be taken for a spine."""

    def test_no_spine_centres_on_trim(self):
        import pymupdf

        tmp = tempfile.mkdtemp()
        MM = 72 / 25.4
        tpl = os.path.join(tmp, "spineless.pdf")
        d = pymupdf.open()
        p = d.new_page(width=648 * MM, height=334 * MM)
        x0, y0 = 10 * MM, 10 * MM
        p.draw_rect(pymupdf.Rect(x0, y0, x0 + 628 * MM, y0 + 314 * MM), color=(1, 0, 1), width=0.5)
        for x in (307, 314, 321):  # glue flap, centre fold, glue flap
            p.draw_line((x0 + x * MM, y0), (x0 + x * MM, y0 + 314 * MM), color=(1, 0, 1), width=0.5)
        d.save(tpl)
        art = os.path.join(tmp, "art.pdf")
        d = pymupdf.open()
        p = d.new_page(width=634 * MM, height=320 * MM)  # trim + 3mm bleed, centred
        p.draw_rect(p.rect, color=None, fill=(0.2, 0.3, 0.8))
        d.save(art)
        r = process_component({
            "art_path": art, "work_dir": os.path.join(tmp, "out"), "out_basename": "SL",
            "template": {"path": tpl, "no_spine": True, "trim_width_mm": 628, "trim_height_mm": 314},
            "colour": {"profile_path": _stand_in_icc(), "identifier": "test", "tac_limit": 300},
            "upgrades": [], "options": {},
        })
        info = r["info"]
        self.assertIn("no spine", info["template"]["spine_source"])
        self.assertEqual(info["template"]["spine_width_mm"], 0)
        self.assertAlmostEqual(info["template"]["spine_centre_mm"], 324.0, places=1)  # page centre = trim centre
        self.assertAlmostEqual(info["alignment"]["offset_x_mm"], 7.0, places=1)
        codes = {w["code"] for w in r["warnings"]}
        self.assertNotIn("BLEED", codes)
        self.assertNotIn("TEXT_CROSSES_FOLD", codes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
