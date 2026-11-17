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


if __name__ == "__main__":
    unittest.main(verbosity=2)
