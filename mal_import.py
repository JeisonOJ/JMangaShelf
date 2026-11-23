"""Review and import MyAnimeList manga XML exports without contacting MAL."""

import gzip
import hashlib
import io
import json
import re
import uuid
import zlib
from collections import Counter, defaultdict
from datetime import date
from urllib.parse import urlsplit
from xml.etree import ElementTree

from flask import abort, jsonify, request

from bookmarks import title_key
from library import MAX_MAL_ID, clean, list_works, now, obj, save_work, validate_work
from migration import verified_snapshot

MAX_EXPORT = 16 * 1024 * 1024
MAX_ENTRIES = 10000
MAL_STATUSES = {"reading": "reading", "completed": "completed", "on-hold": "on_hold",
                "dropped": "dropped", "plan to read": "planned"}


def integer(text, limit, label):
    if not re.fullmatch(r"[0-9]{1,10}", text) or int(text) > limit:
        raise ValueError(f"{label} no válido.")
    return int(text)


def export_date(text):
    try:
        return date.fromisoformat(text).isoformat() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None
    except ValueError:
        return None


def parse_mal_export(raw):
    if len(raw) > MAX_EXPORT:
        raise ValueError("El archivo de MyAnimeList supera los 16 MB.")
    try:
        if raw.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                raw = compressed.read(MAX_EXPORT + 1)
        if len(raw) > MAX_EXPORT:
            raise ValueError("El XML descomprimido supera los 16 MB.")
        xml = raw.decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I):
            raise ValueError("El XML contiene declaraciones que no admite el importador.")
        root = ElementTree.fromstring(xml)
    except (UnicodeError, OSError, EOFError, zlib.error, ElementTree.ParseError):
        raise ValueError("El archivo no contiene un XML de MyAnimeList válido.") from None
    if (root.tag != "myanimelist" or root.find("anime") is not None
            or root.findtext("myinfo/user_export_type", "").strip() not in ("", "2")
            or not (root.findall("manga") or root.findtext("myinfo/user_export_type", "").strip() == "2")):
        raise ValueError("Selecciona una lista de manga de MyAnimeList en XML o XML.GZ.")
    nodes = root.findall("manga")
    if len(nodes) > MAX_ENTRIES:
        raise ValueError("El archivo contiene más de 10 000 entradas.")
    rows = []
    for index, element in enumerate(nodes, 1):
        def field(name):
            return element.findtext(name, "").strip()

        title = field("manga_title")
        try:
            title = clean(title, 300, "El título", required=True)
            mal_id = integer(field("manga_mangadb_id"), MAX_MAL_ID, "ID de MyAnimeList")
            if not mal_id:
                raise ValueError("ID de MyAnimeList no válido.")
            score = field("my_score")
            rating = (integer(score, 10, "Puntuación") or None) if score else None
            chapters = field("my_read_chapters")
            progress = integer(chapters, 2_147_483_647, "Número de capítulos") if chapters else None
            status = field("my_status").casefold()
            if status and status not in MAL_STATUSES:
                raise ValueError("Estado de lectura desconocido.")
            rows.append({"row": index, "id": mal_id, "title": title, "rating": rating, "status": MAL_STATUSES.get(status),
                         "chapter": None if progress is None else str(progress) if progress else "",
                         "started": export_date(field("my_start_date")), "finished": export_date(field("my_finish_date"))})
        except ValueError as error:
            rows.append({"row": index, "title": title[:300], "error": str(error)})
    counts = Counter(row["id"] for row in rows if "id" in row)
    for row in rows:
        if counts[row.get("id")] > 1:
            row["error"] = "El ID de MyAnimeList aparece más de una vez en el archivo."
    return rows


def mal_url_id(url):
    parts = urlsplit(url)
    match = re.match(r"^/manga/([0-9]{1,10})(?:/|$)", parts.path)
    if match and parts.hostname in {"myanimelist.net", "www.myanimelist.net"}:
        return int(match[1])
    return None


def review_rows(rows, works):
    by_id, by_url, by_title = {}, defaultdict(set), defaultdict(set)
    for work in works:
        for mal_id in work["mal_ids"]:
            by_id[mal_id] = work["id"]
        for link in work["links"]:
            for url in (link["url"], link["original_url"]):
                mal_id = mal_url_id(url)
                if mal_id:
                    by_url[mal_id].add(work["id"])
        for title in [work["title"], work["suggestion"]["title"], *work["aliases"]]:
            by_title[title_key(title)].add(work["id"])
    title_counts = Counter(title_key(row["title"]) for row in rows if not row.get("error"))
    result = []
    for row in rows:
        ids, strong = set(), False
        registered = False
        if row.get("error"):
            reason, category = row["error"], "skipped"
        elif row["id"] in by_id:
            ids, strong, registered = {by_id[row["id"]]}, True, True
            reason, category = "ID de MyAnimeList ya vinculado a esta ficha.", "strong"
        elif ids := by_url[row["id"]]:
            strong = len(ids) == 1
            reason = "Una fuente local contiene este ID de MyAnimeList." if strong else "Varias fichas contienen este ID en sus enlaces."
            category = "strong" if strong else "ambiguous"
        else:
            key = title_key(row["title"])
            ids = by_title[key] if key else set()
            if ids or title_counts[key] > 1:
                reason = "Coincide por título o alias. Comprueba la edición y el formato." if ids else "El archivo contiene el mismo título con varios ID."
                category = "ambiguous"
            else:
                reason, category = "Ficha nueva sin coincidencias locales.", "new"
        result.append({**row, "category": category, "reason": reason, "matches": sorted(ids),
                       "registered": registered, "allow_create": category in {"new", "ambiguous"} and not by_url.get(row.get("id")),
                       "target": next(iter(ids)) if strong else None})
    return result


