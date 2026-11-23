"""Read Edge and other Netscape bookmark exports without accessing a profile."""

import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit


def web_url(value):
    if not isinstance(value, str):
        raise ValueError("Introduce una URL válida.")
    value = value.strip()
    if not value or len(value) > 4096 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        raise ValueError("Introduce una URL válida sin espacios.")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if parts.scheme not in ("http", "https") or not host or parts.username is not None:
            raise ValueError
        if ":" in host:
            host = f"[{host}]"
        else:
            host = host.encode("idna").decode("ascii").lower()
            if not re.fullmatch(r"[a-z0-9_.-]+", host):
                raise ValueError
        default_port = 443 if parts.scheme == "https" else 80
        authority = f"{host}:{port}" if port is not None and port != default_port else host
        path = parts.path or "/"
        # Keep query order, fragments and trailing slashes. Reader URLs can use all three.
        result = urlunsplit((parts.scheme, authority, path, parts.query, parts.fragment))
        if "?" in value.split("#", 1)[0] and not parts.query:
            base, marker, fragment = result.partition("#")
            result = f"{base}?{marker}{fragment}"
        if "#" in value and not parts.fragment:
            result += "#"
        if len(result) > 4096:
            raise ValueError
        return result
    except (ValueError, UnicodeError):
        raise ValueError("Usa una URL HTTP o HTTPS válida, sin credenciales.") from None


def title_key(title):
    normalized = unicodedata.normalize("NFKD", title.casefold())
    plain = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[^\W_]+", plain))


TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "utm_id", "fbclid", "gclid", "msclkid"}


def review_url(value):
    """A comparison key only. Keep routing, query order, encoding and fragments."""
    base, fragment_marker, fragment = web_url(value).partition("#")
    base, query_marker, query = base.partition("?")
    parts = urlsplit(base)
    authority = parts.netloc
    if parts.port is None and re.fullmatch(r"www\.[a-z0-9-]+\.[a-z]{3,}", parts.hostname):
        authority = authority[4:]
    path = parts.path
    if re.fullmatch(r"(?:/[\w~-]+)+/", path):
        path = path[:-1]
    if query and ";" not in query:
        retained = [part for part in query.split("&") if part.partition("=")[0] not in TRACKING]
        query_marker = "?" if retained else ""
        query = "&".join(retained)
    return f"{parts.scheme}://{authority}{path}{query_marker}{query}{fragment_marker}{fragment}"


CATALOG_DOMAINS = {"anilist.co", "myanimelist.net", "mangaupdates.com", "kitsu.app",
                   "anime-planet.com", "novelupdates.com", "mangacollec.com"}
SEARCH_DOMAINS = {"google.com", "google.com.co", "bing.com", "duckduckgo.com", "search.yahoo.com"}


def reference_hint(url):
    parts = urlsplit(url)
    host = parts.hostname.removeprefix("www.")
    segments = unquote(parts.path).lower().strip("/").split("/")
    query_keys = {key.lower() for key, _ in parse_qsl(parts.query)}
    if (segments[0] in {"search", "buscar", "busqueda", "búsqueda", "results"}
            or segments[-1] in {"search", "search.php", "buscar"}
            or host in SEARCH_DOMAINS and query_keys & {"q", "p"}
            or not segments[0] and query_keys & {"s", "q", "search", "keyword"}):
        return "search"
    if host in CATALOG_DOMAINS:
        return "catalog"
    return ""


def title_suggestion(title):
    """Strip only recognizable reader boilerplate, leaving volume and sequel numbers."""
    candidate = re.sub(r"\s+[-–—|]\s+(?:lector manga|manhwaweb|novel cool|"
                       r"(?:manga|manhwa|manhua|novel) online in english|leer manga online)\s*$",
                       "", title, flags=re.I)
    chapter = re.search(r"\s+(?:cap[ií]tulo|chapter)\s+(\d+(?:[.,]\d+)?)(?:\s+en español)?\s*$", candidate, re.I)
    if chapter:
        candidate = candidate[:chapter.start()]
    spanish_reader = chapter and re.search(r"en español\b", chapter.group(), re.I)
    if spanish_reader or re.search(r"[-–—|]\s*lector manga\s*$", title, re.I):
        candidate = re.sub(r"^leer\s+(?!o\b|y\b|es\b|para\b)", "", candidate, flags=re.I).strip()
    elif re.search(r"manga online in english\s*$", title, re.I):
        candidate = re.sub(r"^read\s+(?!or\b|and\b)", "", candidate, flags=re.I).strip()
    return {"title": candidate or title, "chapter": chapter.group(1) if chapter else ""}


class BookmarkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.levels = []
        self.pending_folder = None
        self.capture = None
        self.buffer = []
        self.href = ""
        self.rows = []
        self.ignored = 0
        self.saw_list = False

    def handle_starttag(self, tag, attrs):
        if tag in {"dt", "dl", "h3", "a"}:
            self.finish_capture()
        if tag == "dl":
            self.saw_list = True
            if len(self.levels) >= 100:
                raise ValueError("El archivo contiene demasiados niveles de carpetas.")
            self.levels.append(self.pending_folder)
            self.pending_folder = None
        elif tag in {"h3", "a"}:
            self.pending_folder = None
            self.capture = tag
            self.href = dict(attrs).get("href") or ""

    def handle_endtag(self, tag):
        if tag == self.capture:
            self.finish_capture()
        if tag == "dl":
            self.finish_capture()
            if self.levels:
                self.levels.pop()
            self.pending_folder = None

    def handle_data(self, data):
        if self.capture:
            self.buffer.append(data)

    def finish_capture(self):
        if not self.capture:
            return
        visible = "".join(char for chunk in self.buffer for char in chunk if ord(char) >= 32 or char.isspace())
        original_title = visible.strip()
        title = " ".join(visible.split())[:300]
        if self.capture == "h3":
            self.pending_folder = title or None
        else:
            if len(self.rows) + self.ignored >= 20000:
                raise ValueError("El archivo supera el límite de 20 000 marcadores.")
            try:
                url = web_url(self.href)
            except ValueError:
                self.ignored += 1
            else:
                if len(original_title) > 8192:
                    raise ValueError("Un título original supera los 8192 caracteres.")
                self.rows.append({
                    "original_title": original_title,
                    "title": title or urlsplit(url).hostname[:300],
                    "url": url, "original_url": self.href.strip(),
                    "path": [name for name in self.levels if name],
                })
        self.buffer.clear()
        self.capture = None


def parse_bookmarks(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        source = raw.decode("utf-16", errors="replace")
    else:
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            source = raw.decode("windows-1252", errors="replace")
    parser = BookmarkParser()
    parser.feed(source)
    parser.close()
    parser.finish_capture()
    if not parser.saw_list or not re.search(
        r"NETSCAPE-Bookmark-file-1|<title\b[^>]*>\s*Bookmarks\b", source, re.I
    ):
        raise ValueError("Selecciona un archivo HTML de marcadores exportado desde tu navegador.")
    return parser.rows, parser.ignored
