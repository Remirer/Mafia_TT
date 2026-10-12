"""Card face layout (face coordinates, mm: x along the sheet, y = 0 at the
table edge, y = 105 at the fold).

    ┌──────────────────────────────── fold ───────────────────────────────┐
    │ holes │                 NICKNAME (1–2 lines)               │ emblem │
    └───────────────────────────── table edge ────────────────────────────┘
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path as FsPath

from .config import EmblemCfg, NameCfg, Style
from .icons import Icon, bullet_holes, load_svg_icon
from .paths import (
    BBox,
    Path,
    Shape,
    box_inside,
    boxes_overlap,
    chain,
    scale,
    transform_path,
    translate,
    union_bbox,
)
from .text import HbFont, Line

ICON_DIR = FsPath(__file__).resolve().parent.parent / "assets" / "icons"


# ------------------------------------------------------------------ nickname


@dataclass
class NameLayout:
    lines: list[str]
    scale: float  # mm per font unit
    cap_mm: float
    stroke_mm: float
    shapes: list[Shape]
    bbox: BBox
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Block:
    lines: list[Line]
    scale: float
    imbalance: float


def _block(font: HbFont, texts: list[str], cfg: NameCfg, cap: float) -> _Block:
    lines = [font.shape(t) for t in texts]
    pitch = cap * (1 + cfg.line_gap)
    width = max(l.ink[2] - l.ink[0] for l in lines)
    top = max(l.ink[3] - i * pitch for i, l in enumerate(lines))
    bottom = min(l.ink[1] - i * pitch for i, l in enumerate(lines))
    e = cfg.embolden * cap  # stroke width in font units
    bx0, by0, bx1, by1 = cfg.box
    max_cap = cfg.max_cap if len(lines) == 1 else cfg.max_cap_multiline
    s = min(max_cap / cap, (bx1 - bx0) / (width + e), (by1 - by0) / (top - bottom + e))
    widths = [l.ink[2] - l.ink[0] for l in lines]
    return _Block(lines, s, (max(widths) - min(widths)) / max(widths))


def _candidates(text: str, cfg: NameCfg) -> tuple[list[str], list[list[str]]]:
    """Single-line layout and possible two-line splits ('|' forces a split)."""
    if "|" in text:
        return [], [[p.strip() for p in text.split("|") if p.strip()]]
    words = text.split()
    splits = []
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if min(len(a.replace(" ", "")), len(b.replace(" ", ""))) >= cfg.min_line_chars:
            splits.append([a, b])
    return [text], splits


def layout_name(font: HbFont, text: str, cfg: NameCfg, prefer_cx: float | None = None) -> NameLayout:
    """Fit a nickname into cfg.box: one line if the letters stay big enough
    (≥ one_line_min_cap), otherwise the best balanced two-line split.

    The block is centred on `prefer_cx` (the middle of the card) when it fits,
    otherwise pushed just enough to stay inside the box.
    """
    cap = font.cap_height()
    single, splits = _candidates(text, cfg)
    best: _Block | None = _block(font, single, cfg, cap) if single else None
    if splits and (best is None or best.scale * cap < cfg.one_line_min_cap):
        multi = [_block(font, s, cfg, cap) for s in splits]
        top = max(multi, key=lambda b: (round(b.scale, 9), -b.imbalance))
        if best is None or top.scale >= best.scale * cfg.two_line_gain:
            best = top
    assert best is not None

    s = best.scale
    pitch = cap * (1 + cfg.line_gap)
    e_mm = cfg.embolden * cap * s
    bx0, by0, bx1, by1 = cfg.box
    half = (max(l.ink[2] - l.ink[0] for l in best.lines) * s + e_mm) / 2
    cx = (bx0 + bx1) / 2 if prefer_cx is None else prefer_cx
    cx = min(max(cx, bx0 + half), bx1 - half)
    n = len(best.lines)
    # Centre the cap-height block (cap line of line 1 … baseline of last line).
    cap_mid = (cap + (-(n - 1) * pitch)) / 2
    ty = by0 + (by1 - by0) * cfg.v_center - s * cap_mid
    # Keep descenders/accents inside the box.
    ink_top = max(l.ink[3] - i * pitch for i, l in enumerate(best.lines)) * s + ty + e_mm / 2
    ink_bot = min(l.ink[1] - i * pitch for i, l in enumerate(best.lines)) * s + ty - e_mm / 2
    if ink_top > by1:
        ty -= ink_top - by1
    elif ink_bot < by0:
        ty += by0 - ink_bot

    shapes: list[Shape] = []
    for i, line in enumerate(best.lines):
        tx = cx - s * (line.ink[0] + line.ink[2]) / 2
        t = chain(translate(0, -i * pitch), scale(s), translate(tx, ty))
        for p in font.line_paths(line):
            shapes.append(Shape(transform_path(p, t), "black", paint="B", width=e_mm, tag="name"))

    bbox = union_bbox(sh.bbox for sh in shapes)
    cap_mm = cap * s
    warnings = []
    if cap_mm < cfg.min_cap:
        warnings.append(
            f"«{text}»: высота букв {cap_mm:.1f} мм меньше min_cap={cfg.min_cap:g} мм — "
            "сократите ник или задайте перенос символом |"
        )
    return NameLayout([l.text for l in best.lines], s, cap_mm, e_mm, shapes, bbox, warnings)


# ------------------------------------------------------------------ emblem


def _subpaths(path: Path) -> list[Path]:
    out: list[Path] = []
    for seg in path:
        if seg[0] == "M":
            out.append([])
        out[-1].append(seg)
    return out


def _area(path: Path) -> float:
    pts = [seg[-1] for seg in path if seg[0] != "Z"]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


@dataclass
class EmblemIcons:
    hat: Icon
    gun: Icon

    @classmethod
    def load(cls, icon_dir: FsPath = ICON_DIR) -> "EmblemIcons":
        return cls(load_svg_icon(icon_dir / "fedora.svg"), load_svg_icon(icon_dir / "thompson-m1928.svg"))


def emblem_shapes(icons: EmblemIcons, cfg: EmblemCfg) -> list[Shape]:
    """Two crossed Thompson guns with a fedora on top; red hat band.

    The hat gets a white knockout outline so the guns behind it never show
    through the gaps around the band, and the red band never touches black.
    """
    W = cfg.width
    gun, hat = icons.gun, icons.hat
    k = W / gun.w
    centre = translate(-gun.w / 2, -gun.h / 2)
    gun_a = transform_path(gun.path, chain(centre, scale(k)))
    gun_b = transform_path(gun.path, chain(centre, scale(-k, k)))

    hw = W * cfg.hat_width
    kh = hw / hat.w
    hat_t = chain(scale(kh), translate(-hw / 2, -W * cfg.hat_drop))
    parts = _subpaths(hat.path)
    band = min(parts, key=_area)  # the band is the smallest separate contour
    body = [p for p in parts if p is not band]

    shapes = [Shape(gun_a, "black", tag="emblem"), Shape(gun_b, "black", tag="emblem")]
    for p in parts:
        shapes.append(
            Shape(transform_path(p, hat_t), "white", paint="B", width=2 * cfg.knockout, tag="emblem")
        )
    for p in body:
        shapes.append(Shape(transform_path(p, hat_t), "black", tag="emblem"))
    shapes.append(Shape(transform_path(band, hat_t), "red", tag="emblem"))

    # Centre the whole emblem (ink box) on cfg.center.
    bx = union_bbox(s.bbox for s in shapes)
    dx = cfg.center[0] - (bx[0] + bx[2]) / 2
    dy = cfg.center[1] - (bx[1] + bx[3]) / 2
    return [
        Shape(transform_path(s.path, translate(dx, dy)), s.color, s.paint, s.width, s.tag)
        for s in shapes
    ]


# ------------------------------------------------------------------ face


@dataclass
class Face:
    name: str  # "" for the blank card
    shapes: list[Shape]
    layout: NameLayout | None  # None for the blank card
    warnings: list[str]


class LayoutError(ValueError):
    pass


# Seed for the bullet holes of the blank card (same holes on every copy).
BLANK_SEED = "пустая табличка"


def _decorations(seed: str, style: Style, icons: EmblemIcons | None) -> tuple[list[Shape], dict[str, BBox], list[str]]:
    """Emblem + bullet holes, with their ink boxes and safe-zone problems."""
    shapes: list[Shape] = []
    boxes: dict[str, BBox] = {}
    if style.emblem.enabled:
        if icons is None:
            raise LayoutError("emblem enabled but icons not loaded")
        em = emblem_shapes(icons, style.emblem)
        boxes["эмблема"] = union_bbox(s.bbox for s in em)
        shapes += em
    if style.holes.enabled:
        h = style.holes
        holes = bullet_holes(seed, h.zone, h.count, h.radius, h.splatter, h.gap)
        boxes["пулевые отверстия"] = union_bbox(s.bbox for s in holes)
        shapes += holes
    problems = [
        f"{label} выходит за безопасную зону {style.safe}: {tuple(round(v, 2) for v in bx)}"
        for label, bx in boxes.items()
        if not box_inside(bx, style.safe)
    ]
    return shapes, boxes, problems


def build_face(name: str, style: Style, font: HbFont, icons: EmblemIcons | None) -> Face:
    nl = layout_name(font, name, style.name, prefer_cx=style.sheet.width / 2)
    shapes, deco_boxes, problems = _decorations(name.replace("|", " "), style, icons)
    shapes += nl.shapes
    if not box_inside(nl.bbox, style.name.box):
        problems.append(f"ник выходит за зону name.box {style.name.box}: {nl.bbox}")
    for label, bx in deco_boxes.items():
        if boxes_overlap(bx, nl.bbox, gap=1.0):
            problems.append(f"{label} пересекается с ником")
    if problems:
        raise LayoutError(f"«{name}»: " + "; ".join(problems))
    return Face(name, shapes, nl, list(nl.warnings))


def build_blank_face(style: Style, icons: EmblemIcons | None) -> Face:
    """Card without a nickname: decorations only, the name zone stays empty
    for writing the nickname by hand with a marker."""
    shapes, deco_boxes, problems = _decorations(BLANK_SEED, style, icons)
    for label, bx in deco_boxes.items():
        if boxes_overlap(bx, style.name.box):
            problems.append(f"{label} заходит в зону ника name.box — там должно остаться место под маркер")
    if problems:
        raise LayoutError("пустая табличка: " + "; ".join(problems))
    return Face("", shapes, None, [])
