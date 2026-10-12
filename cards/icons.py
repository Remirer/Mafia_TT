"""Decorations: SVG silhouettes (emblem) and procedural bullet holes.

Icons come from game-icons.net (CC BY 3.0, see assets/icons/ATTRIBUTION.md).
Their SVGs have a black 512×512 background square plus the white icon path;
we skip the square and keep the icon outline only.
"""

from __future__ import annotations

import math
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path as FsPath

from fontTools.svgLib.path import parse_path

from .paths import (
    BBox,
    Path,
    SegPen,
    Shape,
    ellipse,
    flatten,
    path_bbox,
    polygon,
    smooth_closed,
    transform_path,
    translate,
)

SVG_NS = "{http://www.w3.org/2000/svg}"
_BACKGROUND = {"M0 0h512v512H0z", "M0 0h512v512H0V0z"}


@dataclass
class Icon:
    """Icon outline normalised to its ink box: x ∈ [0, w], y ∈ [0, h], y-up."""

    path: Path
    w: float
    h: float


def load_svg_icon(file: FsPath) -> Icon:
    root = ET.parse(file).getroot()
    vb = [float(v) for v in root.get("viewBox", "0 0 512 512").replace(",", " ").split()]
    vb_h = vb[3]
    pen = SegPen()
    found = False
    for el in root.iter(f"{SVG_NS}path"):
        d = (el.get("d") or "").strip()
        fill = (el.get("fill") or "#000").lower()
        if d in _BACKGROUND or fill in ("#000", "#000000", "black", "none"):
            continue
        if el.get("transform") or el.get("fill-rule") == "evenodd":
            raise ValueError(f"{file}: transforms / evenodd paths are not supported")
        parse_path(d, pen)
        found = True
    if not found:
        raise ValueError(f"{file}: no icon path found")
    path = pen.path
    # SVG is y-down; flip to y-up and move the ink box to the origin.
    path = transform_path(path, (1.0, 0.0, 0.0, -1.0, 0.0, vb_h))
    x0, y0, x1, y1 = path_bbox(path)
    path = transform_path(path, translate(-x0, -y0))
    return Icon(path, x1 - x0, y1 - y0)


# ------------------------------------------------------------------ bullet holes

# Farthest black ink of a hole (crack tips) in units of the core radius.
REACH = 3.25


def _point_in_poly(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xin > x:
                inside = not inside
    return inside


def _dist_to_poly(pt, poly) -> float:
    x, y = pt
    best = math.inf
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L))
        px, py = x1 + t * dx, y1 + t * dy
        best = min(best, math.hypot(x - px, y - py))
    return best


class Clearance:
    """Keeps red ink away from black ink (no touching = no white gaps when the
    printing plates are slightly misregistered)."""

    def __init__(self) -> None:
        self.polys: list[list[tuple[float, float]]] = []

    def add(self, path: Path) -> None:
        self.polys += [p for p in flatten(path, steps=8) if len(p) >= 3]

    def free(self, center, radius: float, gap: float) -> bool:
        for poly in self.polys:
            if _point_in_poly(center, poly) or _dist_to_poly(center, poly) < radius + gap:
                return False
        return True


