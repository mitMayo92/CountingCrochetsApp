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

import importer

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
WOOL = Path(os.environ.get("WOOL_DIR", ROOT.parent / "wolle"))
THUMBS = DATA_DIR / "thumbs"
META_FILE = DATA_DIR / "meta.json"
WOOL_META = DATA_DIR / "wolle.json"
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


def guess_label(stem: str) -> str:
    s = stem.lower()
    if re.search(r"(^|[^a-z])(en|eng|english|englisch|uk|us)([^a-z]|$)|pattern", s):
        return "Englisch"
    if re.search(r"(^|[^a-z])(de|ger|deutsch|german)([^a-z]|$)|anleitung", s):
        return "Deutsch"
    return ""


def fill_labels(files: list[dict]) -> list[dict]:
    """Fehlende Bezeichnungen sinnvoll ergänzen (Deutsch/Englisch, sonst durchnummeriert)."""
    used = {f["label"] for f in files if f["label"]}
    for i, f in enumerate(files):
        if not f["label"]:
            f["label"] = next((l for l in ("Deutsch", "Englisch") if l not in used), f"PDF {i + 1}")
            used.add(f["label"])
    return files


def rel_of(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def file_entry(rel: str, label: str) -> dict | None:
    path = ROOT / rel
    if not path.is_file():
        return None
    return {"id": encode_id(rel), "label": label or guess_label(path.stem), "size": path.stat().st_size}


def variant_rels(meta: dict) -> set[str]:
    """Alle PDFs, die als weitere Fassung an einem anderen (existierenden) Eintrag hängen."""
    out = set()
    for main, m in meta.items():
        if (ROOT / main).is_file():
            out.update(f["rel"] for f in m.get("files", []))
    return out


def main_rel_for(meta: dict, rel: str) -> str:
    """Liefert den Haupteintrag; Fassungen werden nie selbst zum Eintrag."""
    if rel in variant_rels(meta):
        raise web.HTTPBadRequest(text="Diese PDF ist bereits eine Fassung eines anderen Eintrags")
    return rel


def list_items() -> list[dict]:
    meta = load_meta()
    variants = variant_rels(meta)
    items = []
    for pdf in ROOT.rglob("*"):
        if pdf.suffix.lower() != ".pdf" or not pdf.is_file() or hidden(pdf):
            continue
        rel = pdf.relative_to(ROOT).as_posix()
        if rel in variants:
            continue
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
            "source": m.get("source", ""),
            "hasCover": cover is not None,
            "files": fill_labels([f for f in [file_entry(rel, m.get("label", ""))]
                      + [file_entry(v["rel"], v.get("label", "")) for v in m.get("files", [])] if f]),
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


@routes.post("/api/import")
async def api_import(request):
    """Link hinzufügen: PDF-Link herunterladen oder Webseite im Lesemodus als PDF speichern."""
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if url and "://" not in url:
        url = "https://" + url
    category = safe_category(str(body.get("category", "")))
    own_image = take_part(body["image"]) if body.get("image") else None
    if own_image and Path(own_image[1]).suffix.lower() not in IMAGE_EXT:
        own_image[0].unlink(missing_ok=True)
        raise web.HTTPBadRequest(text="Bitte JPG, PNG, WebP oder HEIC als Titelbild verwenden")
    print(f"Link-Import gestartet: {url}")
    try:
        data, ctype, final = await importer.fetch(url)
        folder = ROOT / category if category else ROOT
        folder.mkdir(parents=True, exist_ok=True)
        title, cover_url = "", ""
        if "application/pdf" in ctype or data[:1024].find(b"%PDF-") != -1:
            name = safe_name(Path(final.split("?")[0]).name or "Anleitung")
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            target = unique_path(folder, name)
            target.write_bytes(data)
        else:
            article = await asyncio.to_thread(importer.page_to_article, data, final)
            title, cover_url = article["title"], article["image"]
            target = unique_path(folder, safe_name(re.sub(r"\s+", " ", title))[:90] + ".pdf")
            tmp = DATA_DIR / f"import-{time.time_ns()}.pdf"
            try:
                await asyncio.to_thread(importer.article_to_pdf, article, final, tmp)
                shutil.move(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
            if cover_url and not own_image:
                img = await asyncio.to_thread(importer.download_image, cover_url)
                if img:
                    target.with_suffix(img[1]).write_bytes(img[0])
        if own_image:
            for old in [p for p in target.parent.iterdir() if p.stem == target.stem and p.suffix.lower() in IMAGE_EXT]:
                old.unlink()
            shutil.move(own_image[0], target.with_suffix(Path(own_image[1]).suffix.lower()))
    except importer.ImportError_ as e:
        if own_image:
            own_image[0].unlink(missing_ok=True)
        print(f"Link-Import abgelehnt: {e}")
        raise web.HTTPBadRequest(text=str(e))
    except Exception as e:
        if own_image:
            own_image[0].unlink(missing_ok=True)
        print(f"Link-Import fehlgeschlagen: {type(e).__name__}: {e}")
        raise web.HTTPBadRequest(text="Die Seite konnte nicht umgewandelt werden")
    rel = rel_of(target)
    async with meta_lock:
        meta = load_meta()
        m = meta.setdefault(rel, {})
        m["added"] = int(time.time())
        m["source"] = url
        if title:
            m["title"] = title
        save_meta(meta)
    print(f"Link-Import gespeichert: {rel} ({target.stat().st_size / 1048576:.1f} MB)")
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


# ---------- Mehrere PDFs pro Eintrag (z. B. Deutsch/Englisch) ----------

def clean_label(label) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(label or "")).strip()[:40]


@routes.post("/api/files/{id}/add")
async def api_files_add(request):
    """Neue PDF als weitere Fassung hochladen (Datei vorher per api/chunk gesendet)."""
    main = decode_id(request.match_info["id"])
    body = await request.json()
    tmp, name = take_part(body.get("pdf"))
    with tmp.open("rb") as fh:
        if b"%PDF-" not in fh.read(1024):
            tmp.unlink(missing_ok=True)
            raise web.HTTPBadRequest(text="Die Datei ist keine PDF")
    label = clean_label(body.get("label")) or guess_label(Path(name).stem) or "Weitere Fassung"
    target = unique_path(main.parent, f"{main.stem} ({label}).pdf")
    shutil.move(tmp, target)
    async with meta_lock:
        meta = load_meta()
        rel = main_rel_for(meta, rel_of(main))
        m = meta.setdefault(rel, {})
        m.setdefault("files", []).append({"rel": rel_of(target), "label": label})
        save_meta(meta)
    print(f"Fassung hinzugefügt: {rel_of(target)} → {rel}")
    return web.json_response({"ok": True})


@routes.post("/api/files/{id}/merge")
async def api_files_merge(request):
    """Einen bestehenden Eintrag (samt seinen Fassungen) als Fassung(en) übernehmen."""
    main = decode_id(request.match_info["id"])
    other = decode_id(str((await request.json()).get("from", "")))
    if main == other:
        raise web.HTTPBadRequest(text="Ein Eintrag kann nicht mit sich selbst zusammengeführt werden")
    async with meta_lock:
        meta = load_meta()
        rel = main_rel_for(meta, rel_of(main))
        orel = main_rel_for(meta, rel_of(other))
        m = meta.setdefault(rel, {})
        om = meta.pop(orel, {})
        files = m.setdefault("files", [])
        files.append({"rel": orel, "label": om.get("label") or guess_label(other.stem)})
        files.extend(om.get("files", []))
        if om.get("notes"):
            m["notes"] = (m.get("notes", "") + "\n\n" + om["notes"]).strip()
        if om.get("favorite"):
            m["favorite"] = True
        if not m.get("status") and om.get("status") in STATUSES:
            m["status"] = om["status"]
        save_meta(meta)
    print(f"Zusammengeführt: {orel} → {rel}")
    return web.json_response({"ok": True})


@routes.post("/api/files/{id}/label")
async def api_files_label(request):
    main = decode_id(request.match_info["id"])
    body = await request.json()
    target = rel_of(decode_id(str(body.get("file", ""))))
    label = clean_label(body.get("label"))
    if not label:
        raise web.HTTPBadRequest(text="Bitte eine Bezeichnung eingeben")
    async with meta_lock:
        meta = load_meta()
        rel = main_rel_for(meta, rel_of(main))
        m = meta.setdefault(rel, {})
        if target == rel:
            m["label"] = label
        else:
            for f in m.get("files", []):
                if f["rel"] == target:
                    f["label"] = label
                    break
            else:
                raise web.HTTPNotFound(text="Fassung nicht gefunden")
        save_meta(meta)
    return web.json_response({"ok": True})


@routes.post("/api/files/{id}/split")
async def api_files_split(request):
    """Fassung wieder abtrennen: sie wird ein eigener Eintrag."""
    main = decode_id(request.match_info["id"])
    target = rel_of(decode_id(str((await request.json()).get("file", ""))))
    async with meta_lock:
        meta = load_meta()
        m = meta.get(rel_of(main), {})
        files = m.get("files", [])
        keep = [f for f in files if f["rel"] != target]
        if len(keep) == len(files):
            raise web.HTTPNotFound(text="Fassung nicht gefunden")
        m["files"] = keep
        removed = next(f for f in files if f["rel"] == target)
        meta[target] = {"added": int(time.time()), "label": removed.get("label", "")}
        save_meta(meta)
    return web.json_response({"ok": True})


@routes.delete("/api/items/{id}")
async def api_delete(request):
    pdf = decode_id(request.match_info["id"])
    trash = ROOT / TRASH
    trash.mkdir(exist_ok=True)
    async with meta_lock:
        meta = load_meta()
        m = meta.pop(rel_of(pdf), {})
        save_meta(meta)
    paths = [pdf] + [ROOT / f["rel"] for f in m.get("files", [])]
    for path in paths:
        if not path.is_file():
            continue
        cover = cover_image(path)
        shutil.move(path, unique_path(trash, path.name))
        if cover:
            shutil.move(cover, unique_path(trash, cover.name))
    return web.json_response({"ok": True})


# ---------- Woll-Katalog (Screenshots) ----------

def wool_path(item_id: str) -> Path:
    try:
        rel = base64.urlsafe_b64decode(item_id + "=" * (-len(item_id) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPNotFound()
    path = (WOOL / rel).resolve()
    if WOOL.resolve() not in path.parents or not path.is_file():
        raise web.HTTPNotFound()
    return path


def wool_meta() -> dict:
    try:
        return json.loads(WOOL_META.read_text())
    except (OSError, ValueError):
        return {}


def save_wool_meta(meta: dict) -> None:
    tmp = WOOL_META.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    tmp.replace(WOOL_META)


def list_wool() -> list[dict]:
    meta = wool_meta()
    out = []
    for img in WOOL.rglob("*"):
        if img.suffix.lower() not in IMAGE_EXT or not img.is_file():
            continue
        rel = img.relative_to(WOOL).as_posix()
        if any(part.startswith(".") for part in img.relative_to(WOOL).parts):
            continue
        st = img.stat()
        m = meta.get(rel, {})
        parent = img.parent.relative_to(WOOL).as_posix()
        out.append({
            "id": encode_id(rel),
            "title": m.get("title") or nice_title(img.stem),
            "category": "" if parent == "." else parent,
            "added": int(m.get("added") or st.st_mtime),
            "v": int(st.st_mtime),
            "notes": m.get("notes", ""),
        })
    out.sort(key=lambda i: i["added"], reverse=True)
    return out


@routes.get("/api/wool")
async def api_wool(_):
    items = await asyncio.to_thread(list_wool)
    cats = {i["category"] for i in items if i["category"]}
    cats |= {d.name for d in WOOL.iterdir() if d.is_dir() and not d.name.startswith(".")}
    return web.json_response({"items": items, "categories": sorted(cats, key=str.lower)})


@routes.get("/wool/{id}")
async def wool_file(request):
    resp = web.FileResponse(wool_path(request.match_info["id"]))
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@routes.get("/woolthumb/{id}")
async def wool_thumb(request):
    """Verkleinerte Vorschau für die Übersicht (Screenshots sind oft mehrere MB groß)."""
    img = wool_path(request.match_info["id"])
    st = img.stat()
    key = hashlib.sha1(f"wool|{img}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()
    thumb = THUMBS / f"{key}.jpg"
    if not thumb.exists() and img.suffix.lower() != ".heic":
        def make():
            from PIL import Image
            with Image.open(img) as im:
                im = im.convert("RGB")
                im.thumbnail((700, 1400))
                im.save(thumb, "JPEG", quality=82)
        try:
            await asyncio.to_thread(make)
        except Exception as e:
            print(f"Woll-Vorschau fehlgeschlagen für {img.name}: {e}")
    resp = web.FileResponse(thumb if thumb.exists() else img)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@routes.post("/api/wool/upload")
async def api_wool_upload(request):
    body = await request.json()
    category = safe_category(str(body.get("category", "")))
    folder = WOOL / category if category else WOOL
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for info in body.get("images", [])[:50]:
        tmp, name = take_part(info)
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXT:
            tmp.unlink(missing_ok=True)
            continue
        target = unique_path(folder, name)
        shutil.move(tmp, target)
        saved.append(target.relative_to(WOOL).as_posix())
    if not saved:
        raise web.HTTPBadRequest(text="Keine Bilder erhalten (JPG, PNG, WebP oder HEIC)")
    title = str(body.get("title", "")).strip()[:150]
    async with meta_lock:
        meta = wool_meta()
        for i, rel in enumerate(saved):
            m = meta.setdefault(rel, {})
            m["added"] = int(time.time()) + i
            if title:
                m["title"] = title if len(saved) == 1 else f"{title} ({i + 1})"
        save_wool_meta(meta)
    print(f"Wolle hinzugefügt: {len(saved)} Bild(er) in {category or '(ohne Kategorie)'}")
    return web.json_response({"ok": True, "count": len(saved)})


@routes.post("/api/wool/meta/{id}")
async def api_wool_meta(request):
    rel = wool_path(request.match_info["id"]).relative_to(WOOL).as_posix()
    body = await request.json()
    async with meta_lock:
        meta = wool_meta()
        m = meta.setdefault(rel, {})
        if "title" in body:
            m["title"] = str(body["title"]).strip()[:150]
        if "notes" in body:
            m["notes"] = str(body["notes"])[:5000]
        save_wool_meta(meta)
    return web.json_response({"ok": True})


@routes.delete("/api/wool/{id}")
async def api_wool_delete(request):
    img = wool_path(request.match_info["id"])
    rel = img.relative_to(WOOL).as_posix()
    trash = WOOL / TRASH
    trash.mkdir(exist_ok=True)
    shutil.move(img, unique_path(trash, img.name))
    async with meta_lock:
        meta = wool_meta()
        meta.pop(rel, None)
        save_wool_meta(meta)
    return web.json_response({"ok": True})


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    WOOL.mkdir(parents=True, exist_ok=True)
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
