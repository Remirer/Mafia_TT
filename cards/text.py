"""Text shaping and glyph outlines (HarfBuzz), in font units.

One engine does both shaping (kerning, ligatures, variable-font deltas) and
outline extraction, so advances and outlines always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path as FsPath
from typing import Mapping

import uharfbuzz as hb

from .paths import BBox, Path, SegPen, path_bbox, transform_path, translate, union_bbox


class MissingGlyphError(ValueError):
    pass


@dataclass
class PlacedGlyph:
    gid: int
    x: float  # font units, pen position + offset
    y: float


@dataclass
class Line:
    text: str
    glyphs: list[PlacedGlyph]
    advance: float
    ink: BBox  # font units, relative to the line origin (baseline at y = 0)


class HbFont:
    def __init__(
        self,
        path: FsPath,
        variations: Mapping[str, float] | None = None,
        features: Mapping[str, bool] | None = None,
    ) -> None:
        self.path = FsPath(path)
        blob = hb.Blob.from_file_path(str(self.path))
        self.face = hb.Face(blob)
        self.font = hb.Font(self.face)
        self.upem = self.face.upem
        if variations:
            axes = {a.tag: a for a in self.face.axis_infos}
            for tag, val in variations.items():
                if tag not in axes:
                    raise ValueError(
                        f"шрифт {self.path.name}: нет оси {tag!r} (есть: {', '.join(axes) or 'нет осей'})"
                    )
                ax = axes[tag]
                if not ax.min_value <= val <= ax.max_value:
                    raise ValueError(
                        f"шрифт {self.path.name}: {tag}={val} вне диапазона {ax.min_value}..{ax.max_value}"
                    )
            self.font.set_variations(dict(variations))
        self.features = dict(features or {"kern": True, "liga": True})
        self._outlines: dict[int, tuple[Path, BBox | None]] = {}

    # ------------------------------------------------------------ outlines

    def outline(self, gid: int) -> tuple[Path, BBox | None]:
        """Glyph outline (font units) and its ink box (None for blank glyphs)."""
        if gid not in self._outlines:
            pen = SegPen()
            self.font.draw_glyph_with_pen(gid, pen)
            path = pen.path
            self._outlines[gid] = (path, path_bbox(path) if path else None)
        return self._outlines[gid]

    def glyph_for(self, ch: str) -> int:
        gid = self.font.get_nominal_glyph(ord(ch))
        if not gid:
            raise MissingGlyphError(f"в шрифте {self.path.name} нет символа {ch!r}")
        return gid

    def cap_height(self) -> float:
        """Measured cap height: top of the Cyrillic «Н» (falls back to Latin H)."""
        for ch in ("Н", "H"):
            try:
                _, bb = self.outline(self.glyph_for(ch))
            except MissingGlyphError:
                continue
            if bb:
                return bb[3]
        raise MissingGlyphError(f"в шрифте {self.path.name} нет ни «Н», ни «H»")

    # ------------------------------------------------------------ shaping

    def shape(self, text: str) -> Line:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, self.features)
        glyphs: list[PlacedGlyph] = []
        x = 0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            if info.codepoint == 0:
                ch = text[info.cluster] if info.cluster < len(text) else "?"
                raise MissingGlyphError(f"в шрифте {self.path.name} нет символа {ch!r} (в «{text}»)")
            glyphs.append(PlacedGlyph(info.codepoint, x + pos.x_offset, pos.y_offset))
            x += pos.x_advance
        boxes = []
        for g in glyphs:
            _, bb = self.outline(g.gid)
            if bb:
                boxes.append((bb[0] + g.x, bb[1] + g.y, bb[2] + g.x, bb[3] + g.y))
        if not boxes:
            raise ValueError(f"пустая строка: «{text}»")
        return Line(text, glyphs, x, union_bbox(boxes))

    def line_paths(self, line: Line) -> list[Path]:
        """One path per visible glyph, in font units, positioned on the line."""
        out = []
        for g in line.glyphs:
            path, bb = self.outline(g.gid)
            if bb:
                out.append(transform_path(path, translate(g.x, g.y)))
        return out
