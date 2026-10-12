"""Loading names.txt and style.toml."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path as FsPath
from typing import Any

BBoxT = tuple[float, float, float, float]


@dataclass(frozen=True)
class SheetCfg:
    width: float = 297.0
    height: float = 210.0
    bleed: float = 3.0


@dataclass(frozen=True)
class FaceCfg:
    margin: float = 8.0
    fold_margin: float = 8.0


@dataclass(frozen=True)
class NameCfg:
    font: str = "assets/fonts/Neucha.ttf"
    variations: dict[str, float] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=lambda: {"kern": True, "liga": True})
    uppercase: bool = True
    embolden: float = 0.045
    max_cap: float = 34.0
    max_cap_multiline: float = 30.0
    one_line_min_cap: float = 24.0
    min_cap: float = 16.0
    two_line_gain: float = 1.08
    line_gap: float = 0.32
    min_line_chars: int = 3
    v_center: float = 0.5
    box: BBoxT = (46.0, 8.0, 232.0, 97.0)


@dataclass(frozen=True)
class EmblemCfg:
    enabled: bool = True
    center: tuple[float, float] = (264.0, 52.5)
    width: float = 42.0
    hat_width: float = 0.72
    hat_drop: float = 0.09
    knockout: float = 1.1


@dataclass(frozen=True)
class HolesCfg:
    enabled: bool = True
    zone: BBoxT = (8.0, 8.0, 41.0, 97.0)
    count: int = 3
    radius: tuple[float, float] = (3.2, 4.2)
    splatter: bool = True
    gap: float = 0.6


@dataclass(frozen=True)
class Style:
    sheet: SheetCfg
    face: FaceCfg
    colors: dict[str, tuple[float, float, float, float]]
    name: NameCfg
    emblem: EmblemCfg
    holes: HolesCfg
    root: FsPath  # paths in the style file are relative to this directory

    @property
    def face_h(self) -> float:
        return self.sheet.height / 2

    @property
    def safe(self) -> BBoxT:
        """Safe zone of one face (face coordinates: y = 0 at the table edge,
        y = face_h at the fold)."""
        m, fm = self.face.margin, self.face.fold_margin
        return (m, m, self.sheet.width - m, self.face_h - fm)

    def font_path(self) -> FsPath:
        p = FsPath(self.name.font)
        return p if p.is_absolute() else self.root / p


def _tuple(v: Any, n: int, key: str) -> tuple:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise ValueError(f"style.toml: {key} должен быть списком из {n} чисел")
    return tuple(float(x) for x in v)


def _section(data: dict, name: str, cls, tuples: dict[str, int] | None = None):
    raw = dict(data.get(name, {}))
    known = set(cls.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"style.toml [{name}]: неизвестные ключи {sorted(unknown)}")
    for key, n in (tuples or {}).items():
        if key in raw:
            raw[key] = _tuple(raw[key], n, f"[{name}].{key}")
    return cls(**raw)


def load_style(path: FsPath) -> Style:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    unknown = set(data) - {"sheet", "face", "colors", "name", "emblem", "holes"}
    if unknown:
        raise ValueError(f"style.toml: неизвестные разделы {sorted(unknown)}")
    colors = {k: _tuple(v, 4, f"[colors].{k}") for k, v in data.get("colors", {}).items()}
    colors.setdefault("black", (0.0, 0.0, 0.0, 100.0))
    colors.setdefault("red", (0.0, 100.0, 100.0, 10.0))
    colors.setdefault("white", (0.0, 0.0, 0.0, 0.0))
    colors.setdefault("fold_mark", (0.0, 0.0, 0.0, 40.0))
    for k, c in colors.items():
        if any(not 0 <= v <= 100 for v in c):
            raise ValueError(f"[colors].{k}: значения CMYK должны быть от 0 до 100")
        if sum(c) > 300:
            raise ValueError(f"[colors].{k}: сумма красок {sum(c):.0f}% больше 300%")
    style = Style(
        sheet=_section(data, "sheet", SheetCfg),
        face=_section(data, "face", FaceCfg),
        colors=colors,
        name=_section(data, "name", NameCfg, {"box": 4}),
        emblem=_section(data, "emblem", EmblemCfg, {"center": 2}),
        holes=_section(data, "holes", HolesCfg, {"zone": 4, "radius": 2}),
        root=path.resolve().parent,
    )
    return style


def normalize_name(raw: str, uppercase: bool) -> str:
    s = unicodedata.normalize("NFC", raw)
    s = re.sub(r"\s*\|\s*", "|", s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s.upper() if uppercase else s


def load_names(path: FsPath, uppercase: bool = True) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(normalize_name(line, uppercase))
    if not names:
        raise ValueError(f"{path}: нет ни одного ника")
    return names
