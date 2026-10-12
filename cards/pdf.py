"""PDF writer: page content streams by hand, file structure via pypdf.

We only ever fill/stroke Bézier paths in DeviceCMYK, so the content stream
is written directly (no ReportLab: it injects an unembedded Helvetica, uses
even-odd fill by default and has no OutputIntent support).

The print file follows PDF/X-1a:2003 rules: PDF 1.4, DeviceCMYK only, no
fonts (text is outlined), no transparency, TrimBox/BleedBox on every page,
trailer /ID, Info with GTS_PDFXVersion and Trapped, and a single GTS_PDFX
OutputIntent for the registered FOGRA39 printing condition.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path as FsPath
from typing import Mapping, Sequence

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    RectangleObject,
    TextStringObject,
)

from .paths import Shape, cmyk_ops, num, path_ops, rgb_ops

PT_PER_MM = 72.0 / 25.4

FOGRA39 = {
    "identifier": "FOGRA39",
    "registry": "http://www.color.org",
    "info": "Coated FOGRA39 (ISO 12647-2:2004)",
}


def shapes_ops(
    shapes: Sequence[Shape],
    palette: Mapping[str, Sequence[float]],
    rgb: Mapping[str, Sequence[float]] | None = None,
) -> str:
    """Serialise shapes (face coordinates, mm) into content-stream operators.

    Colours come from the CMYK `palette` (percent). If an `rgb` palette
    (0..1) is given, DeviceRGB is written instead — only for the on-screen
    preview, never for print files.

    The result is self-contained: colours and line width are always set
    before first use, so the same string can be emitted twice (upright and
    rotated) inside one page.
    """
    colour_ops = rgb_ops if rgb is not None else cmyk_ops
    colours = rgb if rgb is not None else palette
    out: list[str] = []
    fill = stroke = width = None
    for s in shapes:
        col = tuple(colours[s.color])
        if s.paint in ("f", "B") and col != fill:
            out.append(colour_ops(col))
            fill = col
        if s.paint in ("B", "S"):
            if col != stroke:
                out.append(colour_ops(col, stroke=True))
                stroke = col
            if s.width != width:
                out.append(f"{num(s.width)} w")
                width = s.width
        out.append(path_ops(s.path))
        out.append(s.paint)
    return "\n".join(out)


def _page(body: Sequence[str], offset: float = 0.0) -> bytes:
    """Page content in millimetres: scale mm → pt and shift by `offset` mm."""
    s = f"{PT_PER_MM:.6f}"
    o = f"{offset * PT_PER_MM:.6f}".rstrip("0").rstrip(".") or "0"
    parts = [
        "q",
        f"{s} 0 0 {s} {o} {o} cm",
        "1 j 1 J",  # round joins/caps for emboldening strokes
        *body,
        "Q",
    ]
    return ("\n".join(p for p in parts if p) + "\n").encode("ascii")


def sheet_ops(face: str, *, sheet_w: float, sheet_h: float, offset: float, extra: str = "") -> bytes:
    """One A4 sheet: the face upright in the bottom half and rotated by 180°
    in the top half, so the folded tent reads correctly from both sides.

    All coordinates are millimetres relative to the trim box; `offset` is the
    bleed (the trim box origin inside the media box).
    """
    rotated = ["q", f"-1 0 0 -1 {num(sheet_w)} {num(sheet_h)} cm", face, "Q"] if face else []
    return _page([face, *rotated, extra], offset)


def face_page_ops(face: str) -> bytes:
    """A single face on a page of its own (preview): no rotated copy."""
    return _page([face])


def pdf_date(epoch: float | None = None) -> str:
    """PDF date string in UTC. Honours SOURCE_DATE_EPOCH for reproducible builds."""
    if epoch is None:
        env = os.environ.get("SOURCE_DATE_EPOCH")
        epoch = float(env) if env else time.time()
    return time.strftime("D:%Y%m%d%H%M%S+00'00'", time.gmtime(epoch))


@dataclass
class PageGeometry:
    trim_w: float  # mm
    trim_h: float  # mm
    bleed: float  # mm, 0 for the office file

    @property
    def media(self) -> tuple[float, float]:
        return (self.trim_w + 2 * self.bleed, self.trim_h + 2 * self.bleed)


def _stream(data: bytes, extra: Mapping[str, object] | None = None):
    st = DecodedStreamObject()
    st.set_data(data)
    for k, v in (extra or {}).items():
        st[NameObject(k)] = v
    return st.flate_encode(level=9)


def _rect_pt(x0: float, y0: float, x1: float, y1: float) -> RectangleObject:
    return RectangleObject([round(v * PT_PER_MM, 4) for v in (x0, y0, x1, y1)])


def write_pdf(
    path: FsPath,
    pages: Sequence[bytes],
    geom: PageGeometry,
    *,
    title: str,
    subject: str = "",
    pdfx: bool = False,
    icc: bytes | None = None,
    creator: str = "cards (Mafia_TT tent-card generator)",
) -> None:
    w = PdfWriter()
    w.pdf_header = b"%PDF-1.4"
    mw, mh = geom.media
    b = geom.bleed
    for ops in pages:
        page = w.add_blank_page(width=round(mw * PT_PER_MM, 4), height=round(mh * PT_PER_MM, 4))
        page[NameObject("/Contents")] = w._add_object(_stream(ops))
        if pdfx or b > 0:
            page.trimbox = _rect_pt(b, b, b + geom.trim_w, b + geom.trim_h)
            page.bleedbox = _rect_pt(0, 0, mw, mh)

    if pdfx:
        oi = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/OutputIntent"),
                NameObject("/S"): NameObject("/GTS_PDFX"),
                NameObject("/OutputConditionIdentifier"): TextStringObject(FOGRA39["identifier"]),
                NameObject("/RegistryName"): TextStringObject(FOGRA39["registry"]),
                NameObject("/Info"): TextStringObject(FOGRA39["info"]),
            }
        )
        if icc:
            oi[NameObject("/DestOutputProfile")] = w._add_object(
                _stream(icc, {"/N": NumberObject(4)})
            )
        w.root_object[NameObject("/OutputIntents")] = ArrayObject([w._add_object(oi)])

    date = pdf_date()
    info = {
        NameObject("/Title"): TextStringObject(title),
        NameObject("/Creator"): TextStringObject(creator),
        NameObject("/Producer"): TextStringObject("pypdf"),
        NameObject("/CreationDate"): TextStringObject(date),
        NameObject("/ModDate"): TextStringObject(date),
        NameObject("/Trapped"): NameObject("/False"),
    }
    if subject:
        info[NameObject("/Subject")] = TextStringObject(subject)
    if pdfx:
        info[NameObject("/GTS_PDFXVersion")] = TextStringObject("PDF/X-1a:2003")
    w._info = DictionaryObject(info)

    w.generate_file_identifiers()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        w.write(fh)
