"""PNG previews: contact sheet of all cards and a "folded tent" view.

Uses poppler's pdftoppm if it is installed (optional). When the FOGRA39
profile is available it is used for CMYK→RGB conversion (soft proof), so the
red on screen is closer to the printed one.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path as FsPath
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def available() -> bool:
    return shutil.which("pdftoppm") is not None


def _label_font(size: int):
    for p in FONT_CANDIDATES:
        if FsPath(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render(pdf: FsPath, dpi: int, icc: FsPath | None = None, first: int | None = None, last: int | None = None) -> list[Image.Image]:
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["pdftoppm", "-r", str(dpi), "-png"]
        if icc is not None:
            cmd += ["-defaultcmykprofile", str(icc)]
        if first:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        subprocess.run(cmd + [str(pdf), f"{tmp}/p"], check=True, capture_output=True)
        files = sorted(FsPath(tmp).glob("p*.png"))
        return [Image.open(f).convert("RGB") for f in files]


def contact_sheet(pages: Sequence[Image.Image], labels: Sequence[str], out: FsPath, cols: int = 3) -> None:
    w, h = pages[0].size
    pad, lab_h = 16, 34
    rows = (len(pages) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + pad) + pad, rows * (h + pad + lab_h) + pad), (96, 96, 96))
    draw = ImageDraw.Draw(sheet)
    font = _label_font(22)
    for i, (im, lab) in enumerate(zip(pages, labels)):
        x = pad + (i % cols) * (w + pad)
        y = pad + (i // cols) * (h + pad + lab_h)
        sheet.paste(im, (x, y))
        draw.text((x, y + h + 4), f"{i + 1}. {lab}", fill=(255, 255, 255), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)


def tent_view(page: Image.Image, out: FsPath) -> None:
    """Both faces of one sheet as people at the table will see them."""
    w, h = page.size
    front = page.crop((0, h // 2, w, h))  # bottom half, upright
    back = page.crop((0, 0, w, h // 2)).rotate(180)  # top half, turned upright
    pad, lab_h = 20, 40
    canvas = Image.new("RGB", (w + 2 * pad, 2 * (h // 2) + 3 * pad + 2 * lab_h), (96, 96, 96))
    draw = ImageDraw.Draw(canvas)
    font = _label_font(26)
    y = pad
    for title, im in (("Сторона 1 (нижняя половина листа)", front), ("Сторона 2 (верхняя половина, после сгиба)", back)):
        draw.text((pad, y), title, fill=(255, 255, 255), font=font)
        canvas.paste(im, (pad, y + lab_h))
        y += lab_h + im.height + pad
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, optimize=True)
