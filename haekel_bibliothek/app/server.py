"""Häkel-Bibliothek – kleiner Webserver für das Home-Assistant-Add-on.

Liest PDFs aus /share/<ordner>, erzeugt Vorschaubilder (Seite 1 oder ein
gleichnamiges Bild) und liefert Katalog, Dateien und Metadaten über Ingress aus.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import time
from pathlib import Path

from aiohttp import web

mimetypes.add_type("text/javascript", ".mjs")

OPTIONS_FILE = Path("/data/options.json")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PDFJS_DIR = Path(os.environ.get("PDFJS_DIR", "/opt/pdfjs"))
STATIC_DIR = Path(__file__).parent / "static"
PORT = int(os.environ.get("PORT", "8099"))

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic")
TRASH = ".papierkorb"
STATUSES = {"", "in_arbeit", "fertig"}


def library_root() -> Path:
    if "LIBRARY_DIR" in os.environ:
        return Path(os.environ["LIBRARY_DIR"])
    folder = "haekeln"
    try:
        folder = json.loads(OPTIONS_FILE.read_text()).get("ordner", folder) or folder
    except (OSError, ValueError):
        pass
    folder = folder.strip("/").replace("..", "")
    return Path("/share") / folder


ROOT = library_root()
THUMBS = DATA_DIR / "thumbs"
META_FILE = DATA_DIR / "meta.json"
meta_lock = asyncio.Lock()


# ---------- Hilfsfunktionen ----------

def encode_id(rel: str) -> str:
    return base64.urlsafe_b64encode(rel.encode()).decode().rstrip("=")


def decode_id(item_id: str) -> Path:
    try:
        rel = base64.urlsafe_b64decode(item_id + "=" * (-len(item_id) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPNotFound()
    path = (ROOT / rel).resolve()
    if ROOT.resolve() not in path.parents or not path.is_file():
        raise web.HTTPNotFound()
    return path


def hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(ROOT).parts)


def nice_title(stem: str) -> str:
    title = re.sub(r"[_]+|(?<=\S)-(?=\S)", " ", stem)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:1].upper() + title[1:] if title else stem


def cover_image(pdf: Path) -> Path | None:
    stem = pdf.stem.lower()
    for sibling in pdf.parent.iterdir():
        if sibling.suffix.lower() in IMAGE_EXT and sibling.stem.lower() == stem:
            return sibling
    return None


def safe_name(name: str) -> str:
    name = os.path.basename(name or "").strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    return name.lstrip(".") or "anleitung"


def safe_category(cat: str) -> str:
    cat = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (cat or "").strip()).lstrip(".")
    return cat[:60]


def unique_path(folder: Path, name: str) -> Path:
    target = folder / name
    stem, ext = target.stem, target.suffix
    n = 2
    while target.exists():
        target = folder / f"{stem} ({n}){ext}"
        n += 1
    return target


def load_meta() -> dict:
    try:
        return json.loads(META_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_meta(meta: dict) -> None:
    tmp = META_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    tmp.replace(META_FILE)


def list_items() -> list[dict]:
    meta = load_meta()
    items = []
    for pdf in ROOT.rglob("*"):
        if pdf.suffix.lower() != ".pdf" or not pdf.is_file() or hidden(pdf):
            continue
        rel = pdf.relative_to(ROOT).as_posix()
        stat = pdf.stat()
        cover = cover_image(pdf)
        version = int(max(stat.st_mtime, cover.stat().st_mtime if cover else 0))
        m = meta.get(rel, {})
        parent = pdf.parent.relative_to(ROOT).as_posix()
        items.append({
            "id": encode_id(rel),
            "title": m.get("title") or nice_title(pdf.stem),
            "category": "" if parent == "." else parent,
            "added": int(m.get("added") or stat.st_mtime),
            "size": stat.st_size,
            "v": version,
            "favorite": bool(m.get("favorite")),
            "status": m.get("status", "") if m.get("status", "") in STATUSES else "",
            "notes": m.get("notes", ""),
            "hasCover": cover is not None,
        })
    items.sort(key=lambda i: i["added"], reverse=True)
    return items


async def make_thumb(pdf: Path) -> Path:
    cover = cover_image(pdf)
    source = cover or pdf
    stat = source.stat()
    key = hashlib.sha1(f"{source}|{stat.st_mtime}|{stat.st_size}".encode()).hexdigest()
    thumb = THUMBS / f"{key}.jpg"
    if thumb.exists():
        return thumb
    if cover and cover.suffix.lower() != ".heic":
        # Bild direkt verwenden (Browser skaliert); HEIC kann nicht jeder Browser
        return cover
    if cover:
        source = pdf
    out = THUMBS / key
    proc = await asyncio.create_subprocess_exec(
        "pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-scale-to", "700",
        "-jpeg", "-jpegopt", "quality=82", str(source), str(out),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not thumb.exists():
        print(f"Vorschau fehlgeschlagen für {pdf.name}: {err.decode(errors='ignore')[:200]}")
        raise web.HTTPNotFound()
    return thumb


# ---------- Routen ----------

routes = web.RouteTableDef()


@routes.get("/")
async def index(_):
    resp = web.FileResponse(STATIC_DIR / "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@routes.get("/api/items")
async def api_items(_):
    items = await asyncio.to_thread(list_items)
    categories = sorted({i["category"] for i in items if i["category"]}, key=str.lower)
    for folder in ROOT.iterdir():
        if folder.is_dir() and not folder.name.startswith(".") and folder.name not in categories:
            categories.append(folder.name)
    return web.json_response({"items": items, "categories": sorted(categories, key=str.lower)})


@routes.get("/thumb/{id}")
async def thumb(request):
    pdf = decode_id(request.match_info["id"])
    path = await make_thumb(pdf)
    resp = web.FileResponse(path)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@routes.get("/file/{id}")
async def file(request):
    pdf = decode_id(request.match_info["id"])
    resp = web.FileResponse(pdf)
    resp.content_type = "application/pdf"
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@routes.post("/api/meta/{id}")
async def api_meta(request):
    pdf = decode_id(request.match_info["id"])
    rel = pdf.relative_to(ROOT).as_posix()
    body = await request.json()
    async with meta_lock:
        meta = load_meta()
        m = meta.setdefault(rel, {})
        if "title" in body:
            m["title"] = str(body["title"]).strip()[:200]
        if "notes" in body:
            m["notes"] = str(body["notes"])[:5000]
        if "favorite" in body:
            m["favorite"] = bool(body["favorite"])
        if "status" in body and body["status"] in STATUSES:
            m["status"] = body["status"]
        save_meta(meta)
    return web.json_response({"ok": True})


# Home Assistant (Ingress) lässt pro Anfrage höchstens 16 MB durch.
# Dateien werden daher im Browser in Stücke geteilt (api/chunk) und
# anschließend mit api/upload bzw. api/cover übernommen.
UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def part_path(upload_id: str) -> Path:
    if not UPLOAD_ID.match(upload_id or ""):
        raise web.HTTPBadRequest(text="Ungültige Upload-ID")
    return DATA_DIR / f"upload-{upload_id}.part"


@routes.post("/api/chunk")
async def api_chunk(request):
    path = part_path(request.query.get("id", ""))
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        raise web.HTTPBadRequest(text="Ungültiger Offset")
    size = path.stat().st_size if path.exists() else 0
    if offset != size:
        # Stück fehlt oder wurde doppelt gesendet: Client setzt bei 'size' fort
        return web.json_response({"ok": False, "size": size}, status=409)
    with path.open("ab") as fh:
        async for chunk in request.content.iter_chunked(1 << 16):
            fh.write(chunk)
    return web.json_response({"ok": True, "size": path.stat().st_size})


def take_part(info) -> tuple[Path, str]:
    if not isinstance(info, dict):
        raise web.HTTPBadRequest(text="Datei fehlt")
    path = part_path(str(info.get("id", "")))
    if not path.exists():
        raise web.HTTPBadRequest(text="Upload unvollständig, bitte erneut versuchen")
    if "size" in info and path.stat().st_size != int(info["size"]):
        path.unlink(missing_ok=True)
        raise web.HTTPBadRequest(text="Upload unvollständig, bitte erneut versuchen")
    return path, safe_name(str(info.get("name", "")))


@routes.post("/api/upload")
async def api_upload(request):
    body = await request.json()
    pdf_tmp, name = take_part(body.get("pdf"))
    image = body.get("image")
    image_tmp, image_name = take_part(image) if image else (None, "")
    size_mb = pdf_tmp.stat().st_size / 1048576
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    with pdf_tmp.open("rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        pdf_tmp.unlink(missing_ok=True)
        if image_tmp:
            image_tmp.unlink(missing_ok=True)
        print(f"Upload abgelehnt (keine PDF): {name}")
        raise web.HTTPBadRequest(text="Die Datei ist keine PDF")
    category = safe_category(str(body.get("category", "")))
    folder = ROOT / category if category else ROOT
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder, name)
    shutil.move(pdf_tmp, target)
    if image_tmp:
        ext = Path(image_name).suffix.lower()
        if ext in IMAGE_EXT:
            shutil.move(image_tmp, target.with_suffix(ext))
        else:
            image_tmp.unlink(missing_ok=True)
    rel = target.relative_to(ROOT).as_posix()
    async with meta_lock:
        meta = load_meta()
        meta.setdefault(rel, {})["added"] = int(time.time())
        save_meta(meta)
    print(f"Upload gespeichert: {rel} ({size_mb:.1f} MB)")
    return web.json_response({"ok": True, "id": encode_id(rel)})


@routes.post("/api/cover/{id}")
async def api_cover(request):
    pdf = decode_id(request.match_info["id"])
    tmp, name = take_part((await request.json()).get("image"))
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_EXT:
        tmp.unlink(missing_ok=True)
        raise web.HTTPBadRequest(text="Bitte JPG, PNG, WebP oder HEIC verwenden")
    old = cover_image(pdf)
    if old:
        old.unlink()
    shutil.move(tmp, pdf.with_suffix(ext))
    return web.json_response({"ok": True})


def cleanup_parts():
    cutoff = time.time() - 24 * 3600
    for part in DATA_DIR.glob("upload-*"):
        if part.stat().st_mtime < cutoff:
            part.unlink(missing_ok=True)


_theme_seen: set[str] = set()


@routes.post("/api/theme-info")
async def api_theme_info(request):
    """Protokolliert einmalig, welchen Hintergrund das HA-Theme liefert (zur Fehlersuche)."""
    text = (await request.text())[:2000]
    if text not in _theme_seen and len(_theme_seen) < 20:
        _theme_seen.add(text)
        print(f"Theme-Hintergrund: {text}")
    return web.json_response({"ok": True})


@routes.delete("/api/items/{id}")
async def api_delete(request):
    pdf = decode_id(request.match_info["id"])
    trash = ROOT / TRASH
    trash.mkdir(exist_ok=True)
    cover = cover_image(pdf)
    shutil.move(pdf, unique_path(trash, pdf.name))
    if cover:
        shutil.move(cover, unique_path(trash, cover.name))
    return web.json_response({"ok": True})


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    THUMBS.mkdir(parents=True, exist_ok=True)
    cleanup_parts()
    app = web.Application(client_max_size=200 * 1024 * 1024)
    app.add_routes(routes)
    app.router.add_static("/static/", STATIC_DIR)
    app.router.add_static("/pdfjs/", PDFJS_DIR)
    print(f"Häkel-Bibliothek: Ordner {ROOT}, Port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
