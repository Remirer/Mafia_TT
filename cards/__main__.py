"""python -m cards — build print-ready tent cards from names.txt + style.toml."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path as FsPath
from typing import Mapping, Sequence

from . import preview
from .assets import DEFAULT_CACHE, ICC_NAME, ensure_icc, load_icc
from .config import Style, load_names, load_style
from .layout import EmblemIcons, Face, LayoutError, build_blank_face, build_face
from .paths import cmyk_ops, num
from .pdf import PageGeometry, face_page_ops, shapes_ops, sheet_ops, write_pdf
from .text import HbFont, MissingGlyphError
from .verify import verify_pdf

ROOT = FsPath(__file__).resolve().parent.parent
PRINT_PDF = "mafia_tablichki_print_FOGRA39.pdf"
OFFICE_PDF = "mafia_tablichki_A4_office.pdf"
BLANK_PRINT_PDF = "mafia_tablichka_pustaya_print_FOGRA39.pdf"
BLANK_OFFICE_PDF = "mafia_tablichka_pustaya_A4_office.pdf"
PREVIEW_PDF = "mafia_tablichki_preview.pdf"
CREDITS = "Шрифт: {font} (SIL OFL 1.1). Иконки шляпы и автомата: Lorc, Skoll — game-icons.net (CC BY 3.0)."


def fold_ticks(style: Style) -> str:
    """Short grey ticks at both ends of the fold line (office file only)."""
    y = num(style.sheet.height / 2)
    w = style.sheet.width
    return "\n".join(
        [
            cmyk_ops(style.colors["fold_mark"], stroke=True),
            "0.25 w",
            f"4 {y} m 9 {y} l S",
            f"{num(w - 9)} {y} m {num(w - 4)} {y} l S",
        ]
    )


def _verify(
    path: FsPath, kind: str, style: Style, pages: int | None, rgb: Mapping[str, Sequence[float]] | None = None
) -> bool:
    rep = verify_pdf(
        path,
        kind=kind,
        palette=style.colors,
        sheet=(style.sheet.width, style.sheet.height),
        bleed=style.sheet.bleed,
        margin=style.face.margin,
        fold_margin=style.face.fold_margin,
        pages=pages,
        rgb_palette=rgb,
    )
    status = "OK" if rep.ok else "ОШИБКИ"
    print(f"  Проверка {path.name}: {status}")
    for n in rep.notes:
        print(f"    · {n}")
    for e in rep.errors:
        print(f"    ✗ {e}")
    return rep.ok


def _write_sheets(
    faces: Sequence[Face], style: Style, out: FsPath, names: tuple[str, str], what: str, credits: str, icc: bytes | None
) -> tuple[FsPath, FsPath]:
    """Print (PDF/X-1a, bleed) and office (plain A4) files for these faces."""
    W, H, bleed = style.sheet.width, style.sheet.height, style.sheet.bleed
    ops = [shapes_ops(f.shapes, style.colors) for f in faces]
    print_pdf, office_pdf = out / names[0], out / names[1]
    write_pdf(
        print_pdf,
        [sheet_ops(fo, sheet_w=W, sheet_h=H, offset=bleed) for fo in ops],
        PageGeometry(W, H, bleed),
        title=f"{what} — для типографии (CMYK, FOGRA39, вылеты {num(bleed)} мм)",
        subject=credits,
        pdfx=True,
        icc=icc,
    )
    write_pdf(
        office_pdf,
        [sheet_ops(fo, sheet_w=W, sheet_h=H, offset=0, extra=fold_ticks(style)) for fo in ops],
        PageGeometry(W, H, 0),
        title=f"{what} — A4 для офисного принтера",
        subject=credits,
    )
    return print_pdf, office_pdf


def build(args: argparse.Namespace) -> int:
    style = load_style(args.style)
    names = load_names(args.names, style.name.uppercase)
    font = HbFont(style.font_path(), style.name.variations, style.name.features)
    icons = EmblemIcons.load() if style.emblem.enabled else None

    print(f"Табличек: {len(names)} + пустая. Шрифт: {style.font_path().name}")
    faces: list[Face] = []
    failed = False
    for i, name in enumerate(names, 1):
        try:
            face = build_face(name, style, font, icons)
        except (LayoutError, MissingGlyphError, ValueError) as exc:
            print(f"  ✗ {i:2d}. {name}: {exc}")
            failed = True
            continue
        faces.append(face)
        lines = " / ".join(face.layout.lines)
        print(f"  {i:2d}. {lines:<26} буквы {face.layout.cap_mm:4.1f} мм")
        for w in face.warnings:
            print(f"      ! {w}")
    try:
        blank = build_blank_face(style, icons)
        print("   —  пустая табличка (под маркер)")
    except (LayoutError, ValueError) as exc:
        print(f"  ✗ пустая табличка: {exc}")
        failed = True
    if failed:
        return 1

    icc: bytes | None
    icc_file: FsPath | None
    if args.no_icc:
        icc, icc_file = None, None
    elif args.icc:
        icc, icc_file = load_icc(args.icc), args.icc
    else:
        icc = ensure_icc()
        icc_file = DEFAULT_CACHE / ICC_NAME if icc else None

    out: FsPath = args.out
    credits = CREDITS.format(font=style.font_path().stem)
    print_pdf, office_pdf = _write_sheets(
        faces, style, out, (PRINT_PDF, OFFICE_PDF), "Таблички для мафии", credits, icc
    )
    blank_print, blank_office = _write_sheets(
        [blank], style, out, (BLANK_PRINT_PDF, BLANK_OFFICE_PDF), "Пустая табличка для мафии", credits, icc
    )

    # Screen preview: front faces only (no upside-down halves), sRGB soft proof.
    rgb = preview.screen_palette(style.colors, icc_file)
    preview_pdf = out / PREVIEW_PDF
    write_pdf(
        preview_pdf,
        [face_page_ops(shapes_ops(f.shapes, style.colors, rgb=rgb)) for f in [*faces, blank]],
        PageGeometry(style.sheet.width, style.face_h, 0),
        title="Таблички для мафии — предпросмотр (лицевые стороны, не для печати)",
        subject=credits,
    )
    print("\nГотово:")
    for p in (print_pdf, office_pdf, blank_print, blank_office, preview_pdf):
        print(f"  {p}")

    n = len(faces)
    ok = _verify(print_pdf, "print", style, n)
    ok &= _verify(office_pdf, "office", style, n)
    ok &= _verify(blank_print, "print", style, 1)
    ok &= _verify(blank_office, "office", style, 1)
    ok &= _verify(preview_pdf, "preview", style, n + 1, rgb)

    if not args.no_preview:
        if preview.available():
            pv = out / "preview"
            pages = preview.render(office_pdf, dpi=48, icc=icc_file)
            preview.contact_sheet(pages, [" / ".join(f.layout.lines) for f in faces], pv / "contact_sheet.png")
            preview.tent_view(preview.render(office_pdf, dpi=100, icc=icc_file, first=1, last=1)[0], pv / "tent_view.png")
            print(f"  Превью: {pv / 'contact_sheet.png'}, {pv / 'tent_view.png'}")
        else:
            print("  (pdftoppm не установлен — превью PNG не созданы)")
    return 0 if ok else 1


def _verify_files(paths: Sequence[FsPath], style: Style) -> int:
    ok = True
    for p in paths:
        if "preview" in p.name:
            # the preview may have been built with or without the ICC profile
            cached = DEFAULT_CACHE / ICC_NAME
            rgb = dict(preview.screen_palette(style.colors, None))
            if cached.exists():
                rgb |= {f"{k}*": v for k, v in preview.screen_palette(style.colors, cached).items()}
            ok &= _verify(p, "preview", style, None, rgb)
        else:
            ok &= _verify(p, "print" if "print" in p.name else "office", style, None)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m cards",
        description="Генератор табличек-домиков для мафии: A4, сгиб вдоль, CMYK FOGRA39.",
    )
    ap.add_argument("--names", type=FsPath, default=ROOT / "names.txt", help="файл с никами (по одному в строке)")
    ap.add_argument("--style", type=FsPath, default=ROOT / "style.toml", help="файл настроек")
    ap.add_argument("--out", type=FsPath, default=ROOT / "output", help="папка для результатов")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--icc", type=FsPath, help="свой ICC-профиль печати (например, от типографии)")
    g.add_argument("--no-icc", action="store_true", help="не встраивать профиль (только имя FOGRA39)")
    ap.add_argument("--no-preview", action="store_true", help="не делать PNG-превью")
    ap.add_argument("--verify", type=FsPath, nargs="+", metavar="PDF", help="только проверить готовые PDF")
    args = ap.parse_args(argv)

    if args.verify:
        return _verify_files(args.verify, load_style(args.style))
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
