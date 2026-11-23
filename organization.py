"""Local capture, duplicate review and user-confirmed organization."""
import json
import uuid
from itertools import combinations
from urllib.parse import urlsplit

from flask import abort, jsonify, request

from bookmarks import title_suggestion, web_url
from duplicate_review import candidate_review, pair_key
from library import clean, items, list_works, now, obj, save_work, validate_work
from migration import verified_snapshot


def social_origin(url):
    host = urlsplit(url).hostname
    for domain, origin in (("x.com", "X"), ("twitter.com", "X"), ("whatsapp.com", "WhatsApp"), ("wa.me", "WhatsApp")):
        if host == domain or host.endswith("." + domain):
            return origin
    return "Manual"


def link_role(url):
    return "reading" if social_origin(url) == "Manual" else "reference"


def selection(db, data):
    versions = obj(data.get("versions"))
    if not 1 <= len(versions) <= 20000:
        raise ValueError("Selecciona al menos una obra.")
    works = []
    for identifier, version in versions.items():
        rows = list_works(db, identifier=identifier)
        if not rows or rows[0]["updated_at"] != version:
            abort(409, "Cambió la selección. Actualiza la biblioteca y vuelve a revisarla.")
        works.append(rows[0])
    return works




def folder_type(work):
    mapping = {"manga": "manga", "manhwa": "manhwa", "manhua": "manhua",
               "novel": "novel", "novela": "novel", "novelas": "novel"}
    hints = {mapping[name.casefold()] for path in work["bookmark_paths"] for name in path if name.casefold() in mapping}
    specific = hints - {"manga"}
    if len(specific) == 1:
        return next(iter(specific))
    if hints == {"manga"} and work["type"] == "other":
        return "manga"
    return work["type"]


