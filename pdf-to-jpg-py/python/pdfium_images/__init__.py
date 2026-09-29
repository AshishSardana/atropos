"""pdfium-images: Fast PDF to JPEG converter powered by pdfium."""

from __future__ import annotations

import os

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if "PDFIUM_LIB_DIR" not in os.environ:
    os.environ["PDFIUM_LIB_DIR"] = _pkg_dir

_libs_dir = os.path.join(os.path.dirname(_pkg_dir), "pdfium_images.libs")
for _dir in [_pkg_dir, _libs_dir]:
    if os.path.isdir(_dir):
        _ld = os.environ.get("LD_LIBRARY_PATH", "")
        if _dir not in _ld:
            os.environ["LD_LIBRARY_PATH"] = _dir + (":" + _ld if _ld else "")

from pdfium_images._native import convert_pdf_to_images, page_count, render_pdf_to_bytes

__all__ = ["convert_pdf_to_images", "render_pdf_to_bytes", "page_count"]
