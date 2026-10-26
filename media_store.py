"""Server-side image cache for room photos, listing photos, and cutouts.

Browser session storage is limited to roughly 5 MB, which forced earlier
versions of Theia to shrink every listing photo to 800 px. Full-resolution
images now live on the server under unguessable random names and only a short
``/media/<id>`` URL is stored in the browser session. Files older than
``THEIA_MEDIA_TTL_HOURS`` (default 7 days) are purged so private room photos are
not retained indefinitely.
"""
from __future__ import annotations

import base64
import io
import os
import re
import time
from pathlib import Path
from uuid import uuid4

import requests
from flask import abort, send_from_directory
from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent
MEDIA_DIR = Path(os.getenv("THEIA_MEDIA_DIR", PROJECT_DIR / "data" / "media"))
TTL_HOURS = float(os.getenv("THEIA_MEDIA_TTL_HOURS", "168"))  # 7 days, so saved rooms/items keep working
_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|bin)$")


def save_image(image: Image.Image, fmt: str = "PNG", quality: int = 92) -> str:
    """Store a PIL image and return its local URL."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = "png" if fmt.upper() == "PNG" else "jpg"
    name = f"{uuid4().hex}.{ext}"
    if ext == "jpg":
        image.convert("RGB").save(MEDIA_DIR / name, format="JPEG", quality=quality, optimize=True)
    else:
        image.save(MEDIA_DIR / name, format="PNG", optimize=False, compress_level=6)
    return f"/media/{name}"


def save_bytes(data: bytes, ext: str = "bin") -> str:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}.{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    return f"/media/{name}"


def local_path(url: str) -> Path | None:
    if not isinstance(url, str) or not url.startswith("/media/"):
        return None
    name = url.rsplit("/", 1)[-1]
    if not _NAME.match(name):
        return None
    path = MEDIA_DIR / name
    return path if path.exists() else None


def read_bytes(source: str, referer: str | None = None, timeout: int = 12) -> bytes:
    """Return raw bytes for a data URL, a /media URL, or a public http(s) URL."""
    if source.startswith("data:"):
        return base64.b64decode(source.split(",", 1)[1])
    path = local_path(source)
    if path is not None:
        return path.read_bytes()
    if source.startswith("/media/"):
        raise FileNotFoundError("cached image expired; add the item again")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Theia/0.2)"}
    if referer:
        headers["Referer"] = referer
    response = requests.get(source, timeout=timeout, headers=headers)
    response.raise_for_status()
    if not response.headers.get("content-type", "image/").lower().startswith("image/"):
        raise ValueError("listing image URL returned a web page instead of an image")
    return response.content


def open_image(source: str, referer: str | None = None) -> Image.Image:
    from PIL import ImageOps
    return ImageOps.exif_transpose(Image.open(io.BytesIO(read_bytes(source, referer))))


def purge_old() -> int:
    if not MEDIA_DIR.exists():
        return 0
    cutoff = time.time() - TTL_HOURS * 3600
    removed = 0
    for path in MEDIA_DIR.iterdir():
        if _NAME.match(path.name) and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def register(server) -> None:
    """Expose cached media at /media/<name> on the Dash Flask server."""
    purge_old()

    @server.route("/media/<name>")
    def _media(name: str):  # pragma: no cover - exercised through the browser
        if not _NAME.match(name):
            abort(404)
        response = send_from_directory(MEDIA_DIR, name, max_age=3600)
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response
