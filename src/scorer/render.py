"""Engrave a music21 score to a multi-page PDF, with no external programs.

verovio lays out and draws each page as SVG; svglib/reportlab turn the SVGs
into a PDF. The title block is drawn with reportlab so long titles wrap
cleanly and the tempo doesn't need a music font.
"""

from __future__ import annotations

import copy
import io
import re
from importlib.resources import files
from pathlib import Path

import verovio
from music21 import stream, tempo
from music21.musicxml.m21ToXml import GeneralObjectExporter
from reportlab.graphics import renderPDF
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from svglib.svglib import svg2rlg

# verovio page units are 0.3 pt at scale 40, so these give a US-letter page.
_SCALE = 40
_PAGE_W, _PAGE_H = letter
_UNITS_PER_PT = 100 / _SCALE / 0.75
_HEADER_PT = 84


def render_pdf(score: stream.Score, path: Path, title: str, subtitle: str) -> None:
    # The package points verovio at its fonts with a default resource path,
    # but only for the thread that imported it; a toolkit made on another
    # thread (the API's background jobs) would look in a stale build path and
    # fail to load any font. Setting the path on the toolkit works anywhere.
    tk = verovio.toolkit(False)
    if not tk.setResourcePath(str(files("verovio") / "data")):
        raise RuntimeError("verovio could not load its fonts")
    tk.setOptions(
        {
            "scale": _SCALE,
            "pageWidth": round(_PAGE_W * _UNITS_PER_PT),
            "pageHeight": round(_PAGE_H * _UNITS_PER_PT),
            "pageMarginTop": round(_HEADER_PT * _UNITS_PER_PT),
            "pageMarginBottom": 120,
            "pageMarginLeft": 120,
            "pageMarginRight": 120,
            "adjustPageHeight": False,
            "header": "none",
            "footer": "none",
        }
    )
    if not tk.loadData(_musicxml_without_tempo(score)):
        raise RuntimeError("verovio could not read the generated MusicXML")

    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle(title)
    for page in range(1, tk.getPageCount() + 1):
        drawing = svg2rlg(io.BytesIO(_flatten(tk.renderToSVG(page)).encode()))
        if page == 1:
            _draw_header(pdf, title, subtitle)
        # Scale the SVG to fill the page exactly, whatever verovio's unit maths did.
        drawing.scale(_PAGE_W / drawing.width, _PAGE_H / drawing.height)
        renderPDF.draw(drawing, pdf, 0, 0)
        pdf.showPage()
    pdf.save()


def _flatten(svg: str) -> str:
    """Hoist the viewBox of verovio's nested <svg> onto the root element.

    svglib applies its px->pt conversion to the nested element as well, which
    draws everything at 75% size in the bottom-left of the page.
    """
    inner = re.search(r'<svg class="definition-scale"([^>]*)>', svg)
    if not inner:
        return svg
    attrs = inner.group(1)
    viewbox = re.search(r'\s*(viewBox="[^"]+")', attrs)
    flat = svg[: inner.start()] + "<g" + attrs.replace(viewbox.group(0), "") + ">" + svg[inner.end():]
    flat = flat.replace("</svg>", "</g>", 1)  # the nested element closes first
    return flat.replace("<svg ", f"<svg {viewbox.group(1)} ", 1)


def _musicxml_without_tempo(score: stream.Score) -> str:
    # The metronome mark's note glyph needs a music font the PDF lacks; the
    # tempo is written in the header instead.
    plain = copy.deepcopy(score)
    for mark in list(plain.recurse().getElementsByClass(tempo.MetronomeMark)):
        mark.activeSite.remove(mark)
    return GeneralObjectExporter(plain).parse().decode()


def _draw_header(pdf: canvas.Canvas, title: str, subtitle: str) -> None:
    max_width = _PAGE_W - 72

    def width(text: str, size: int) -> float:
        return pdf.stringWidth(text, "Helvetica-Bold", size)

    # Shrink long titles a little; past that, truncate with an ellipsis.
    size = 18
    while size > 12 and width(title, size) > max_width:
        size -= 1
    if width(title, size) > max_width:
        while title and width(title + "…", size) > max_width:
            title = title[:-1].rstrip()
        title += "…"
    pdf.setFont("Helvetica-Bold", size)
    pdf.drawCentredString(_PAGE_W / 2, _PAGE_H - 40, title)
    pdf.setFont("Helvetica", 10)
    pdf.drawCentredString(_PAGE_W / 2, _PAGE_H - 58, subtitle)
