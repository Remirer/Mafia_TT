"""Pre-flight checks for the generated PDFs (what we can check without Acrobat).

Structure (PDF/X-1a:2003 rules we rely on), colours (only palette CMYK
values, i.e. no RGB and pure-K black), geometry (all ink inside the safe
zones of both faces) and an optional render check that the sheet is
symmetric under a 180° rotation (= the top half really is the upside-down
copy of the bottom half).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path as FsPath
from typing import Mapping, Sequence

from pypdf import PdfReader
from pypdf.generic import ContentStream, NameObject

from .assets import ICC_SHA256, sha256
from .paths import affine_mul, apply
from .pdf import FOGRA39, PT_PER_MM

ALLOWED_OPS = {b"q", b"Q", b"cm", b"m", b"l", b"c", b"h", b"f", b"B", b"S", b"k", b"K", b"w", b"j", b"J"}
FORBIDDEN_KEYS = [
    b"/Font", b"/XObject", b"/ExtGState", b"/Pattern", b"/Shading", b"/SMask", b"/Group",
    b"/Annots", b"/JavaScript", b"/OpenAction", b"/AA", b"/Encrypt", b"/ObjStm", b"/XRef",
    b"/CA ", b"/ca ", b"/BM ", b"/ArtBox", b"/OCProperties", b"/Transparency",
]
DATE_RE = re.compile(r"^D:\d{14}([+-]\d{2}'\d{2}'|Z)$")


@dataclass
class Report:
    path: FsPath
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


def _key(cmyk: Sequence[float]) -> tuple[float, ...]:
    return tuple(round(float(v), 3) for v in cmyk)


def _close(a: float, b: float, tol: float = 0.02) -> bool:
    return abs(a - b) <= tol


def _ink_boxes(ops, rgb: bool = False) -> tuple[list[tuple[tuple, tuple[float, float, float, float], bytes]], list[str]]:
    """Mini content-stream interpreter: bbox (pt) and colour of every painted path.

    DeviceRGB operators (rg/RG) are accepted only for the screen preview.
    """
    allowed_ops = ALLOWED_OPS | {b"rg", b"RG"} if rgb else ALLOWED_OPS
    problems: list[str] = []
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    fill = stroke = (0.0, 0.0, 0.0, 1.0)
    lw = 1.0
    stack: list = []
    pts: list[tuple[float, float]] = []
    out = []
    for operands, op in ops:
        if op not in allowed_ops:
            problems.append(f"недопустимый оператор {op.decode('latin1')!r}")
            continue
        v = [float(x) for x in operands] if op not in (b"q", b"Q") else []
        if op == b"q":
            stack.append((ctm, fill, stroke, lw))
        elif op == b"Q":
            if not stack:
                problems.append("лишний Q")
                continue
            ctm, fill, stroke, lw = stack.pop()
        elif op == b"cm":
            ctm = affine_mul(ctm, tuple(v))
        elif op in (b"m", b"l"):
            pts.append(apply(ctm, (v[0], v[1])))
        elif op == b"c":
            pts += [apply(ctm, (v[i], v[i + 1])) for i in (0, 2, 4)]
        elif op in (b"k", b"rg"):
            fill = _key(v)
        elif op in (b"K", b"RG"):
            stroke = _key(v)
        elif op == b"w":
            lw = v[0]
        elif op in (b"f", b"B", b"S"):
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                grow = 0.0
                if op in (b"B", b"S"):
                    a, b, c, d = ctm[:4]
                    grow = lw / 2 * abs(a * d - b * c) ** 0.5
                box = (min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow)
                if op in (b"f", b"B"):
                    out.append((fill, box, op))
                if op in (b"B", b"S"):
                    out.append((stroke, box, op))
            pts = []
    if stack:
        problems.append("несбалансированные q/Q")
    return out, problems


def verify_pdf(
    path: FsPath,
    *,
    kind: str,
    palette: Mapping[str, Sequence[float]],
    sheet: tuple[float, float] = (297.0, 210.0),
    bleed: float = 3.0,
    margin: float = 8.0,
    fold_margin: float = 8.0,
    pages: int | None = None,
    render: bool = True,
    rgb_palette: Mapping[str, Sequence[float]] | None = None,
) -> Report:
    """kind: "print" (PDF/X-1a, bleed), "office" (plain A4) or "preview"
    (one face per page, DeviceRGB colours from `rgb_palette`)."""
    rep = Report(path)
    raw = path.read_bytes()
    pdfx = kind == "print"
    preview = kind == "preview"
    if preview and rgb_palette is None:
        raise ValueError("для проверки предпросмотра нужна rgb_palette")
    b = bleed if pdfx else 0.0
    W, H = sheet

    # --- file structure
    if not raw.startswith(b"%PDF-1.4"):
        rep.err(f"заголовок {raw[:8]!r}, ожидался %PDF-1.4")
    skeleton = re.sub(rb"stream\r?\n.*?endstream", b"", raw, flags=re.S)
    for key in FORBIDDEN_KEYS:
        if key in skeleton:
            rep.err(f"в файле есть недопустимый ключ {key.decode().strip()}")

    reader = PdfReader(path)
    ids = reader.trailer.get("/ID")
    if not ids or len(ids) != 2:
        rep.err("нет /ID в трейлере")
    if pages is not None and len(reader.pages) != pages:
        rep.err(f"страниц {len(reader.pages)}, ожидалось {pages}")

    # --- info dictionary
    info = reader.trailer.get("/Info")
    info = info.get_object() if info is not None else {}
    trapped = info.get("/Trapped")
    if not isinstance(trapped, NameObject) or trapped != "/False":
        rep.err(f"/Trapped должен быть именем /False, а не {trapped!r}")
    if not info.get("/Title"):
        rep.err("нет /Title")
    for k in ("/CreationDate", "/ModDate"):
        if not DATE_RE.match(str(info.get(k, ""))):
            rep.err(f"{k} в неверном формате: {info.get(k)!r}")
    gts = info.get("/GTS_PDFXVersion")
    if pdfx and gts != "PDF/X-1a:2003":
        rep.err(f"GTS_PDFXVersion = {gts!r}, ожидалось PDF/X-1a:2003")

    # --- output intent
    intents = reader.trailer["/Root"].get("/OutputIntents")
    if pdfx:
        intents = intents.get_object() if intents is not None else []
        if len(intents) != 1:
            rep.err(f"OutputIntents: {len(intents)} шт., нужен ровно один")
        for oi in intents:
            oi = oi.get_object()
            if oi.get("/S") != "/GTS_PDFX":
                rep.err("OutputIntent /S не /GTS_PDFX")
            if oi.get("/OutputConditionIdentifier") != FOGRA39["identifier"]:
                rep.err(f"OutputConditionIdentifier = {oi.get('/OutputConditionIdentifier')!r}")
            if oi.get("/RegistryName") != FOGRA39["registry"]:
                rep.err(f"RegistryName = {oi.get('/RegistryName')!r}")
            prof = oi.get("/DestOutputProfile")
            if prof is None:
                rep.note("ICC-профиль не встроен (указано только условие печати FOGRA39)")
            else:
                prof = prof.get_object()
                data = prof.get_data()
                if prof.get("/N") != 4:
                    rep.err("DestOutputProfile: /N должно быть 4")
                if data[36:40] != b"acsp" or data[12:16] != b"prtr" or data[16:20] != b"CMYK":
                    rep.err("DestOutputProfile: это не CMYK-профиль печати")
                if data[8] != 2:
                    rep.err(f"DestOutputProfile: версия ICC {data[8]}, для PDF/X-1a нужна 2")
                if sha256(data) == ICC_SHA256:
                    rep.note("встроен профиль Coated FOGRA39 (ISO 12647-2:2004), Adobe")
                else:
                    rep.note(f"встроен пользовательский профиль ({len(data)} байт)")
    elif intents is not None:
        rep.note("офисный файл содержит OutputIntent")

    # --- pages: boxes, operators, colours, geometry
    fh = H / 2
    if preview:
        allowed = {_key(c) for c in rgb_palette.values()}
        white = _key(rgb_palette.get("white", (1, 1, 1)))
        fold = None
        mw, mh = W * PT_PER_MM, fh * PT_PER_MM
        safe_zones = [(margin, margin, W - margin, fh - fold_margin)]  # one face per page
    else:
        allowed = {_key([v / 100 for v in c]) for c in palette.values()}
        white = _key([v / 100 for v in palette.get("white", (0, 0, 0, 0))])
        fold = _key([v / 100 for v in palette.get("fold_mark", (0, 0, 0, 40))])
        mw, mh = (W + 2 * b) * PT_PER_MM, (H + 2 * b) * PT_PER_MM
        safe_zones = [
            (margin, margin, W - margin, fh - fold_margin),  # bottom face
            (margin, fh + fold_margin, W - margin, H - margin),  # top face (rotated)
        ]
    trim_pt = (b * PT_PER_MM, b * PT_PER_MM, (b + W) * PT_PER_MM, (b + H) * PT_PER_MM)
    for i, page in enumerate(reader.pages, 1):
        mb = [float(x) for x in page.mediabox]
        if not (_close(mb[0], 0) and _close(mb[1], 0) and _close(mb[2], mw) and _close(mb[3], mh)):
            rep.err(f"стр. {i}: MediaBox {mb}")
        if pdfx:
            tb = [float(x) for x in page.trimbox]
            bb = [float(x) for x in page.bleedbox]
            if any(not _close(x, y) for x, y in zip(tb, trim_pt)):
                rep.err(f"стр. {i}: TrimBox {tb}, ожидалось {trim_pt}")
            if any(not _close(x, y) for x, y in zip(bb, mb)):
                rep.err(f"стр. {i}: BleedBox {bb} ≠ MediaBox")
        res = page.get("/Resources")
        if res is not None and len(res.get_object()) > 0:
            rep.err(f"стр. {i}: непустые /Resources {list(res.get_object().keys())}")
        contents = page.get_contents()
        if contents is None:
            rep.err(f"стр. {i}: пустая страница")
            continue
        boxes, problems = _ink_boxes(ContentStream(contents, reader).operations, rgb=preview)
        for p in sorted(set(problems)):
            rep.err(f"стр. {i}: {p}")
        for col, box, _ in boxes:
            if col not in allowed:
                rep.err(f"стр. {i}: цвет {col} не из палитры")
                continue
            if col == white:
                continue
            mm = (box[0] / PT_PER_MM - b, box[1] / PT_PER_MM - b, box[2] / PT_PER_MM - b, box[3] / PT_PER_MM - b)
            if col == fold:
                if mm[0] < -1e-3 or mm[1] < -1e-3 or mm[2] > W + 1e-3 or mm[3] > H + 1e-3:
                    rep.err(f"стр. {i}: метка сгиба за краем листа {mm}")
                continue
            if not any(
                mm[0] >= z[0] - 1e-3 and mm[1] >= z[1] - 1e-3 and mm[2] <= z[2] + 1e-3 and mm[3] <= z[3] + 1e-3
                for z in safe_zones
            ):
                rep.err(f"стр. {i}: краска вне безопасной зоны: {tuple(round(v, 2) for v in mm)}")
                break

    if render and not preview and not rep.errors:
        _render_symmetry(path, rep, b, W, H)
    return rep


def _render_symmetry(path: FsPath, rep: Report, bleed: float, W: float, H: float) -> None:
    """Rasterise page 1 and compare the trim area with itself rotated by 180°."""
    if not shutil.which("pdftoppm"):
        rep.note("pdftoppm не найден — проверка поворота по растру пропущена")
        return
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return
    dpi = 127  # = 5 px/mm
    with tempfile.TemporaryDirectory() as tmp:
        out = FsPath(tmp) / "p"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-f", "1", "-l", "1", "-png", str(path), str(out)],
            check=True,
            capture_output=True,
        )
        png = next(FsPath(tmp).glob("p*.png"))
        im = Image.open(png).convert("L")
        px = dpi / 25.4
        x0, y0 = round(bleed * px), round(bleed * px)
        im = im.crop((x0, y0, x0 + round(W * px), y0 + round(H * px)))
        diff = ImageChops.difference(im, im.rotate(180))
        hist = diff.point(lambda v: 255 if v > 96 else 0).histogram()
        frac = hist[255] / (im.width * im.height)
        if frac > 0.002:
            rep.err(f"лист не симметричен при повороте на 180° ({frac:.2%} пикселей отличаются)")
        else:
            rep.note(f"поворот верхней половины на 180° подтверждён рендером (различий {frac:.3%})")
