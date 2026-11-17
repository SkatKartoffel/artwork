"""Artwork processing engine (framework-independent).

Requires: Ghostscript (system), pikepdf, PyMuPDF, numpy, Pillow.
"""
from .pipeline import DEFAULT_TEMPLATE_ALIASES, PS_STANDARD_SPOTS, process_component  # noqa: F401
from .util import EngineError  # noqa: F401
