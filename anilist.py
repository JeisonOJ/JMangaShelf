"""Explicit, bounded AniList lookups and a cache for confirmed covers."""

import hashlib
import json
import logging
from version import VERSION
import os
import re
import secrets
import tempfile
import time
from http.client import HTTPException
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from itsdangerous import BadData, URLSafeTimedSerializer

from bookmarks import title_key, title_suggestion
from library import MAX_IMAGE, image_cover

SEARCH_QUERY = """
query ($search: String!) {
  Page(page: 1, perPage: 8) {
    media(type: MANGA, search: $search) {
      id title { romaji english native } synonyms format countryOfOrigin
      startDate { year } coverImage { medium large extraLarge }
    }
  }
}
"""
SEARCH_URL = "https://graphql.anilist.co"
USER_AGENT = f"JMangaShelf/{VERSION} (personal local manga library)"
CDN_HOSTS = {"s4.anilist.co", "s3.anilist.co"}
TIMEOUT = 8
INTERVAL = 3
LOG = logging.getLogger(__name__)


class AniListFailure(Exception):
    def __init__(self, message, status=502, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_image_url(value):
    if not isinstance(value, str) or len(value) > 600:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if (parts.scheme == "https" and parts.netloc in CDN_HOSTS and not parts.query and not parts.fragment
            and re.fullmatch(r"/file/anilistcdn/media/manga/cover/(?:small|medium|large)/[A-Za-z0-9_-]+\.(?:jpe?g|png|webp|gif)",
                             parts.path, re.I)
            and not parts.path.rsplit("/", 1)[-1].lower().startswith(("default.", "placeholder."))):
        return value
    return None


def title_match(work, candidates):
    if not work:
        return None
    titles = {title_key(title) for title in [work["title"], title_suggestion(work["title"])["title"], *work["aliases"]]} - {""}
    expected = {"novel": {"NOVEL"}, "manga": {"MANGA", "ONE_SHOT"}, "manhwa": {"MANGA", "ONE_SHOT"},
                "manhua": {"MANGA", "ONE_SHOT"}}.get(work["type"])
    countries = {"manga": {"JP"}, "manhwa": {"KR"}, "manhua": {"CN", "TW", "HK"}}.get(work["type"])
    if not work["classified"]:
        expected = countries = None
    matches = []
    for candidate in candidates:
        names = {*candidate["titles"].values(), *candidate["aliases"]}
        if titles & {title_key(name) for name in names}:
            matches.append(candidate)
    if len(matches) != 1:
        return None
    candidate = matches[0]
    if not candidate["token"] or (expected and candidate["format"] not in expected):
        return None
    if countries and candidate["country"] and candidate["country"] not in countries:
        return None
    return candidate["id"]


def log_api_error(status, payload=None, ray=None):
    payload = payload if isinstance(payload, dict) else {}
    # Upstream messages can echo a user's query. Only keep diagnostic identifiers.
    code = str(payload.get("error_code", ""))
    ray = str(ray or "")
    LOG.warning("AniList HTTP=%s code=%s ray=%s", status,
                code if code.isdigit() and len(code) <= 10 else "-",
                ray if re.fullmatch(r"[A-Za-z0-9-]{1,64}", ray) else "-")


class AniList:
    def __init__(self, cache, opener=None):
        self.cache = cache
        self.open = opener or build_opener(NoRedirects()).open
        self.tokens = URLSafeTimedSerializer(secrets.token_bytes(32), salt="anilist-cover")
        self.search_lock = Lock()
        self.cache_lock = Lock()
        self.next_search = 0

    def _graphql(self, query):
        request = Request(SEARCH_URL, data=json.dumps({"query": SEARCH_QUERY, "variables": {"search": query}}).encode(),
                          headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT})
        for attempt in range(2):
            wait = self.next_search - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next_search = time.monotonic() + INTERVAL
            try:
                with self.open(request, timeout=TIMEOUT) as response:
                    raw = response.read(256 * 1024 + 1)
                    headers = getattr(response, "headers", {})
                if len(raw) > 256 * 1024:
                    raise ValueError
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError
                errors = payload.get("errors")
                if errors:
                    if not isinstance(errors, list) or not all(isinstance(error, dict) for error in errors):
                        raise ValueError
                    log_api_error(200, payload)
                    if any(error.get("status") == 429 for error in errors):
                        self._rate_limited(headers)
                    raise AniListFailure("La API de AniList devolvió un error de GraphQL. Consulta el registro de la aplicación.")
                return payload["data"]["Page"]["media"]
            except HTTPError as error:
                headers = error.headers or {}
                with error:
                    try:
                        details = json.loads(error.read(8192)) if error.fp is not None else None
                    except (ValueError, OSError, HTTPException, RecursionError):
                        details = None
                log_api_error(error.code, details, headers.get("CF-Ray"))
                if error.code == 429:
                    self._rate_limited(headers)
                if error.code not in (500, 502, 503, 504) or attempt:
                    raise AniListFailure(f"La API de AniList devolvió un error HTTP {error.code}. Consulta el registro de la aplicación.") from None
            except (URLError, OSError, HTTPException) as error:
                LOG.warning("AniList search network failure: %r", error)
                if attempt:
                    raise AniListFailure("Fallo temporal de conexión con AniList. Reintenta cuando haya conexión.") from None
            except (KeyError, TypeError, ValueError, RecursionError) as error:
                LOG.warning("AniList search invalid response: %r", error)
                raise AniListFailure("AniList devolvió una respuesta no válida.") from None

    def _rate_limited(self, headers):
        retry = str(headers.get("Retry-After", "")).strip()
        reset = str(headers.get("X-RateLimit-Reset", "")).strip()
        if retry.isdecimal() and len(retry) <= 6:
            seconds = max(INTERVAL, int(retry))
        elif reset.isdecimal() and len(reset) <= 12:
            seconds = max(INTERVAL, int(int(reset) - time.time()) + 1)
        else:
            seconds = 60
        self.next_search = time.monotonic() + seconds
        raise AniListFailure(f"AniList alcanzó su límite de consultas. Reintenta en {seconds} s.", 429, seconds)

    def search(self, query, work=None):
        if not self.search_lock.acquire(blocking=False):
            raise AniListFailure("Hay una búsqueda en curso. Espera a que termine.", 429, INTERVAL)
        try:
            wait = self.next_search - time.monotonic()
            if wait > INTERVAL:
                seconds = int(wait) + 1
                raise AniListFailure(f"Espera {seconds} s para volver a consultar AniList.", 429, seconds)
            result = self._graphql(query)
        finally:
            self.search_lock.release()
        if not isinstance(result, list):
            LOG.warning("AniList search response has unexpected media type: %s", type(result).__name__)
            raise AniListFailure("AniList devolvió una lista de resultados no válida.")
        candidates = []
        seen = set()
        for media in result[:8]:
            if not isinstance(media, dict) or type(media.get("id")) is not int or media["id"] <= 0 or media["id"] in seen:
                continue
            seen.add(media["id"])
            titles = media.get("title") if isinstance(media.get("title"), dict) else {}
            names = {key: value[:300] for key, value in titles.items()
                     if key in {"romaji", "english", "native"} and isinstance(value, str) and value.strip()}
            aliases = media.get("synonyms")
            images = media.get("coverImage") if isinstance(media.get("coverImage"), dict) else {}
            url = next((value for key in ("extraLarge", "large", "medium") if (value := safe_image_url(images.get(key)))), None)
            start = media.get("startDate") if isinstance(media.get("startDate"), dict) else {}
            candidates.append({
                "id": media["id"], "titles": names,
                "aliases": [s[:300] for s in aliases[:12] if isinstance(s, str) and s.strip()] if isinstance(aliases, list) else [],
                "format": media.get("format") if isinstance(media.get("format"), str) else None,
                "country": media.get("countryOfOrigin") if isinstance(media.get("countryOfOrigin"), str) else None,
                "year": start.get("year") if type(start.get("year")) is int else None,
                "thumbnail": (safe_image_url(images.get("medium")) or url) if url else None, "image": url,
                "token": self.tokens.dumps({"url": url}) if url else None,
            })
        if result and not candidates:
            LOG.warning("AniList search returned media without valid IDs")
            raise AniListFailure("AniList devolvió resultados con un formato no válido.")
        return {"candidates": candidates, "suggested": title_match(work, candidates)}

    def cover(self, token):
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError("Selecciona una portada de los resultados de AniList.")
        try:
            url = safe_image_url(self.tokens.loads(token, max_age=45 * 60)["url"])
        except (BadData, KeyError, TypeError):
            url = None
        if not url:
            raise ValueError("La selección de AniList ya no está disponible. Vuelve a buscar la portada.")
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".webp")
        with self.cache_lock:
            try:
                if self.cache.resolve() != self.cache.parent.resolve() / self.cache.name:
                    raise OSError("Unexpected cover cache location")
                if path.is_file() and not path.is_symlink() and path.resolve().parent == self.cache.resolve():
                    with path.open("rb") as cached:
                        data = cached.read(MAX_IMAGE + 1)
                    try:
                        return image_cover(data, preserve=True)
                    except ValueError:
                        pass
                request = Request(url, headers={"Accept": "image/webp,image/jpeg,image/png,image/gif", "User-Agent": USER_AGENT})
                with self.open(request, timeout=TIMEOUT) as response:
                    data = response.read(MAX_IMAGE + 1)
                cover = image_cover(data)
                self.cache.mkdir(parents=True, exist_ok=True)
                temp = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.cache, prefix="cover-", suffix=".tmp", delete=False) as handle:
                        temp = handle.name
                        handle.write(cover[2])
                    os.replace(temp, path)
                finally:
                    if temp is not None:
                        self.cache.joinpath(os.path.basename(temp)).unlink(missing_ok=True)
                return cover
            except HTTPError as error:
                error.close()
                raise AniListFailure("La imagen de AniList no está disponible. Elige otra portada o reintenta.") from None
            except (URLError, OSError):
                raise AniListFailure("No se pudo descargar o guardar la portada. Comprueba la conexión y el espacio en disco.") from None
            except ValueError:
                raise AniListFailure("AniList devolvió una imagen no válida o demasiado grande.") from None