def read_upload():
    file = request.files.get("file")
    if file is None:
        raise ValueError("Selecciona el XML de manga de MyAnimeList.")
    raw = file.read(MAX_EXPORT + 1)
    return hashlib.sha256(raw).hexdigest(), parse_mal_export(raw)


def review_version(file_hash, works):
    versions = [(work["id"], work["updated_at"]) for work in works]
    return hashlib.sha256(json.dumps([file_hash, versions]).encode()).hexdigest()


def register_mal_import(app, get_db):
    @app.post("/api/mal/preview")
    def preview_mal():
        file_hash, rows = read_upload()
        db = get_db()
        with db:
            db.execute("BEGIN")
            works = list_works(db)
            return jsonify(rows=review_rows(rows, works), version=review_version(file_hash, works),
                           works=[{key: work[key] for key in ("id", "title", "type", "rating", "status", "chapter", "mal_ids")}
                                  for work in works])

    @app.post("/api/mal/import")
    def import_mal():
        request.max_form_memory_size = 2 * 1024 * 1024
        if request.form.get("confirm") != "1":
            raise ValueError("Confirma la importación de MyAnimeList.")
        try:
            choices = obj(json.loads(request.form.get("choices", "")))
        except (ValueError, RecursionError):
            raise ValueError("Revisa las decisiones de importación.") from None
        file_hash, rows = read_upload()
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            works = list_works(db)
            if review_version(file_hash, works) != request.form.get("version"):
                abort(409, "El archivo o la biblioteca cambió. Genera otra vista previa.")
            by_id = {work["id"]: work for work in works}
            reviewed = {str(row["row"]): row for row in review_rows(rows, works)}
            plan = []
            used = set()
            for index, raw_choice in choices.items():
                row = reviewed.get(index)
                if not row or row.get("error"):
                    raise ValueError("Una entrada seleccionada no se puede importar.")
                choice = obj(raw_choice)
                action = choice.get("action")
                if action == "skip":
                    continue
                if action == "create" and row["allow_create"]:
                    plan.append((row, None, None, {}))
                    continue
                target = clean(choice.get("target"), 36, "La obra de destino") if action == "link" else ""
                work = by_id.get(target)
                fixed_match = row["matches"] and not row["allow_create"]
                if not work or (fixed_match and target not in row["matches"]) or target in used:
                    raise ValueError("Selecciona una ficha compatible y distinta para cada entrada.")
                if work["mal_ids"] and row["id"] not in work["mal_ids"]:
                    raise ValueError("La ficha elegida ya está vinculada a otro ID de MyAnimeList.")
                used.add(target)
                fields = {}
                for key in ("rating", "status", "chapter"):
                    use_import = choice.get(key, False)
                    if type(use_import) is not bool or (use_import and row[key] is None):
                        raise ValueError("La elección de datos de MyAnimeList no es válida.")
                    if use_import and work[key] != row[key]:
                        fields[key] = row[key]
                if fields or row["id"] not in work["mal_ids"]:
                    plan.append((row, target, work, fields))
            if not plan:
                return jsonify(created=0, linked=0, ignored=len(rows), backup=None)
            snapshot = verified_snapshot(app.config["DATABASE"], "antes-importar-mal")
            created = linked = 0
            stamp = now()
            for row, target, work, fields in plan:
                if work is None:
                    target = str(uuid.uuid4())
                    new_work = validate_work({"title":row["title"], "type":"other", "status":row["status"] or "planned",
                                              "chapter":row["chapter"] or "", "rating":row["rating"], "classified":False,
                                              "notes":"", "tags":[], "links":[]})
                    new_work["mal_ids"] = [row["id"]]
                    save_work(db, target, new_work, (None, None, None), stamp, stamp)
                    created += 1
                else:
                    if row["id"] not in work["mal_ids"]:
                        db.execute("INSERT INTO mal_entries(mal_id,work_id) VALUES(?,?)", (row["id"], target))
                    fields["updated_at"] = stamp
                    columns = ",".join(f"{key}=?" for key in fields)
                    db.execute(f"UPDATE works SET {columns} WHERE id=?", (*fields.values(), target))
                    linked += 1
            return jsonify(created=created, linked=linked, ignored=len(rows) - len(plan), backup=snapshot.name)
