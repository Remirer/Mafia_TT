from pathlib import Path

import pytest
from pypdf import PdfReader

from cards.__main__ import main, slug
from cards.config import load_names, load_style, normalize_name
from cards.layout import EmblemIcons, build_blank_face, build_face, layout_name
from cards.paths import apply, box_inside, boxes_overlap, flatten
from cards.pdf import PT_PER_MM, PageGeometry, face_page_ops, shapes_ops, sheet_ops, write_pdf
from cards.text import HbFont, MissingGlyphError
from cards.preview import screen_palette
from cards.verify import verify_pdf

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def style():
    return load_style(ROOT / "style.toml")


@pytest.fixture(scope="module")
def font(style):
    return HbFont(style.font_path(), style.name.variations, style.name.features)


@pytest.fixture(scope="module")
def names(style):
    return load_names(ROOT / "names.txt", style.name.uppercase)


@pytest.fixture(scope="module")
def icons():
    return EmblemIcons.load()


def test_names_file(names):
    assert len(names) == 17
    assert names[0] == "КОРОЛЬ ТОМ"
    assert "ДЕН ФАРТ YG" in names


def test_normalize_name():
    assert normalize_name("  гога нут | футбол 28 ", True) == "ГОГА НУТ|ФУТБОЛ 28"
    assert normalize_name("р2ришник", False) == "р2ришник"


def test_every_name_has_glyphs(font, names):
    for n in names:
        font.shape(n.replace("|", " "))


def test_missing_glyph_is_an_error(font):
    with pytest.raises(MissingGlyphError):
        font.shape("☃")


def test_short_name_one_line_at_max_size(font, style):
    nl = layout_name(font, "ЭВА", style.name)
    assert nl.lines == ["ЭВА"]
    assert nl.cap_mm == pytest.approx(style.name.max_cap)


def test_long_name_goes_to_two_lines(font, style):
    nl = layout_name(font, "ГОГА НУТ ФУТБОЛ 28", style.name)
    assert nl.lines == ["ГОГА НУТ", "ФУТБОЛ 28"]


def test_forced_line_break(font, style):
    assert layout_name(font, "КОРОЛЬ|ТОМ", style.name).lines == ["КОРОЛЬ", "ТОМ"]


def test_faces_fit_and_do_not_collide(font, style, names, icons):
    for n in names:
        face = build_face(n, style, font, icons)  # raises LayoutError on overlap / leaving the zones
        assert box_inside(face.layout.bbox, style.name.box)
        assert all(box_inside(s.bbox, style.safe) for s in face.shapes)


def test_red_ink_never_touches_black(font, style, names, icons):
    """Red droplets keep a white gap to black ink (no misregistration slivers)."""
    gap = style.holes.gap
    for n in names[:5]:
        face = build_face(n, style, font, icons)
        black = [p for s in face.shapes if s.tag == "hole" for p in flatten(s.path, 8)]
        for s in (s for s in face.shapes if s.tag == "splatter"):
            for poly in flatten(s.path, 8):
                for x, y in poly:
                    for bp in black:
                        assert min((x - bx) ** 2 + (y - by) ** 2 for bx, by in bp) ** 0.5 > gap * 0.5


def test_blank_card_keeps_name_zone_empty(style, icons):
    face = build_blank_face(style, icons)
    assert face.layout is None and face.name == ""
    assert {s.tag for s in face.shapes} == {"emblem", "hole", "splatter"}
    assert all(box_inside(s.bbox, style.safe) for s in face.shapes)
    assert not any(boxes_overlap(s.bbox, style.name.box) for s in face.shapes)


