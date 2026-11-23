"""SQLite storage and validation for works, links, and portable backups."""

import base64
import binascii
import io
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from itertools import groupby

from PIL import Image, ImageOps, UnidentifiedImageError

from urllib.parse import urlsplit
from bookmarks import reference_hint, title_suggestion, web_url

TYPES = {"manga", "manhwa", "manhua", "novel", "other"}
STATUSES = {"planned", "reading", "on_hold", "completed", "dropped"}
MAX_IMAGE = 5 * 1024 * 1024
IMAGE_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
MAX_MAL_ID = 2_147_483_647

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('manga','manhwa','manhua','novel','other')),
    status TEXT NOT NULL CHECK (status IN ('planned','reading','on_hold','completed','dropped')),
    chapter TEXT NOT NULL,
    notes TEXT NOT NULL,
    tags TEXT NOT NULL,
    bookmark_paths TEXT NOT NULL DEFAULT '[]',
    cover_url TEXT,
    cover_mime TEXT,
    cover_data BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    paths TEXT NOT NULL,
    PRIMARY KEY (work_id, position)
);
"""


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def obj(value):
    if not isinstance(value, dict):
        raise ValueError("El formato de los datos no es válido.")
    return value


def items(value, maximum, label):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} debe ser una lista de hasta {maximum} elementos.")
    return value


def clean(value, maximum, label, *, required=False, multiline=False):
    if not isinstance(value, str):
        raise ValueError(f"{label} debe ser texto.")
    allowed = "\r\n\t" if multiline else ""
    if any((ord(char) < 32 and char not in allowed) or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f"{label} contiene caracteres no válidos.")
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise ValueError(f"{label} admite {'entre 1 y' if required else 'hasta'} {maximum} caracteres.")
    return value


def folder_path(value):
    path = items(value, 100, "La ruta de carpeta")
    return [clean(part, 300, "El nombre de carpeta", required=True) for part in path]


def decode_cover(value, *, preserve=False):
    if value is None:
        return (None, None, None)
    value = obj(value)
    if value.get("kind") == "url":
        return (web_url(value.get("url")), None, None)
    encoded = value.get("data")
    if value.get("kind") != "local" or not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE + 2) // 3):
        raise ValueError("La portada debe ser una imagen de hasta 5 MB o una URL.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("La portada contiene datos no válidos.") from None
    return image_cover(data, preserve=preserve)


def image_cover(data, *, preserve=False):
    try:
        if len(data) > MAX_IMAGE:
            raise ValueError
        with Image.open(io.BytesIO(data), formats=list(IMAGE_TYPES)) as image:
            if image.width * image.height > 24_000_000:
                raise ValueError
            image.load()
            if preserve:
                return (None, IMAGE_TYPES[image.format], data)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((800, 1000))
            image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            output = io.BytesIO()
            image.save(output, "WEBP", quality=85, method=4)
            return (None, "image/webp", output.getvalue())
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Usa una imagen JPG, PNG, GIF o WebP válida de hasta 5 MB y 24 megapíxeles.") from None


def source_fields(link, url, old):
    original = link.get("original_url", old.get("original_url", link["url"]))
    web_url(original)
    titles = items(link.get("original_titles", old.get("original_titles", [])), 20000, "Los títulos originales")
    role = link.get("role", old.get("role", "reading"))
    origin = link.get("origin", old.get("origin", "Manual"))
    health = link.get("health", old.get("health", "unchecked"))
    if health not in ("unchecked", "working", "broken", "suspicious"):
        raise ValueError("Selecciona un estado de enlace válido.")
    if role not in ("reading", "reference") or origin not in ("Manual", "Edge", "Navegador", "WhatsApp", "X", "Otro"):
        raise ValueError("Selecciona un origen y una función válidos.")
    return {"original_url": original.strip(), "original_titles": [clean(t, 8192, "El título original", required=True, multiline=True) for t in titles],
            "role": role, "origin": origin, "health": health,
            "language": clean(link.get("language", old.get("language", "")), 60, "El idioma")}


def validate_work(value, previous_links=None, *, backup=False):
    value = obj(value)
    kind, status = value.get("type"), value.get("status")
    if not isinstance(kind, str) or kind not in TYPES or not isinstance(status, str) or status not in STATUSES:
        raise ValueError("Selecciona un tipo de obra y un estado válidos.")
    tags = {}
    for tag in items(value.get("tags"), 40, "Las etiquetas"):
        tag = clean(tag, 60, "La etiqueta", required=True)
        tags.setdefault(tag.casefold(), tag)
    links = []
    seen_urls = set()
    for link in items(value.get("links"), 100, "Los enlaces"):
        link = obj(link)
        url = web_url(link.get("url"))
        if url in seen_urls:
            raise ValueError("La obra contiene un enlace repetido.")
        seen_urls.add(url)
        previous_url = web_url(link["previous_url"]) if "previous_url" in link else url
        old = (previous_links or {}).get(previous_url, {})
        if backup:
            paths = [folder_path(path) for path in items(link.get("paths"), 20000, "Las carpetas originales")]
        else:
            # Only retain import metadata on links whose URL still matches.
            paths = old.get("paths", [])
        links.append({
            **source_fields(link, url, old),
            "url": url,
            "label": clean(link.get("label"), 100, "El nombre del enlace"),
            "paths": paths,
        })
    classified = value.get("classified", True)
    if type(classified) is not bool:
        raise ValueError("Clasificación no válida.")
    rating = value.get("rating")
    if rating is not None and (type(rating) is not int or not 0 <= rating <= 10):
        raise ValueError("La puntuación debe ser un número entero de 0 a 10 o quedar vacía.")
    return {
        "rating": rating,
        "aliases": list(dict.fromkeys(clean(alias, 300, "El título alternativo", required=True)
                         for alias in items(value.get("aliases", []), 1000, "Los títulos alternativos"))),
        "classified": classified,
        "title": clean(value.get("title"), 300, "El título", required=True),
        "type": kind,
        "status": status,
        "chapter": clean(value.get("chapter"), 80, "El capítulo"),
        "notes": clean(value.get("notes"), 20000, "Las notas", multiline=True),
        "tags": list(tags.values()),
        "links": links,
    }


def validate_backup(value):
    value = obj(value)
    if value.get("format") != "JMangaShelf" or type(value.get("version")) is not int or value["version"] not in (1, 2, 3, 4):
        raise ValueError("Este archivo no es una copia compatible de JMangaShelf.")
    allowed_top = {"format", "version", "works"}
    if value["version"] >= 3:
        allowed_top.add("review_rejections")
        for signature in items(value.get("review_rejections"), 100000, "Las decisiones"):
            if not isinstance(signature, str) or not re.fullmatch("[0-9a-f]{64}", signature):
                raise ValueError("Decisión de revisión inválida.")
    if value.keys() - allowed_top:
        raise ValueError("La copia contiene campos desconocidos.")
    works = []
    ids, urls, mal_ids = set(), set(), set()
    for raw in items(value.get("works"), 100000, "Las obras"):
        raw = obj(raw)
        allowed = {"id", "title", "type", "status", "chapter", "notes", "tags", "cover", "links",
                   "created_at", "updated_at", "bookmark_paths"}
        link_fields = {"label", "url", "paths"}
        if value["version"] >= 2:
            allowed.add("classified")
            link_fields.update({"original_url", "origin", "language", "role", "original_titles"})
        if value["version"] >= 3:
            allowed.update({"aliases", "alternate_covers"})
            link_fields.add("health")
            if not {"aliases", "alternate_covers"} <= raw.keys() or any("health" not in obj(link)
                    for link in items(raw.get("links"), 100, "Los enlaces")):
                raise ValueError("Faltan datos de organización en la copia.")
        if value["version"] == 4:
            allowed.update({"rating", "mal_ids"})
            if not {"rating", "mal_ids"} <= raw.keys():
                raise ValueError("Faltan la puntuación y los identificadores de MyAnimeList.")
        if raw.keys() - allowed or any(obj(link).keys() - link_fields for link in items(raw.get("links"), 100, "Los enlaces")):
            raise ValueError("La obra o sus fuentes contienen campos desconocidos.")
        cover = raw.get("cover")
        if cover is not None and obj(cover).keys() - ({"kind", "url"} if cover.get("kind") == "url" else {"kind", "data"}):
            raise ValueError("La portada contiene campos desconocidos.")
        if value["version"] >= 2:
            if "classified" not in raw or any(not {"original_url", "original_titles", "origin", "role", "language"} <= obj(link).keys()
                                               for link in items(raw.get("links"), 100, "Los enlaces")):
                raise ValueError("Faltan datos de organización o de fuentes en la copia.")
        else:
            raw = dict(raw, classified=not bool(raw.get("bookmark_paths")))
            for link in items(raw.get("links"), 100, "Los enlaces"):
                if obj(link).get("paths"):
                    link.setdefault("origin", "Edge")
                    if len(raw["links"]) == 1 and raw.get("created_at") == raw.get("updated_at"):
                        link.setdefault("original_titles", [raw.get("title")])
        work = validate_work(raw, backup=True)
        work["mal_ids"] = items(raw.get("mal_ids", []), 10000, "Los identificadores de MyAnimeList")
        for mal_id in work["mal_ids"]:
            if type(mal_id) is not int or not 1 <= mal_id <= MAX_MAL_ID or mal_id in mal_ids:
                raise ValueError("La copia contiene un ID de MyAnimeList duplicado o inválido.")
            mal_ids.add(mal_id)
        work["alternate_covers"] = []
        for entry in items(raw.get("alternate_covers", []), 20000, "Las portadas alternativas"):
            fields = {"kind", "url"} if obj(entry).get("kind") == "url" else {"kind", "data"}
            if entry.keys() != fields:
                raise ValueError("Portada alternativa inválida.")
            work["alternate_covers"].append(decode_cover(entry, preserve=True))
        try:
            identifier = str(uuid.UUID(raw["id"]))
            dates = [datetime.fromisoformat(raw[key]) for key in ("created_at", "updated_at")]
            if any(date.utcoffset() is None for date in dates) or dates[1] < dates[0]:
                raise ValueError
            created, updated = (date.astimezone(timezone.utc).isoformat(timespec="microseconds") for date in dates)
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            raise ValueError("La copia contiene un identificador o una fecha no válidos.") from None
        work_urls = {link["url"] for link in work["links"]}
        if identifier in ids or urls & work_urls:
            raise ValueError("La copia contiene obras o URLs duplicadas.")
        if "cover" not in raw:
            raise ValueError("Faltan datos de portada en la copia.")
        work["bookmark_paths"] = [
            folder_path(path) for path in items(raw.get("bookmark_paths"), 20000, "Las carpetas originales")
        ]
        ids.add(identifier)
        urls.update(work_urls)
        works.append((identifier, work, decode_cover(raw["cover"], preserve=True), created, updated))
    return works


def list_works(db, *, identifier=None, backup=False):
    columns = "*, cover_data IS NOT NULL AS has_cover" if backup else (
        "id, title, type, status, chapter, notes, tags, aliases, classified, bookmark_paths, cover_url, rating, "
        "cover_data IS NOT NULL AS has_cover, created_at, updated_at"
    )
    if identifier is None:
        rows = db.execute(f"SELECT {columns} FROM works ORDER BY id")
        links = db.execute("SELECT * FROM links ORDER BY work_id, position")
    else:
        rows = db.execute(f"SELECT {columns} FROM works WHERE id = ?", (identifier,))
        links = db.execute("SELECT * FROM links WHERE work_id = ? ORDER BY position", (identifier,))
    by_work = {
        key: [{**{field: row[field] for field in ("url", "original_url", "label", "origin", "role", "language", "health")},
               "original_titles": json.loads(row["original_titles"]), "paths": json.loads(row["paths"])} for row in group]
        for key, group in groupby(links, key=lambda row: row["work_id"])
    }
    extra_sql = "SELECT id,work_id,cover_url" + (",cover_data" if backup else "") + " FROM alternate_covers"
    if identifier is not None:
        extra_sql += " WHERE work_id=?"
    alternatives = {}
    for entry in db.execute(extra_sql + " ORDER BY id", (identifier,) if identifier is not None else ()):
        if entry["cover_url"]:
            cover = {"kind": "url", "url": entry["cover_url"]}
        elif backup:
            cover = {"kind": "local", "data": base64.b64encode(entry["cover_data"]).decode("ascii")}
        else:
            cover = {"kind": "local", "url": f"/api/works/{entry['work_id']}/covers/{entry['id']}"}
        alternatives.setdefault(entry["work_id"], []).append(cover)
    mal_query = "SELECT work_id, mal_id FROM mal_entries"
    if identifier is not None:
        mal_query += " WHERE work_id=?"
    mal_by_work = {}
    for entry in db.execute(mal_query + " ORDER BY mal_id", () if identifier is None else (identifier,)):
        mal_by_work.setdefault(entry["work_id"], []).append(entry["mal_id"])
    result = []
    for row in rows:
        work = {key: row[key] for key in ("id", "title", "type", "status", "chapter", "notes", "rating", "created_at", "updated_at")}
        work["mal_ids"] = mal_by_work.get(row["id"], [])
        work["classified"] = bool(row["classified"])
        work["aliases"] = json.loads(row["aliases"])
        work["alternate_covers"] = alternatives.get(row["id"], [])
        work["tags"] = json.loads(row["tags"])
        work["bookmark_paths"] = json.loads(row["bookmark_paths"])
        work["links"] = by_work.get(row["id"], [])
        if not backup:
            work["suggestion"] = title_suggestion(work["title"])
            for link in work["links"]:
                link["website"] = urlsplit(link["url"]).hostname
                link["reference_hint"] = reference_hint(link["url"])
        cover = None
        if row["cover_url"]:
            cover = {"kind": "url", "url": row["cover_url"]}
        elif row["has_cover"]:
            cover = {"kind": "local"}
            if backup:
                cover["data"] = base64.b64encode(row["cover_data"]).decode("ascii")
            else:
                cover["url"] = f"/api/works/{row['id']}/cover?v={row['updated_at']}"
        work["cover"] = cover
        result.append(work)
    return result


def save_work(db, identifier, work, cover, created, updated, *, editing=False):
    values = (work["title"], work["type"], work["status"], work["chapter"], work["notes"], json.dumps(work["tags"]))
    if editing:
        db.execute(
            "UPDATE works SET title=?, type=?, status=?, chapter=?, notes=?, tags=?, updated_at=? WHERE id=?",
            (*values, updated, identifier),
        )
        if cover is not None:
            db.execute("UPDATE works SET cover_url=?, cover_mime=?, cover_data=? WHERE id=?", (*cover, identifier))
        db.execute("DELETE FROM links WHERE work_id=?", (identifier,))
    else:
        db.execute(
            """INSERT INTO works (id,title,type,status,chapter,notes,tags,bookmark_paths,cover_url,cover_mime,cover_data,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (identifier, *values, json.dumps(work.get("bookmark_paths", [])), *cover, created, updated),
        )
    db.execute("UPDATE works SET classified=?, aliases=?, rating=? WHERE id=?",
               (work["classified"], json.dumps(work["aliases"]), work.get("rating"), identifier))
    if not editing:
        db.executemany("INSERT INTO mal_entries(mal_id,work_id) VALUES(?,?)",
                       [(mal_id, identifier) for mal_id in work.get("mal_ids", [])])
        db.executemany("INSERT INTO alternate_covers(work_id,cover_url,cover_mime,cover_data) VALUES(?,?,?,?)",
                       [(identifier, *cover) for cover in work.get("alternate_covers", [])])
    # The first reading link is the preferred source.
    ordered = sorted(work["links"], key=lambda link: link["role"] == "reference")
    db.executemany(
        """INSERT INTO links (work_id,position,label,url,paths,original_url,original_titles,origin,language,role,health)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [(identifier, index, link["label"], link["url"], json.dumps(link["paths"]), link["original_url"],
          json.dumps(link["original_titles"]), link["origin"], link["language"], link["role"], link.get("health", "unchecked"))
         for index, link in enumerate(ordered)],
    )
