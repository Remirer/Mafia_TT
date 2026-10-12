"""Vector paths in millimetres and their serialisation to PDF operators.

A path is a list of segments:
    ("M", (x, y))                      move to
    ("L", (x, y))                      line to
    ("C", (x1, y1), (x2, y2), (x3, y3))  cubic Bézier
    ("Z",)                             close subpath

Coordinates are y-up (PDF convention). Everything that ends up on the page
(glyphs, icons, bullet holes) is converted to this form, so the PDF writer
only has to know five operators.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from fontTools.misc.bezierTools import calcCubicBounds
from fontTools.pens.basePen import BasePen

Point = tuple[float, float]
Segment = tuple
Path = list[Segment]
BBox = tuple[float, float, float, float]  # x0, y0, x1, y1

# PDF paint operators we allow: fill (nonzero), fill + stroke (nonzero), stroke.
PAINTS = ("f", "B", "S")


class SegPen(BasePen):
    """fontTools pen that records M/L/C/Z segments.

    BasePen converts quadratic curves (TrueType outlines, SVG Q/T) into exact
    cubic segments, so the output only contains cubic Béziers.
    """

    def __init__(self) -> None:
        super().__init__(glyphSet=None)
        self.path: Path = []
        self._open = False

    def _moveTo(self, pt):
        self.path.append(("M", tuple(pt)))
        self._open = True

    def _lineTo(self, pt):
        self.path.append(("L", tuple(pt)))

    def _curveToOne(self, p1, p2, p3):
        self.path.append(("C", tuple(p1), tuple(p2), tuple(p3)))

    def _closePath(self):
        self.path.append(("Z",))
        self._open = False

    def _endPath(self):
        # Open subpaths are closed implicitly by a fill anyway; close them
        # explicitly so the bbox/inside tests treat them consistently.
        if self._open:
            self.path.append(("Z",))
        self._open = False


@dataclass
class Shape:
    """One painted path: one glyph, one icon, one crack..."""

    path: Path
    color: str  # palette key, e.g. "black", "red", "white"
    paint: str = "f"
    width: float = 0.0  # stroke width in mm (for "B" and "S")
    tag: str = ""  # what it is: "name", "emblem", "hole", "splatter", ...
    _bbox: BBox | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.paint not in PAINTS:
            raise ValueError(f"unknown paint operator {self.paint!r}")

    @property
    def bbox(self) -> BBox:
        """Ink bounding box, including half the stroke width."""
        if self._bbox is None:
            x0, y0, x1, y1 = path_bbox(self.path)
            h = self.width / 2 if self.paint in ("B", "S") else 0.0
            self._bbox = (x0 - h, y0 - h, x1 + h, y1 + h)
        return self._bbox


# ---------------------------------------------------------------- transforms

Affine = tuple[float, float, float, float, float, float]  # a b c d e f
IDENTITY: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def affine_mul(t1: Affine, t2: Affine) -> Affine:
    """Return t1 ∘ t2: apply t2 first, then t1."""
    a1, b1, c1, d1, e1, f1 = t1
    a2, b2, c2, d2, e2, f2 = t2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def translate(dx: float, dy: float) -> Affine:
    return (1.0, 0.0, 0.0, 1.0, dx, dy)


def scale(sx: float, sy: float | None = None) -> Affine:
    return (sx, 0.0, 0.0, sx if sy is None else sy, 0.0, 0.0)


def rotate(deg: float) -> Affine:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (c, s, -s, c, 0.0, 0.0)


def chain(*ts: Affine) -> Affine:
    """chain(A, B, C) applies A first, then B, then C."""
    out = IDENTITY
    for t in ts:
        out = affine_mul(t, out)
    return out


def apply(t: Affine, p: Point) -> Point:
    a, b, c, d, e, f = t
    x, y = p
    return (a * x + c * y + e, b * x + d * y + f)


def transform_path(path: Path, t: Affine) -> Path:
    out: Path = []
    for seg in path:
        op = seg[0]
        if op == "Z":
            out.append(seg)
        else:
            out.append((op, *(apply(t, p) for p in seg[1:])))
    return out


# ---------------------------------------------------------------- geometry


def path_bbox(path: Path) -> BBox:
    xs: list[float] = []
    ys: list[float] = []
    cur: Point | None = None
    for seg in path:
        op = seg[0]
        if op in ("M", "L"):
            cur = seg[1]
            xs.append(cur[0])
            ys.append(cur[1])
        elif op == "C":
            assert cur is not None, "curve without current point"
            x0, y0, x1, y1 = calcCubicBounds(cur, seg[1], seg[2], seg[3])
            xs += [x0, x1]
            ys += [y0, y1]
            cur = seg[3]
    if not xs:
        raise ValueError("empty path")
    return (min(xs), min(ys), max(xs), max(ys))


def union_bbox(boxes: Iterable[BBox]) -> BBox:
    boxes = list(boxes)
    if not boxes:
        raise ValueError("no boxes")
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def boxes_overlap(a: BBox, b: BBox, gap: float = 0.0) -> bool:
    return not (
        a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1]
    )


def box_inside(inner: BBox, outer: BBox, eps: float = 1e-6) -> bool:
    return (
        inner[0] >= outer[0] - eps
        and inner[1] >= outer[1] - eps
        and inner[2] <= outer[2] + eps
        and inner[3] <= outer[3] + eps
    )


def flatten(path: Path, steps: int = 12) -> list[list[Point]]:
    """Approximate a path by polylines (one per subpath)."""
    polys: list[list[Point]] = []
    cur: list[Point] = []
    p0: Point | None = None
    for seg in path:
        op = seg[0]
        if op == "M":
            if cur:
                polys.append(cur)
            cur = [seg[1]]
            p0 = seg[1]
        elif op == "L":
            cur.append(seg[1])
            p0 = seg[1]
        elif op == "C":
            assert p0 is not None
            (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p0, seg[1], seg[2], seg[3]
            for i in range(1, steps + 1):
                t = i / steps
                mt = 1 - t
                cur.append(
                    (
                        mt**3 * x0 + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t**3 * x3,
                        mt**3 * y0 + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t**3 * y3,
                    )
                )
            p0 = seg[3]
        elif op == "Z":
            if cur:
                polys.append(cur)
            cur = []
    if cur:
        polys.append(cur)
    return polys


def smooth_closed(points: Sequence[Point], tension: float = 1.0) -> Path:
    """Closed Catmull-Rom spline through points, as cubic Béziers."""
    n = len(points)
    path: Path = [("M", tuple(points[0]))]
    for i in range(n):
        p0 = points[(i - 1) % n]
        p1 = points[i]
        p2 = points[(i + 1) % n]
        p3 = points[(i + 2) % n]
        k = tension / 6.0
        c1 = (p1[0] + (p2[0] - p0[0]) * k, p1[1] + (p2[1] - p0[1]) * k)
        c2 = (p2[0] - (p3[0] - p1[0]) * k, p2[1] - (p3[1] - p1[1]) * k)
        path.append(("C", c1, c2, tuple(p2)))
    path.append(("Z",))
    return path


def polygon(points: Sequence[Point]) -> Path:
    path: Path = [("M", tuple(points[0]))]
    path += [("L", tuple(p)) for p in points[1:]]
    path.append(("Z",))
    return path


def ellipse(cx: float, cy: float, rx: float, ry: float, rot_deg: float = 0.0) -> Path:
    """Ellipse from four cubic arcs (kappa approximation)."""
    k = 0.5522847498307936
    pts = [
        ("M", (rx, 0.0)),
        ("C", (rx, k * ry), (k * rx, ry), (0.0, ry)),
        ("C", (-k * rx, ry), (-rx, k * ry), (-rx, 0.0)),
        ("C", (-rx, -k * ry), (-k * rx, -ry), (0.0, -ry)),
        ("C", (k * rx, -ry), (rx, -k * ry), (rx, 0.0)),
        ("Z",),
    ]
    return transform_path(pts, chain(rotate(rot_deg), translate(cx, cy)))


# ---------------------------------------------------------------- PDF output


def num(v: float) -> str:
    """Compact PDF number: 3 decimals (1 µm in mm space), no trailing zeros."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def path_ops(path: Path) -> str:
    out: list[str] = []
    for seg in path:
        op = seg[0]
        if op == "M":
            out.append(f"{num(seg[1][0])} {num(seg[1][1])} m")
        elif op == "L":
            out.append(f"{num(seg[1][0])} {num(seg[1][1])} l")
        elif op == "C":
            (a, b), (c, d), (e, f) = seg[1], seg[2], seg[3]
            out.append(f"{num(a)} {num(b)} {num(c)} {num(d)} {num(e)} {num(f)} c")
        elif op == "Z":
            out.append("h")
        else:
            raise ValueError(f"bad segment {seg!r}")
    return "\n".join(out)


def cmyk_ops(cmyk: Sequence[float], stroke: bool = False) -> str:
    """CMYK in percent (0..100) → DeviceCMYK colour operator."""
    vals = " ".join(num(v / 100.0) for v in cmyk)
    return f"{vals} {'K' if stroke else 'k'}"