def _hole(rng: random.Random, cx: float, cy: float, r: float) -> list[Path]:
    """Black parts of one bullet hole: ragged core, torn paper petals, cracks."""
    parts: list[Path] = []

    def polar(a: float, d: float) -> tuple[float, float]:
        return (cx + d * math.cos(a), cy + d * math.sin(a))

    # Ragged core.
    n = rng.randint(14, 19)
    pts = [
        polar(2 * math.pi * i / n + rng.uniform(-0.1, 0.1), r * rng.uniform(0.8, 1.05))
        for i in range(n)
    ]
    parts.append(smooth_closed(pts, tension=0.85))

    # Torn paper: jagged petals around the core, separated by a thin white gap.
    k = rng.randint(3, 5)
    start = rng.uniform(0, 2 * math.pi)
    for i in range(k):
        a0 = start + 2 * math.pi * i / k + rng.uniform(0.04, 0.2)
        span = 2 * math.pi / k * rng.uniform(0.45, 0.75)
        r_in = r * 1.2
        teeth = rng.randint(3, 5)
        inner = [polar(a0 + span * j / 8, r_in) for j in range(9)]
        outer = []
        for j in range(2 * teeth + 1):
            a = a0 + span * j / (2 * teeth)
            if j in (0, 2 * teeth):
                d = r_in + r * 0.06
            elif j % 2:
                d = r * rng.uniform(1.42, 1.7)  # tooth tip
            else:
                d = r * rng.uniform(1.27, 1.36)  # notch
            outer.append(polar(a, d))
        parts.append(polygon(outer + inner[::-1]))

    # Radial cracks: thin tapered zigzags, some with a small branch.
    m = rng.randint(6, 9)
    for i in range(m):
        a = 2 * math.pi * i / m + rng.uniform(-0.22, 0.22)
        d0 = r * 0.7
        length = r * rng.uniform(1.4, 2.5)
        w0 = r * rng.uniform(0.07, 0.11)
        pts_c = [(d0, 0.0)]
        for f in (0.35, 0.7):
            pts_c.append((d0 + length * f, rng.uniform(-0.12, 0.12)))
        pts_c.append((d0 + length, rng.uniform(-0.1, 0.1)))
        left, right = [], []
        for j, (d, da) in enumerate(pts_c):
            w = w0 * (1 - j / (len(pts_c) - 1))
            ang = a + da
            px, py = polar(ang, d)
            nx, ny = -math.sin(ang), math.cos(ang)
            left.append((px + nx * w, py + ny * w))
            right.append((px - nx * w, py - ny * w))
        parts.append(polygon(left + right[::-1][1:]))
        if rng.random() < 0.45:
            # small branch from the middle kink
            d, da = pts_c[1]
            ang = a + da
            bx, by = polar(ang, d)
            ba = ang + rng.choice((-1, 1)) * rng.uniform(0.5, 0.9)
            bl = length * rng.uniform(0.25, 0.4)
            tip = (bx + bl * math.cos(ba), by + bl * math.sin(ba))
            nx, ny = -math.sin(ba), math.cos(ba)
            wb = w0 * 0.55
            parts.append(polygon([(bx + nx * wb, by + ny * wb), tip, (bx - nx * wb, by - ny * wb)]))
    return parts


def _splatter(
    rng: random.Random, cx: float, cy: float, r: float, zone: BBox, clear: Clearance, gap: float
) -> list[Path]:
    """Red droplets thrown away from the hole (never touching black ink)."""
    out: list[Path] = []
    direction = rng.uniform(0, 2 * math.pi)
    tries = 0
    want = rng.randint(6, 10)
    while len(out) < want and tries < 400:
        tries += 1
        a = direction + rng.gauss(0, 0.55)
        dist = r * rng.uniform(2.4, 4.6)
        size = r * rng.uniform(0.07, 0.24) * (1.25 if dist < 3 * r else 0.9)
        px, py = cx + dist * math.cos(a), cy + dist * math.sin(a)
        stretch = rng.uniform(1.0, 2.2)
        rx, ry = size * stretch, size
        if not (zone[0] + rx <= px <= zone[2] - rx and zone[1] + rx <= py <= zone[3] - rx):
            continue
        if not clear.free((px, py), rx, gap):
            continue
        out.append(ellipse(px, py, rx, ry, math.degrees(a)))
        clear.add(out[-1])
    return out


def bullet_holes(
    seed: str,
    zone: BBox,
    count: int,
    radius: tuple[float, float],
    splatter: bool,
    gap: float,
) -> list[Shape]:
    """2–3 bullet holes with cracks (black) and red droplets inside `zone`.

    `seed` (the nickname) makes every card a little different but stable
    between builds.
    """
    rng = random.Random(f"holes:{seed}")
    x0, y0, x1, y1 = zone
    holes: list[tuple[float, float, float, list[Path]]] = []
    attempts = 0
    while len(holes) < count and attempts < 2000:
        attempts += 1
        r = rng.uniform(*radius)
        reach = r * REACH
        if x1 - x0 < 2 * reach or y1 - y0 < 2 * reach:
            continue
        cx = rng.uniform(x0 + reach, x1 - reach)
        cy = rng.uniform(y0 + reach, y1 - reach)
        if any(math.hypot(cx - hx, cy - hy) < (reach + hr * REACH) * 0.62 for hx, hy, hr, _ in holes):
            continue
        parts = _hole(rng, cx, cy, r)
        bx = path_bbox([seg for p in parts for seg in p])
        if bx[0] < x0 or bx[1] < y0 or bx[2] > x1 or bx[3] > y1:
            continue
        holes.append((cx, cy, r, parts))
    if len(holes) < count:
        raise ValueError(
            f"не удалось разместить {count} пулевых отверстия в зоне {zone}: уменьшите radius_mm или count"
        )
    holes.sort(key=lambda h: -h[1])

    shapes: list[Shape] = []
    clear = Clearance()
    for _, _, _, parts in holes:
        for p in parts:
            clear.add(p)
            shapes.append(Shape(p, "black", tag="hole"))
    if splatter:
        for cx, cy, r, _ in holes:
            for p in _splatter(rng, cx, cy, r, zone, clear, gap):
                shapes.append(Shape(p, "red", tag="splatter"))
    return shapes