def test_preview_pdf_is_one_rgb_face_per_page(tmp_path, font, style, names, icons):
    faces = [build_face(n, style, font, icons) for n in names[:2]] + [build_blank_face(style, icons)]
    rgb = screen_palette(style.colors, None)
    out = tmp_path / "preview.pdf"
    write_pdf(
        out,
        [face_page_ops(shapes_ops(f.shapes, style.colors, rgb=rgb)) for f in faces],
        PageGeometry(297, 105, 0),
        title="предпросмотр",
    )
    rep = verify_pdf(out, kind="preview", palette=style.colors, rgb_palette=rgb, pages=3)
    assert rep.ok, rep.errors
    page = PdfReader(out).pages[0]
    assert float(page.mediabox.width) == pytest.approx(297 * PT_PER_MM, abs=0.01)
    assert float(page.mediabox.height) == pytest.approx(105 * PT_PER_MM, abs=0.01)
    ops = page.get_contents().get_data()
    assert b" rg" in ops and b" k\n" not in ops  # DeviceRGB only, no CMYK, no rotated copy
    assert b"-1 0 0 -1" not in ops


def test_top_half_is_rotated_180():
    # the matrix used in sheet_ops for the top half
    t = (-1.0, 0.0, 0.0, -1.0, 297.0, 210.0)
    assert apply(t, (10.0, 20.0)) == (287.0, 190.0)
    assert b"-1 0 0 -1 297 210 cm" in sheet_ops("0 0 m 1 1 l h f", sheet_w=297, sheet_h=210, offset=0)


def test_print_pdf_passes_checks(tmp_path, font, style, names, icons):
    faces = [build_face(n, style, font, icons) for n in names[:3]]
    ops = [sheet_ops(shapes_ops(f.shapes, style.colors), sheet_w=297, sheet_h=210, offset=3) for f in faces]
    out = tmp_path / "print.pdf"
    write_pdf(out, ops, PageGeometry(297, 210, 3), title="тест", pdfx=True, icc=None)
    rep = verify_pdf(out, kind="print", palette=style.colors, pages=3, render=False)
    assert rep.ok, rep.errors


def test_verify_rejects_rgb_and_ink_outside_safe_zone(tmp_path, style):
    out = tmp_path / "bad.pdf"
    write_pdf(out, [b"q 1 0 0 rg 0 0 50 50 re f Q\n"], PageGeometry(297, 210, 0), title="bad")
    rep = verify_pdf(out, kind="office", palette=style.colors, render=False)
    assert any("rg" in e for e in rep.errors)

    out2 = tmp_path / "edge.pdf"
    write_pdf(out2, [b"0 0 0 1 k 0 0 m 10 0 l 10 10 l h f\n"], PageGeometry(297, 210, 0), title="edge")
    rep2 = verify_pdf(out2, kind="office", palette=style.colors, render=False)
    assert any("безопасной зоны" in e for e in rep2.errors)


def test_cli_build_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1791849600")
    a, b = tmp_path / "a", tmp_path / "b"
    assert main(["--out", str(a), "--no-icc", "--no-preview"]) == 0
    assert main(["--out", str(b), "--no-icc", "--no-preview"]) == 0
    for name in (
        "mafia_tablichki_print_FOGRA39.pdf",
        "mafia_tablichki_A4_office.pdf",
        "mafia_tablichka_pustaya_print_FOGRA39.pdf",
        "mafia_tablichka_pustaya_A4_office.pdf",
        "mafia_tablichki_preview.pdf",
    ):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_slug_transliterates_nicknames():
    assert slug("РЫЖИЙ") == "ryzhiy"
    assert slug("ДЕН ФАРТ YG") == "den_fart_yg"
    assert slug("ГОГА НУТ|ФУТБОЛ 28") == "goga_nut_futbol_28"


def test_cli_only_builds_separate_pair(tmp_path):
    assert main(["--out", str(tmp_path), "--no-icc", "--only", "рыжий"]) == 0
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["mafia_tablichka_ryzhiy_A4_office.pdf", "mafia_tablichka_ryzhiy_print_FOGRA39.pdf"]
    for name in files:
        assert len(PdfReader(tmp_path / name).pages) == 1
