"""Migrate the local library under a write lock after verifying its snapshot."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3

from library import SCHEMA


def verify_original_rows(original, current):
    def quote(name):
        return '"' + name.replace('"', '""') + '"'

    for (table,) in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        columns = [row[1] for row in original.execute(f"PRAGMA table_info({quote(table)})")]
        projection = ", ".join(map(quote, columns))
        query = f"SELECT {projection} FROM {quote(table)} ORDER BY rowid"
        for left, right in zip(original.execute(query), current.execute(query), strict=True):
            if tuple(left) != tuple(right):
                raise ValueError("Los datos originales difieren de la copia de seguridad.")


def verify_integrity(db):
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("La comprobación de integridad SQLite falló.")


def verified_snapshot(filename, purpose):
    """Called before the first mutation while the caller holds BEGIN IMMEDIATE."""
    path = Path(filename).resolve()
    directory = path.parent / "backups"
    directory.mkdir(exist_ok=True)
    snapshot = directory / f"{path.stem}-{purpose}-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3"
    temporary = snapshot.with_suffix(".tmp")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(temporary)) as copy:
            source.backup(copy)
            verify_integrity(copy)
            verify_original_rows(source, copy)
        temporary.replace(snapshot)
    finally:
        temporary.unlink(missing_ok=True)
    return snapshot


def initialize(db, filename):
    with db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 4:
            return
        if version not in (0, 2, 3):
            raise ValueError("Versión de base de datos incompatible.")
        existing = db.execute("SELECT 1 FROM sqlite_master WHERE name='works'").fetchone()
        if existing:
            snapshot = verified_snapshot(filename, "pre-v0.5" if version == 3 else f"v0.{version or 1}")
        else:
            for sql in SCHEMA.split(";"):
                if sql.strip():
                    db.execute(sql)
        if version == 0:
            migrate_v1(db)
        if version < 3:
            migrate_v2(db)
        db.execute("ALTER TABLE works ADD COLUMN rating INTEGER CHECK(rating IS NULL OR (typeof(rating)='integer' AND rating BETWEEN 0 AND 10))")
        db.execute("""CREATE TABLE mal_entries (
                      mal_id INTEGER PRIMARY KEY CHECK(mal_id > 0),
                      work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE)""")
        db.execute("CREATE INDEX mal_entries_work ON mal_entries(work_id)")
        if existing:
            with closing(sqlite3.connect(snapshot)) as copy:
                verify_original_rows(copy, db)
            verify_integrity(db)
        db.execute("PRAGMA user_version=4")


def migrate_v2(db):
    db.execute("ALTER TABLE works ADD COLUMN aliases TEXT NOT NULL DEFAULT '[]'")
    db.execute("""ALTER TABLE links ADD COLUMN health TEXT NOT NULL DEFAULT 'unchecked'
                      CHECK(health IN ('unchecked','working','broken','suspicious'))""")
    db.execute("CREATE TABLE review_rejections (signature TEXT PRIMARY KEY)")
    db.execute("""CREATE TABLE alternate_covers (
                      id INTEGER PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                      cover_url TEXT, cover_mime TEXT, cover_data BLOB)""")
    db.execute("CREATE INDEX alternate_cover_work ON alternate_covers(work_id)")


def migrate_v1(db):
    db.execute("ALTER TABLE works ADD COLUMN classified INTEGER NOT NULL DEFAULT 1 CHECK(classified IN (0,1))")
    db.execute("UPDATE works SET classified=0 WHERE bookmark_paths!='[]'")
    for column, default in [("original_url", ""), ("original_titles", "[]"), ("origin", "Manual"),
                            ("language", ""), ("role", "reading")]:
        db.execute(f"ALTER TABLE links ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
    db.execute("UPDATE links SET original_url=url, origin=CASE WHEN paths='[]' THEN 'Manual' ELSE 'Edge' END")
    db.execute("""UPDATE links SET original_titles=json_array((SELECT title FROM works WHERE id=work_id))
                  WHERE paths!='[]' AND work_id IN
                  (SELECT id FROM works WHERE created_at=updated_at)
                  AND work_id IN (SELECT work_id FROM links GROUP BY work_id HAVING count(*)=1)""")