def register_organization(app, get_db):
    @app.post("/api/batch/delete")
    def delete_selected():
        data = obj(request.get_json())
        if data.get("confirm") != "ELIMINAR":
            raise ValueError("Escribe ELIMINAR para confirmar.")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            works = selection(db, data)
            snapshot = verified_snapshot(app.config["DATABASE"], "antes-eliminar-lote")
            db.executemany("DELETE FROM works WHERE id=?", [(work["id"],) for work in works])
        return jsonify(count=len(works), backup=snapshot.name)

    @app.get("/api/review")
    def review():
        db = get_db()
        with db:
            db.execute("BEGIN")
            groups, dismissed = candidate_review(db)
            return jsonify(groups=groups, dismissed=[group for group, _ in dismissed])

    @app.post("/api/review/reject")
    def reject():
        signature = obj(request.get_json()).get("signature")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            groups, _ = candidate_review(db)
            group = next((group for group in groups if group["signature"] == signature), None)
            if group is None:
                abort(409, "La sugerencia cambió. Vuelve a abrir la revisión.")
            db.executemany("INSERT OR IGNORE INTO review_rejections VALUES(?)",
                           [(pair_key(*pair),) for pair in combinations(group["ids"], 2)])
        return "", 204

    @app.post("/api/review/reconsider")
    def reconsider():
        signature = obj(request.get_json()).get("signature")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            _, dismissed = candidate_review(db)
            keys = next((keys for group, keys in dismissed if group["signature"] == signature), None)
            if keys is None:
                abort(409, "La sugerencia cambió. Vuelve a abrir la revisión.")
            db.executemany("DELETE FROM review_rejections WHERE signature=?", [(key,) for key in keys])
        return "", 204

    @app.post("/api/capture")
    def capture():
        data = obj(request.get_json())
        url = web_url(data.get("url"))
        source = data.get("origin") or social_origin(url)
        title = clean(data.get("title", ""), 300, "El título")
        link = {"url": data["url"], "origin": source, "role": data.get("role") or link_role(url), "label": ""}
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM links WHERE url=?", (url,)).fetchone():
                abort(409, "Esta URL ya está guardada.")
            stamp = now()
            target = data.get("work_id")
            if target:
                target = clean(target, 36, "La obra")
                rows = list_works(db, identifier=target)
                if not rows:
                    abort(404)
                work = rows[0]
                previous = {entry["url"]: entry for entry in work["links"]}
                link["label"] = clean(title, 100, "El nombre de la fuente")
                work["links"].append(link)
                save_work(db, target, validate_work(work, previous), None, work["created_at"], stamp, editing=True)
            else:
                target = str(uuid.uuid4())
                work = validate_work({"title": title or urlsplit(url).hostname, "classified": False,
                                      "type": "other", "status": "planned", "chapter": "", "notes": "",
                                      "tags": [], "links": [link]})
                save_work(db, target, work, (None, None, None), stamp, stamp)
        return jsonify(id=target), 201

    @app.post("/api/batch")
    @app.post("/api/batch/preview")
    def batch():
        data = obj(request.get_json())
        operation, value = data.get("operation"), data.get("value")
        db = get_db()
        preview = request.path.endswith("/preview")
        changes = []
        if not preview and data.get("confirm") is not True:
            raise ValueError("Confirma los cambios del lote.")
        with db:
            db.execute("BEGIN" if preview else "BEGIN IMMEDIATE")
            works = selection(db, data)
            for work in works:
                old = {link["url"]: link for link in work["links"]}
                before = validate_work(work, old)
                if operation in ("classified", "type", "status"):
                    work[operation] = value
                elif operation in ("add_tag", "remove_tag"):
                    tag = clean(value, 60, "La etiqueta", required=True)
                    if operation == "add_tag":
                        if tag.casefold() not in {t.casefold() for t in work["tags"]}:
                            work["tags"].append(tag)
                    else:
                        work["tags"] = [t for t in work["tags"] if t.casefold() != tag.casefold()]
                elif operation == "folder_type":
                    work["type"] = folder_type(work)
                elif operation == "clean_title":
                    suggestion = title_suggestion(work["title"])
                    work["title"] = suggestion["title"]
                    if not work["chapter"]:
                        work["chapter"] = suggestion["chapter"]
                else:
                    raise ValueError("Operación de lote no válida.")
                after = validate_work(work, old)
                if after != before:
                    fields = {key: after[key] for key in after if before[key] != after[key]}
                    changes.append({"id": work["id"], "title": before["title"], "fields": fields})
                    if not preview:
                        columns = [key for key in ("type", "status", "classified", "title", "chapter", "tags") if key in fields]
                        assignments = ", ".join(f"{key}=?" for key in columns)
                        values = [json.dumps(fields[key]) if key == "tags" else fields[key] for key in columns]
                        db.execute(f"UPDATE works SET {assignments}, updated_at=? WHERE id=?",
                                   (*values, now(), work["id"]))
        return jsonify(count=len(changes), changes=changes) if preview else jsonify(count=len(changes))

    @app.post("/api/merge")
    @app.post("/api/merge/preview")
    def merge():
        data = obj(request.get_json())
        preview = request.path.endswith("/preview")
        if not preview and data.get("confirm") is not True:
            raise ValueError("Confirma la fusión.")
        db = get_db()
        with db:
            db.execute("BEGIN" if preview else "BEGIN IMMEDIATE")
            works = selection(db, data)
            if len(works) < 2:
                raise ValueError("Selecciona al menos dos obras distintas.")
            first = next((w for w in works if w["id"] == data.get("canonical", works[0]["id"])), None)
            if first is None:
                raise ValueError("Elige una obra de destino de la selección.")
            chosen = {}
            for key in ("type", "status", "chapter", "classified", "cover"):
                source = next((work for work in works if work["id"] == data.get(key)), None)
                if source is None:
                    raise ValueError("Elige qué datos conservar en cada campo.")
                chosen[key] = source[key]
            rating_id = data.get("rating")
            rating_source = next((work for work in works if work["id"] == rating_id), None)
            if rating_id is not None and rating_source is None:
                raise ValueError("Elige de qué ficha conservar la puntuación.")
            if rating_source is None:
                rating_source = first if first["rating"] is not None else next((w for w in works if w["rating"] is not None), first)
            chosen["rating"] = rating_source["rating"]
            links = [link for work in works for link in work["links"]]
            preferred = data.get("preferred", "")
            reading = [link["url"] for link in links if link["role"] == "reading"]
            if (reading and preferred not in reading) or (not reading and preferred):
                raise ValueError("Selecciona una fuente de lectura preferida.")
            links.sort(key=lambda link: link["url"] != preferred)
            paths = list(dict.fromkeys(tuple(path) for work in works for path in work["bookmark_paths"]))
            items(paths, 20000, "Las carpetas originales")
            notes = []
            for work in works:
                if work["notes"]:
                    notes.append(work["title"] + "\n" + work["notes"])
                elif work["title"] != data.get("title"):
                    notes.append("Título anterior · " + work["title"])
                if work["chapter"] and work["chapter"] != chosen["chapter"]:
                    notes.append(f"{work['title']} · Progreso anterior: {work['chapter']}")
            aliases = list(dict.fromkeys(alias for work in works for alias in [*work["aliases"], work["title"]]
                                         if alias != data.get("title") or alias in work["aliases"]))
            merged = validate_work({**chosen, "title": data.get("title"), "aliases": aliases,
                                    "tags": list(dict.fromkeys(tag for work in works for tag in work["tags"])),
                                    "notes": "\n\n".join(notes), "links": links},
                                   {link["url"]: link for link in links})
            others = [w for w in works if w["id"] != first["id"]]
            if preview:
                extra_count = sum(len(w["alternate_covers"]) + bool(w["cover"] and w["id"] != data["cover"]) for w in works)
                return jsonify(work={**merged, "cover": chosen["cover"], "bookmark_paths": paths}, alternate_count=extra_count)
            cover = db.execute("SELECT cover_url,cover_mime,cover_data FROM works WHERE id=?", (data["cover"],)).fetchone()
            for w in works:
                if w["cover"] and w["id"] != data["cover"]:
                    db.execute("""INSERT INTO alternate_covers(work_id,cover_url,cover_mime,cover_data)
                                  SELECT ?,cover_url,cover_mime,cover_data FROM works WHERE id=?""", (first["id"], w["id"]))
            for w in others:
                db.execute("UPDATE alternate_covers SET work_id=? WHERE work_id=?", (first["id"], w["id"]))
                db.execute("UPDATE mal_entries SET work_id=? WHERE work_id=?", (first["id"], w["id"]))
                db.execute("DELETE FROM works WHERE id=?", (w["id"],))
            save_work(db, first["id"], merged, tuple(cover), first["created_at"], now(), editing=True)
            db.execute("UPDATE works SET bookmark_paths=? WHERE id=?", (json.dumps(paths), first["id"]))
        return jsonify(id=first["id"])

