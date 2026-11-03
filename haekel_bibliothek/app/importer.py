"""Link-Import: Webseite im Lesemodus als PDF speichern oder PDF-Link herunterladen."""

import asyncio
import datetime
import html
import ipaddress
import os
import re
import socket
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import aiohttp
from lxml import html as lhtml
from readability import Document

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
MAX_BYTES = 150 * 1024 * 1024
LAZY_ATTRS = ("data-lazy-src", "data-src", "data-original", "data-orig-file", "data-large-file")


class ImportError_(Exception):
    """Fehler mit verständlicher Meldung für die Oberfläche."""


def check_public(url: str) -> None:
    """Nur öffentliche http(s)-Adressen zulassen (kein Zugriff aufs Heimnetz/HA intern)."""
    parsed = urlparse(url)
    if os.environ.get("IMPORT_ALLOW_PRIVATE") and parsed.scheme in ("http", "https"):
        return  # nur für lokale Tests
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ImportError_("Bitte einen Link beginnend mit http:// oder https:// eingeben")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ImportError_(f"Seite nicht gefunden: {parsed.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ImportError_("Links ins Heimnetz werden nicht importiert")


async def fetch(url: str) -> tuple[bytes, str, str]:
    """Lädt die Adresse; liefert Inhalt, Content-Type und endgültige URL."""
    await asyncio.to_thread(check_public, url)
    timeout = aiohttp.ClientTimeout(total=90)
    headers = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as s:
            async with s.get(url, allow_redirects=True) as r:
                final = str(r.url)
                await asyncio.to_thread(check_public, final)
                if r.status in (401, 403):
                    raise ImportError_("Die Seite verlangt eine Anmeldung oder blockiert den Abruf")
                if r.status >= 400:
                    raise ImportError_(f"Die Seite antwortet mit Fehler {r.status}")
                data = bytearray()
                async for chunk in r.content.iter_chunked(1 << 16):
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise ImportError_("Die Datei ist zu groß")
                return bytes(data), r.headers.get("Content-Type", ""), final
    except aiohttp.ClientError as e:
        raise ImportError_(f"Seite konnte nicht geladen werden ({type(e).__name__})")
    except asyncio.TimeoutError:
        raise ImportError_("Die Seite hat zu lange nicht geantwortet")


def _meta(tree, *names) -> str:
    for n in names:
        for attr in ("property", "name"):
            v = tree.xpath(f'//meta[@{attr}="{n}"]/@content')
            if v and v[0].strip():
                return v[0].strip()
    return ""


IMG_LINK = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)


def _hires(u: str) -> str:
    """Blogger liefert verkleinerte Bilder (/s320/, =w400-h300); volle Größe anfordern."""
    if "googleusercontent.com" in u or "bp.blogspot.com" in u:
        u = re.sub(r"/(?:s\d+|w\d+(?:-h\d+)?)(?:-[a-z0-9-]+)?/", "/s1600/", u, count=1)
        u = re.sub(r"=(?:s\d+|w\d+(?:-h\d+)?)(?:-[a-z0-9-]+)?$", "=s1600", u)
    return u


def _fix_images(tree, base: str) -> None:
    """Nachladende Bilder (WordPress & Co.) auf echte Adressen umstellen, Links absolut machen."""
    for img in tree.xpath("//img"):
        src = next((img.get(a) for a in LAZY_ATTRS if img.get(a)), None)
        srcset = img.get("data-lazy-srcset") or img.get("data-srcset") or img.get("srcset")
        if not src and srcset:
            src = srcset.split(",")[-1].strip().split(" ")[0]  # größte Variante
        if not src:
            src = img.get("src")
        if not src or src.startswith("data:"):
            img.drop_tree()  # nur Platzhalter, kein echtes Bild
            continue
        # Bild verlinkt auf größere Fassung (Blogger, viele WordPress-Themes)?
        parent = img.getparent()
        href = parent.get("href") if parent is not None and parent.tag == "a" else None
        if href and (IMG_LINK.search(href) or "googleusercontent.com" in href or "bp.blogspot.com" in href):
            src = href
        img.set("src", _hires(urljoin(base, src)))
        for a in ("srcset", "sizes", "loading", "width", "height", "style", *LAZY_ATTRS,
                  "data-lazy-srcset", "data-srcset"):
            if a in img.attrib:
                del img.attrib[a]
    for noscript in tree.xpath("//noscript"):
        noscript.drop_tree()
    for a in tree.xpath("//a[@href]"):
        a.set("href", urljoin(base, a.get("href")))


