from __future__ import annotations

import os
import shutil
import subprocess

MM = 72.0 / 25.4  # points per millimetre


def mm(v_pt: float) -> float:
    return v_pt / MM


def pt(v_mm: float) -> float:
    return float(v_mm or 0) * MM


class Report:
    """Collects preflight findings and processing info for one component."""

    def __init__(self):
        self.items: list[dict] = []
        self.info: dict = {}

    def add(self, severity: str, code: str, message: str, **extra):
        # de-duplicate identical codes+messages
        for it in self.items:
            if it["code"] == code and it["message"] == message:
                return
        self.items.append({"severity": severity, "code": code, "message": message, **extra})

    def error(self, code, message, **kw):
        self.add("Error", code, message, **kw)

    def warn(self, code, message, **kw):
        self.add("Warning", code, message, **kw)

    def note(self, code, message, **kw):
        self.add("Info", code, message, **kw)

    @property
    def has_errors(self):
        return any(i["severity"] == "Error" for i in self.items)

    def as_dict(self):
        order = {"Error": 0, "Warning": 1, "Info": 2}
        return {"warnings": sorted(self.items, key=lambda i: order[i["severity"]]), "info": self.info}


class EngineError(Exception):
    pass


def ghostscript_bin(explicit: str | None = None) -> str:
    for cand in (explicit, os.environ.get("ARTWORK_GS"), "gs", "gsc", "gswin64c"):
        if cand and shutil.which(cand):
            return shutil.which(cand)
    raise EngineError("Ghostscript not found. Install it on the server (apt install ghostscript).")


def run_gs(args: list[str], gs: str | None = None, timeout: int = 900):
    cmd = [ghostscript_bin(gs), "-dSAFER", "-dBATCH", "-dNOPAUSE", "-q", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise EngineError(f"Ghostscript failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"[:2000])
    return proc
