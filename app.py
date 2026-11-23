"""JMangaShelf, a loopback-only Flask application."""

import json
import sqlite3
import uuid
from pathlib import Path

from flask import Flask, Response, abort, current_app, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from anilist import AniList, AniListFailure
from bookmarks import parse_bookmarks, title_key, title_suggestion, web_url
from library import (
    clean, connect, decode_cover, folder_path, items, list_works,
    now, obj, save_work, validate_backup, validate_work,
)

from version import VERSION
from migration import initialize
from mal_import import register_mal_import
from organization import link_role, register_organization

ROOT = Path(__file__).resolve().parent
MAX_BOOKMARKS = 16 * 1024 * 1024


def create_app(database=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=str(database or ROOT / "data" / "library.sqlite3"),
        MAX_CONTENT_LENGTH=128 * 1024 * 1024,
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
    )
    data_dir = Path(app.config["DATABASE"]).resolve().parent
    data_dir.mkdir(parents=True, exist_ok=True)
    app.extensions["anilist"] = AniList(data_dir / "anilist-covers")
    @app.teardown_appcontext
    def close_db(_error):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        initialize(get_db(), app.config["DATABASE"])
    register_organization(app, get_db)
    register_mal_import(app, get_db)

    @app.before_request
    def local_requests():
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if request.headers.get("X-JMangaShelf") != "1" or (origin and origin != request.host_url.rstrip("/")):
                abort(403, "Abre JMangaShelf desde su dirección local.")

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: http: https:; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(AniListFailure)
    def anilist_error(error):
        response = jsonify(error=str(error), retry_after=error.retry_after)
        if error.retry_after is not None:
            response.headers["Retry-After"] = str(error.retry_after)
        return response, error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = {400: "La solicitud no contiene datos válidos.", 404: "No se encontró el elemento.",
                   405: "Esta operación no está disponible.", 413: "El archivo supera el tamaño permitido."}
        return jsonify(error=message.get(error.code, error.description)), error.code

    @app.errorhandler(sqlite3.IntegrityError)
    def duplicate_link(error):
        app.logger.info("SQLite rejected a conflicting change: %s", error)
        return jsonify(error="Uno de los enlaces ya pertenece a otra obra."), 409

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        app.logger.error("SQLite operation failed: %s", error)
        return jsonify(error="No se pudo acceder a la biblioteca. Comprueba el espacio en disco y vuelve a intentarlo."), 503

    @app.get("/")
    def index():
        return render_template("index.html", version=VERSION)

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("icons/JMangaShelf.ico")

    @app.get("/api/health")
    def health():
        return jsonify(application="JMangaShelf")

    @app.get("/api/works")
    def works():
        db = get_db()
        with db:
            db.execute("BEGIN")
            return jsonify(list_works(db))

    @app.post("/api/works")
    @app.put("/api/works/<uuid:work_id>")
    def edit_work(work_id=None):
        payload = obj(request.get_json())
        db = get_db()
        cover_payload = payload.get("cover")
        if isinstance(cover_payload, dict) and cover_payload.get("kind") == "anilist":
            if cover_payload.get("confirm") is not True:
                raise ValueError("Confirma la portada seleccionada.")
            if work_id:
                check_current(db, work_id, payload)
            cover = app.extensions["anilist"].cover(cover_payload.get("token"))
        else:
            cover = decode_cover(cover_payload) if "cover" in payload else None
        with db:
            db.execute("BEGIN IMMEDIATE")
            stamp = now()
            identifier = str(work_id or uuid.uuid4())
            previous = list_works(db, identifier=identifier) if work_id else []
            if work_id and not previous:
                abort(404)
            if previous and payload.get("updated_at", previous[0]["updated_at"]) != previous[0]["updated_at"]:
                abort(409, "La obra cambió desde que la abriste. Vuelve a abrirla para editar.")
            old_links = {link["url"]: link for link in previous[0]["links"]} if previous else {}
            if previous:
                payload.setdefault("classified", previous[0]["classified"])
                payload.setdefault("aliases", previous[0]["aliases"])
                payload.setdefault("rating", previous[0]["rating"])
            work = validate_work(payload, old_links)
            if previous:
                save_work(db, identifier, work, cover, previous[0]["created_at"], stamp, editing=True)
            else:
                save_work(db, identifier, work, cover or (None, None, None), stamp, stamp)
            return jsonify(list_works(db, identifier=identifier)[0]), 200 if work_id else 201

    @app.post("/api/anilist/search")
    def search_anilist():
        payload = obj(request.get_json())
        query = clean(payload.get("query"), 300, "El título de búsqueda", required=True)
        work_id = payload.get("work_id")
        work = None
        if work_id is not None:
            identifier = clean(work_id, 36, "La obra", required=True)
            rows = list_works(get_db(), identifier=identifier)
            if not rows:
                abort(404)
            work = rows[0]
        return jsonify(app.extensions["anilist"].search(query, work))

    @app.patch("/api/works/<uuid:work_id>/cover")
    def select_anilist_cover(work_id):
        payload = obj(request.get_json())
        if payload.get("confirm") is not True:
            raise ValueError("Confirma la portada seleccionada.")
        db = get_db()
        check_current(db, work_id, payload, missing_cover=True)
        image = app.extensions["anilist"].cover(payload.get("token"))
        with db:
            db.execute("BEGIN IMMEDIATE")
            check_current(db, work_id, payload, missing_cover=True)
            db.execute("UPDATE works SET cover_url=?, cover_mime=?, cover_data=?, updated_at=? WHERE id=?",
                       (*image, now(), str(work_id)))
            return jsonify(list_works(db, identifier=str(work_id))[0])

    @app.patch("/api/works/<uuid:work_id>/chapter")
    def update_chapter(work_id):
        payload = obj(request.get_json())
        chapter = clean(payload.get("chapter"), 80, "El capítulo")
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = check_current(db, work_id, payload)
            if row["chapter"] != chapter:
                db.execute("UPDATE works SET chapter=?, updated_at=? WHERE id=?", (chapter, now(), str(work_id)))
            return jsonify(list_works(db, identifier=str(work_id))[0])

    @app.delete("/api/works/<uuid:work_id>")
    def delete_work(work_id):
        db = get_db()
        with db:
            if not db.execute("DELETE FROM works WHERE id=?", (str(work_id),)).rowcount:
                abort(404)
        return "", 204

    @app.get("/api/works/<uuid:work_id>/cover")
    @app.get("/api/works/<uuid:work_id>/covers/<int:cover_id>")
    def cover(work_id, cover_id=None):
        if cover_id is None:
            row = get_db().execute("SELECT cover_mime,cover_data FROM works WHERE id=?", (str(work_id),)).fetchone()
        else:
            row = get_db().execute("SELECT cover_mime,cover_data FROM alternate_covers WHERE work_id=? AND id=?",
                                   (str(work_id), cover_id)).fetchone()
        if row is None or row["cover_data"] is None:
            abort(404)
        return Response(row["cover_data"], mimetype=row["cover_mime"])

    @app.post("/api/bookmarks/preview")
    def preview_bookmarks():
        file = request.files.get("file")
        if file is None:
            raise ValueError("Selecciona un archivo HTML.")
        raw = file.read(MAX_BOOKMARKS + 1)
        if len(raw) > MAX_BOOKMARKS:
            abort(413)
        rows, ignored = parse_bookmarks(raw)
        urls = {known for row in get_db().execute("SELECT url,original_url FROM links")
                for known in (row["url"], web_url(row["original_url"]))}
        titles = {title_key(title_suggestion(row["title"])["title"]) for row in get_db().execute("SELECT title FROM works")}
        by_title = {}
        for row in rows:
            by_title.setdefault(title_key(title_suggestion(row["title"])["title"]), set()).add(row["url"])
        seen = set()
        for row in rows:
            key = title_key(title_suggestion(row["title"])["title"])
            url = row["url"]
            row["existing"] = url in urls
            row["repeated"] = url in seen
            row["similar"] = bool(key and (key in titles or len(by_title[key]) > 1) and not row["existing"])
            seen.add(url)
        return jsonify(rows=rows, ignored=ignored)

    @app.post("/api/bookmarks/import")
    def import_bookmarks():
        data = obj(request.get_json())
        grouped = {}
        for entry in items(data.get("rows"), 20000, "Los marcadores"):
            entry = obj(entry)
            url = web_url(entry.get("url"))
            title = clean(entry.get("title"), 300, "El título", required=True)
            path = tuple(folder_path(entry.get("path")))
            original = entry.get("original_url", entry["url"])
            if web_url(original) != url:
                raise ValueError("La URL original no coincide.")
            group = grouped.setdefault(url, {"title": title, "original": original, "titles": {}, "paths": {}})
            original_title = clean(entry.get("original_title", title), 8192, "El título original", multiline=True)
            if original_title:
                group["titles"][original_title] = None
            group["paths"][path] = None
        kind, status = data.get("type"), data.get("status")
        defaults = validate_work({"title": "Marcador", "type": kind, "status": status,
                                  "chapter": "", "notes": "", "tags": [], "links": []})
        created = enriched = existing = 0
        db = get_db()
        with db:
            db.execute("BEGIN IMMEDIATE")
            stamp = now()
            aliases = {}
            for row in db.execute("SELECT url,original_url FROM links WHERE original_url!=url"):
                aliases.setdefault(web_url(row["original_url"]), set()).add(row["url"])
            current_urls = {r["url"] for r in db.execute("SELECT url FROM links")}
            for url, group in grouped.items():
                if url not in current_urls and url in aliases:
                    matches = aliases[url]
                    if len(matches) != 1:
                        raise ValueError("Una URL original corresponde a varias fuentes editadas. Exclúyela y revisa esas obras.")
                    url = next(iter(matches))
                paths = list(group["paths"])
                linked = db.execute(
                    "SELECT work_id, paths, bookmark_paths, original_titles FROM links JOIN works ON id=work_id WHERE url=?", (url,)
                ).fetchone()
                if linked:
                    previous = [tuple(path) for path in json.loads(linked["paths"])]
                    merged = list(dict.fromkeys(previous + paths))
                    old_titles = json.loads(linked["original_titles"])
                    titles = list(dict.fromkeys([*old_titles, *group["titles"]]))
                    if len(titles) > 20000:
                        raise ValueError("Demasiados títulos para un mismo enlace.")
                    if merged != previous or titles != old_titles:
                        work_paths = list(dict.fromkeys(
                            [tuple(path) for path in json.loads(linked["bookmark_paths"])] + paths
                        ))
                        if len(merged) > 20000 or len(work_paths) > 20000:
                            raise ValueError("Un enlace supera el límite de 20 000 carpetas originales.")
                        db.execute("UPDATE links SET paths=?, original_titles=? WHERE url=?", (json.dumps(merged), json.dumps(titles), url))
                        db.execute("UPDATE works SET updated_at=?, bookmark_paths=? WHERE id=?",
                                   (stamp, json.dumps(work_paths), linked["work_id"]))
                        enriched += 1
                    else:
                        existing += 1
                    continue
                work = {**defaults, "classified": False, "title": group["title"], "bookmark_paths": paths,
                        "links": [{"url": url, "paths": paths, "label": "", "original_url": group["original"],
                                   "original_titles": list(group["titles"]), "role": link_role(url), "language": "", "origin": "Navegador"}]}
                save_work(db, str(uuid.uuid4()), work, (None, None, None), stamp, stamp)
                created += 1
        return jsonify(created=created, enriched=enriched, existing=existing)

    @app.get("/api/backup")
    def export_backup():
        db = get_db()
        with db:
            db.execute("BEGIN")
            rejected = [row[0] for row in db.execute("SELECT signature FROM review_rejections ORDER BY signature")]
            response = jsonify(format="JMangaShelf", version=4, works=list_works(db, backup=True), review_rejections=rejected)
        response.headers["Content-Disposition"] = f'attachment; filename="JMangaShelf-{now()[:10]}.json"'
        return response

    @app.post("/api/backup/restore")
    def restore_backup():
        mode = request.form.get("mode")
        if mode not in ("merge", "replace") or (mode == "replace" and request.form.get("confirm") != "REEMPLAZAR"):
            raise ValueError("Confirma el modo de restauración.")
        file = request.files.get("file")
        if file is None:
            raise ValueError("Selecciona una copia JSON.")
        try:
            backup = json.load(file.stream)
        except (ValueError, RecursionError):
            raise ValueError("El archivo no contiene un JSON válido.") from None
        works_to_restore = validate_backup(backup)
        db = get_db()
        added = skipped = 0
        with db:
            db.execute("BEGIN IMMEDIATE")
            if mode == "replace":
                db.execute("DELETE FROM works")
                db.execute("DELETE FROM review_rejections")
            ids = {row["id"] for row in db.execute("SELECT id FROM works")}
            urls = {row["url"] for row in db.execute("SELECT url FROM links")}
            mal_ids = {row["mal_id"] for row in db.execute("SELECT mal_id FROM mal_entries")}
            for identifier, work, cover_data, created, updated in works_to_restore:
                if identifier in ids or mal_ids.intersection(work["mal_ids"]) or any(link["url"] in urls for link in work["links"]):
                    skipped += 1
                    continue
                save_work(db, identifier, work, cover_data, created, updated)
                added += 1
            db.executemany("INSERT OR IGNORE INTO review_rejections VALUES(?)",
                           [(signature,) for signature in backup.get("review_rejections", [])])
        return jsonify(added=added, skipped=skipped)

    return app


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def check_current(db, work_id, payload, *, missing_cover=False):
    row = db.execute("SELECT updated_at, chapter, (cover_url IS NOT NULL OR cover_data IS NOT NULL) AS has_cover "
                     "FROM works WHERE id=?", (str(work_id),)).fetchone()
    if row is None:
        abort(404)
    if row["updated_at"] != payload.get("updated_at"):
        abort(409, "La obra cambió. Vuelve a abrirla para revisar los datos actuales.")
    if missing_cover and row["has_cover"]:
        abort(409, "Esta obra ya tiene portada. Puedes reemplazarla desde el editor.")
    return row


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8765, debug=False)
