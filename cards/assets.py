"""ICC profile for the OutputIntent: Adobe "Coated FOGRA39 (ISO 12647-2:2004)".

The profile is downloaded from Adobe at build time and cached in .cache/
(not committed): Adobe's end-user licence allows redistributing the profile
"as embedded within digital image files" (our PDF) or standalone, but not
bundled with application software (this generator).
"""

from __future__ import annotations

import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path as FsPath

ADOBE_ZIP_URL = "https://download.adobe.com/pub/adobe/iccprofiles/win/AdobeICCProfilesCS4Win_end-user.zip"
ADOBE_ZIP_SHA256 = "92043b7c3ca5a25a0f6975d0cdc57db60a67dcb9ebdfb15b16267311beb58f94"
ICC_MEMBER = "Adobe ICC Profiles (end-user)/CMYK/CoatedFOGRA39.icc"
ICC_SHA256 = "da2b9b593e27cba2563cbc8596071c5c8f2395d3dbb4434538bac2bc9d58ce77"
ICC_NAME = "CoatedFOGRA39.icc"

REPO_ROOT = FsPath(__file__).resolve().parent.parent
DEFAULT_CACHE = REPO_ROOT / ".cache" / "icc"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_icc(data: bytes, source: str) -> bytes:
    """Minimal sanity check: an ICC v2/v4 *output* (prtr) profile for CMYK."""
    if len(data) < 132 or data[36:40] != b"acsp":
        raise ValueError(f"{source}: это не ICC-профиль")
    if data[12:16] != b"prtr" or data[16:20] != b"CMYK":
        raise ValueError(
            f"{source}: нужен CMYK-профиль печати (класс prtr, пространство CMYK), "
            f"а это {data[12:16].decode('latin1')}/{data[16:20].decode('latin1')}"
        )
    return data


def load_icc(path: FsPath) -> bytes:
    return check_icc(path.read_bytes(), str(path))


def ensure_icc(cache_dir: FsPath = DEFAULT_CACHE) -> bytes | None:
    """Return the Coated FOGRA39 profile, downloading it once if needed.

    Returns None (with a warning) if it cannot be obtained: the PDF is then
    written with an OutputIntent that only names the registered FOGRA39
    condition, which PDF/X-1a allows.
    """
    target = cache_dir / ICC_NAME
    if target.exists():
        data = target.read_bytes()
        if sha256(data) == ICC_SHA256:
            return data
    try:
        print("Скачиваю ICC-профиль Coated FOGRA39 с сайта Adobe…", file=sys.stderr)
        with urllib.request.urlopen(ADOBE_ZIP_URL, timeout=120) as resp:
            blob = resp.read()
        if sha256(blob) != ADOBE_ZIP_SHA256:
            raise ValueError("контрольная сумма архива Adobe не совпала")
        data = zipfile.ZipFile(io.BytesIO(blob)).read(ICC_MEMBER)
        if sha256(data) != ICC_SHA256:
            raise ValueError("контрольная сумма профиля не совпала")
        check_icc(data, ICC_MEMBER)
    except Exception as exc:  # network errors, changed files, …
        print(
            f"ВНИМАНИЕ: не удалось получить профиль FOGRA39 ({exc}). PDF будет собран без "
            "встроенного профиля — только с указанием условия печати FOGRA39. Можно указать "
            "свой профиль: --icc путь/к/профилю.icc",
            file=sys.stderr,
        )
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return data
