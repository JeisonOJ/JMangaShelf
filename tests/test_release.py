import gzip
import io
import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from threading import Event, Thread
from unittest.mock import Mock, patch

from werkzeug.serving import make_server

from app import create_app
from launcher import launch, open_when_ready
from mal_import import parse_mal_export
from migration import verify_integrity, verify_original_rows
from test_library import ApiCase, LOCAL, image_cover

MAL = Path(__file__).parent.joinpath("fixtures", "mal.xml").read_bytes()


class ExportTest(unittest.TestCase):
    def test_mal_xml_and_gzip_preserve_verified_fields(self):
        rows = parse_mal_export(MAL)
        self.assertEqual(rows, parse_mal_export(gzip.compress(MAL)))
        self.assertEqual(rows[0], {"row":1, "id":101, "title":"Torre & luna", "rating":8, "status":"completed",
                                   "chapter":"32", "started":"2024-03-01", "finished":"2024-03-04"})
        self.assertEqual((rows[1]["rating"], rows[1]["chapter"], rows[1]["started"]), (None,"",None))

    def test_refuses_anime_xml_dtd_corrupt_gzip_and_unbounded_payload(self):
        for raw in (b"<myanimelist><anime/></myanimelist>", b"\x1f\x8b",
                    b'<!DOCTYPE x [<!ENTITY x "target">]><myanimelist/>'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_mal_export(raw)
        with patch("mal_import.MAX_EXPORT", 1024), self.assertRaisesRegex(ValueError, "descomprimido"):
            parse_mal_export(gzip.compress(b" "*1025))


class ReleaseTest(ApiCase):
    def backup(self):
        return self.client.get("/api/backup").get_json()

    def snapshot(self, name):
        return closing(sqlite3.connect(self.database.parent / "backups" / name))

    def preview(self, raw=MAL):
        response = self.client.post("/api/mal/preview", headers=LOCAL, data={"file":(io.BytesIO(raw), "mal.xml")})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def apply_mal(self, preview, choices, confirm="1", raw=MAL):
        return self.client.post("/api/mal/import", headers=LOCAL,
            data={"file":(io.BytesIO(raw),"mal.xml"), "version":preview["version"], "confirm":confirm,
                  "choices":json.dumps(choices)})

    def test_ratings_persist_roundtrip_and_validate(self):
        for rating in [None, *range(11)]:
            self.save(title=f"Obra {rating}", rating=rating, links=[])
        before = self.backup()
        self.assertEqual(create_app(self.database).test_client().get("/api/backup").get_json(), before)
        self.assertCountEqual([w["rating"] for w in before["works"]], [None, *range(11)])
        zero = next(w for w in self.works() if w["rating"] == 0)
        path = "/api/works/" + zero["id"]
        for bad in (-1, 11, "4", 2.5, False):
            self.assertEqual(self.client.put(path, headers=LOCAL,json={**zero,"rating":bad}).status_code,400)
        self.assertIsNone(self.client.put(path, headers=LOCAL,json={**zero,"rating":None}).get_json()["rating"])
        self.assertEqual(self.restore(before, "replace", "REEMPLAZAR").status_code,200)
        self.assertEqual(self.backup(), before)
        legacy = self.backup()
        legacy["version"] = 3
        for work in legacy["works"]:
            del work["rating"], work["mal_ids"]
        self.assertEqual(self.restore(legacy, "replace","REEMPLAZAR").status_code,200)
        self.assertTrue(all(w["rating"] is None and not w["mal_ids"] for w in self.works()))

    def test_mal_review_conflicts_reimport_and_backup(self):
        a = self.save(title="Torre local", rating=0, cover=image_cover(), aliases=["Torre & luna"],
                      links=[{"label":"Principal","url":"https://reader.example/tower"},
                             {"label":"Catálogo","url":"https://myanimelist.net/manga/101/Tower","role":"reference"}])
        self.save(title="La viajera", type="manga", links=[])
        self.save(title="La viajera", type="novel", links=[])
        before = self.backup()
        preview = self.preview()
        self.assertEqual([row["category"] for row in preview["rows"]], ["strong","ambiguous","new"])
        self.assertEqual(len(preview["rows"][1]["matches"]),2)
        self.assertEqual(self.backup(), before)
        decisions = {"1":{"action":"link","target":a["id"]}, "3":{"action":"create"}}
        self.assertEqual(self.apply_mal(preview, decisions, confirm="").status_code,400)
        result = self.apply_mal(preview, decisions)
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual((result.get_json()["linked"],result.get_json()["created"]), (1,1))
        with self.snapshot(result.get_json()["backup"]) as db:
            verify_integrity(db)
            self.assertEqual(db.execute("select count(*) from works").fetchone()[0],3)
            self.assertEqual(db.execute("select count(*) from mal_entries").fetchone()[0],0)
        current = next(w for w in self.works() if w["id"] == a["id"])
        self.assertEqual(current["mal_ids"],[101])
        for key in ("title","rating","chapter","status","links","tags","notes","aliases"):
            self.assertEqual(a[key], current[key])
        repeated = self.preview()
        self.assertEqual(repeated["rows"][2]["category"], "strong")
        decisions["3"] = {"action":"link","target":repeated["rows"][2]["target"]}
        self.assertEqual(self.apply_mal(repeated, decisions).get_json(),{"created":0,"linked":0,"ignored":3,"backup":None})
        decisions["1"].update(rating=True, chapter=True)
        self.assertEqual(self.apply_mal(repeated, decisions).get_json()["linked"],1)
        changed = next(w for w in self.works() if w["id"] == a["id"])
        self.assertEqual((changed["rating"],changed["chapter"],changed["status"]),(8,"32",a["status"]))
        export = self.backup()
        self.assertEqual(self.restore(export,"replace","REEMPLAZAR").status_code,200)
        self.assertEqual(self.backup(),export)

    def test_mal_skips_repeated_ids_stale_plan_and_transaction_failure(self):
        repeated = MAL.replace(b"</myanimelist>", MAL[MAL.index(b"<manga>"):MAL.index(b"</manga>")+8] + b"</myanimelist>")
        self.assertEqual([r["category"] for r in self.preview(repeated)["rows"]],["skipped","new","new","skipped"])
        preview = self.preview()
        self.save()
        decisions = {"1":{"action":"create"}, "2":{"action":"create"}}
        self.assertEqual(self.apply_mal(preview, decisions).status_code,409)
        preview = self.preview()
        before = self.backup()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TRIGGER fail_mal BEFORE INSERT ON mal_entries WHEN new.mal_id=102 BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertEqual(self.apply_mal(preview, decisions).status_code,409)
        self.assertEqual(self.backup(), before)

    def test_bulk_delete_checks_confirmation_and_rolls_back(self):
        a = self.save()
        b = self.save(title="Quitar B", links=[{"url":"https://different.example/book","label":""}])
        c = self.save(title="Conservar C", links=[{"url":"https://keep.example/book","label":""}], rating=0)
        versions = {w["id"]:w["updated_at"] for w in (a,b)}
        path = "/api/batch/delete"
        self.assertEqual(self.client.post(path, headers=LOCAL,json={"versions":versions}).status_code,400)
        before = self.backup()
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(f"""CREATE TRIGGER fail_del BEFORE DELETE ON works WHEN old.id='{b['id']}'
                          BEGIN SELECT RAISE(ABORT,'test'); END""")
        self.assertEqual(self.client.post(path, headers=LOCAL,json={"versions":versions,"confirm":"ELIMINAR"}).status_code,409)
        self.assertEqual(self.backup(), before)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP TRIGGER fail_del")
        result = self.client.post(path, headers=LOCAL,json={"versions":versions,"confirm":"ELIMINAR"})
        self.assertEqual(result.get_json()["count"],2)
        self.assertEqual(self.works(),[c])
        with self.snapshot(result.get_json()["backup"]) as db:
            verify_integrity(db)
            self.assertEqual(db.execute("select count(*) from links").fetchone()[0],4)

    def test_v3_migration_snapshot_rollback_and_retry(self):
        self.save(cover=image_cover())
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript("""DROP TABLE mal_entries; ALTER TABLE works DROP COLUMN rating;
                               PRAGMA user_version=3;""")
        with patch("migration.verify_integrity", side_effect=[None, ValueError("simulated")]):
            with self.assertRaisesRegex(ValueError,"simulated"):
                create_app(self.database)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("pragma user_version").fetchone(), (3,))
            self.assertNotIn("rating", [r[1] for r in db.execute("pragma table_info(works)")])
        create_app(self.database)
        snapshots = list((self.database.parent / "backups").glob("*pre-v0.5*"))
        with self.snapshot(snapshots[-1].name) as old, closing(sqlite3.connect(self.database)) as current:
            verify_original_rows(old,current)
            self.assertEqual(current.execute("pragma user_version").fetchone(),(4,))
        create_app(self.database)
        self.assertEqual(len(list((self.database.parent / "backups").glob("*pre-v0.5*"))),2)

    def test_launcher_http_readiness_and_reuse(self):
        app = create_app(self.database)
        opened = Mock(return_value=True)
        with make_server("127.0.0.1", 0, app) as server:
            bg = Thread(target=server.serve_forever, daemon=True)
            bg.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/"
                self.assertTrue(open_when_ready(url, opened))
                factory = Mock()
                self.assertEqual(launch(server.server_port, factory, opened),0)
                factory.assert_not_called()
                self.assertEqual([call.args for call in opened.call_args_list],[(url,),(url,)])
            finally:
                server.shutdown()
                bg.join()

    def test_launcher_starts_one_local_server_and_reports_failure(self):
        app = create_app(self.database)
        opened = Event()
        servers, urls, results = [], [], []

        def bind(host, port, application, **options):
            server = make_server(host, port, application, **options)
            servers.append(server)
            return server

        def browser(url):
            urls.append(url)
            opened.set()
            return True

        with patch("launcher.make_server", side_effect=bind) as start:
            bg = Thread(target=lambda: results.append(launch(0, lambda:app, browser)), daemon=True)
            bg.start()
            try:
                self.assertTrue(opened.wait(5))
                start.assert_called_once_with("127.0.0.1",0,app,threaded=True)
                self.assertEqual(urls,[f"http://127.0.0.1:{servers[0].server_port}/"])
            finally:
                if servers:
                    servers[0].shutdown()
                bg.join(timeout=5)
            self.assertEqual(results,[0])
        with patch("launcher.occupied", return_value=True), patch("launcher.open_when_ready", return_value=False):
            app_factory = Mock()
            self.assertEqual(launch(8765, app_factory, Mock()),1)
            app_factory.assert_not_called()