def page_to_article(raw: bytes, url: str) -> dict:
    """Extrahiert Titel, Titelbild und Hauptinhalt (Lesemodus)."""
    tree = lhtml.fromstring(raw)
    _fix_images(tree, url)
    cleaned = lhtml.tostring(tree, encoding="unicode")
    doc = Document(cleaned, url=url)
    content = doc.summary(html_partial=True)
    body = lhtml.fromstring(content)
    if len(body.text_content().strip()) < 200:
        raise ImportError_("Auf der Seite wurde keine Anleitung gefunden (zu wenig Text)")
    title = _meta(tree, "og:title", "twitter:title") or doc.short_title() or urlparse(url).hostname
    image = _meta(tree, "og:image", "og:image:url", "twitter:image")
    first_img = body.xpath("//img/@src")
    print(f"Lesemodus: {len(body.text_content().strip())} Zeichen, {len(first_img)} Bilder"
          + (f", z. B. {first_img[0][:120]}" if first_img else ""))
    return {
        "title": html.unescape(title).strip()[:150],
        "image": urljoin(url, image) if image else (first_img[0] if first_img else ""),
        "content": lhtml.tostring(body, encoding="unicode"),
        "site": _meta(tree, "og:site_name") or urlparse(url).hostname,
    }


PDF_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; } }
body { font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.5; color: #222; }
h1.doc-title { font-size: 20pt; line-height: 1.2; margin: 0 0 4pt; }
.source { font-size: 8.5pt; color: #777; margin-bottom: 14pt; border-bottom: 1px solid #ddd; padding-bottom: 8pt; }
.source a { color: #777; }
h1, h2, h3, h4 { line-height: 1.25; page-break-after: avoid; }
h2 { font-size: 14pt; } h3 { font-size: 12pt; }
img { max-width: 100%; max-height: 120mm; height: auto; display: block; margin: 6pt auto; page-break-inside: avoid; }
figure { margin: 8pt 0; } figcaption { font-size: 8.5pt; color: #666; text-align: center; }
table { border-collapse: collapse; width: 100%; } td, th { border: 1px solid #ccc; padding: 3pt 5pt; }
a { color: #222; text-decoration: none; }
iframe, video, script, form, button, input { display: none; }
"""


def article_to_pdf(article: dict, url: str, target) -> None:
    from weasyprint import HTML, URLFetcher

    class SafeFetcher(URLFetcher):
        """Lädt Bilder nur von öffentlichen Adressen."""
        def fetch(self, u, headers=None):
            if u.startswith(("http://", "https://")):
                check_public(u)
            return super().fetch(u, headers)

    fetcher = SafeFetcher(timeout=20, http_headers={"User-Agent": UA},
                          allowed_protocols=("http", "https", "data"))

    today = datetime.date.today().strftime("%d.%m.%Y")
    doc = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><style>{PDF_CSS}</style></head><body>
<h1 class="doc-title">{html.escape(article['title'])}</h1>
<div class="source">{html.escape(article['site'] or '')} · <a href="{html.escape(url)}">{html.escape(url)}</a> · gespeichert am {today}</div>
{article['content']}
</body></html>"""
    HTML(string=doc, base_url=url, url_fetcher=fetcher).write_pdf(str(target))


def download_image(url: str, limit: int = 15 * 1024 * 1024) -> tuple[bytes, str] | None:
    """Titelbild laden; liefert (Daten, Endung) oder None."""
    try:
        check_public(url)
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            ctype = r.headers.get("Content-Type", "")
            data = r.read(limit + 1)
    except Exception:
        return None
    if len(data) > limit:
        return None
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(ctype.split(";")[0].strip())
    if not ext:
        m = re.search(r"\.(jpe?g|png|webp)(\?|$)", url, re.I)
        ext = "." + m.group(1).lower().replace("jpeg", "jpg") if m else None
    return (data, ext) if ext else None
