import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app import create_app
from bookmarks import title_suggestion
from library import SCHEMA
from test_library import ApiCase, LOCAL, image_cover


class OrganizationTest(ApiCase):
    def post(self, path, **data):
        return self.client.post("/api/" + path, json=data, headers=LOCAL)

    def batch(self, works, operation, value=None):
        return self.post("batch", versions={w["id"]: w["updated_at"] for w in works}, operation=operation, value=value, confirm=True)

    def test_migration_snapshot_and_legacy_values(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.executescript(SCHEMA)
            db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("old", "A", "novel", "reading", "9", "Notes", '["tag"]', '[["Edge"]]',
                        None, "image/png", b"original bytes", "2021", "2021"))
            db.execute("INSERT INTO links VALUES(?,?,?,?,?)", ("old", 0, "Alt", "https://a.example/", '[["Edge"]]'))
            db.commit()
            before = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in ("works","links")}
        create_app(path)
        create_app(path)
        backups = list(path.parent.glob("backups/legacy*.sqlite3"))
        self.assertEqual(len(backups), 1)
        for filename in [backups[0], path]:
            with closing(sqlite3.connect(filename)) as db:
                self.assertEqual(db.execute("pragma integrity_check").fetchone(), ("ok",))
                for table, rows in before.items():
                    self.assertEqual([row[:len(rows[0])] for row in db.execute(f"SELECT * FROM {table}")], rows)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT classified FROM works").fetchone(), (0,))
            self.assertEqual(db.execute("SELECT origin,original_titles FROM links").fetchone(), ("Edge",'["A"]'))

    def test_clean_titles_and_guard_sequels(self):
        for raw, title, chapter in [
            ("Leer One Piece Capítulo 1156 en Español - Lector Manga", "One Piece", "1156"),
            ("Read My Cuteness Isn't Understood By Kuroiwa Medaka - manga Online in English",
             "My Cuteness Isn't Understood By Kuroiwa Medaka", ""),
            ("Mato Seihei no Slave Capítulo 179", "Mato Seihei no Slave", "179"),
            ("Read or Die Vol. 2 Capítulo 10", "Read or Die Vol. 2", "10"),
            ("Torre 2: temporada 3", "Torre 2: temporada 3", ""),
        ]:
            self.assertEqual(title_suggestion(raw), {"title": title, "chapter": chapter})

    def test_source_metadata_preference_reimport_and_cleanup(self):
        raw = "Leer Torre Capítulo 11 en Español - Lector Manga"
        row = {"url":"HTTPS://A.example:443/Serie", "path":["Edge","Torre"], "title":raw}
        self.import_rows([row])
        w = self.works()[0]
        self.assertFalse(w["classified"])
        self.assertEqual(self.batch([w], "clean_title").status_code, 200)
        w = self.works()[0]
        self.assertEqual((w["title"],w["chapter"]), ("Torre","11"))
        self.assertEqual(w["links"][0]["original_url"], row["url"])
        self.assertEqual(w["links"][0]["original_titles"], [raw])
        w["links"].insert(0, {"url":"https://b.example/1","label":"Edición ES","language":"es","origin":"WhatsApp"})
        result = self.client.put("/api/works/" + w["id"], json=w, headers=LOCAL)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.import_rows([row])["existing"], 1)
        w = self.works()[0]
        self.assertEqual(w["links"][0]["website"], "b.example")
        self.assertEqual(w["links"][1]["paths"], [row["path"]])
        self.assertEqual(w["title"], "Torre")
        w["links"][1] = {"previous_url": w["links"][1]["url"], "url": "https://a.example/new", "label": "Nueva URL"}
        result = self.client.put("/api/works/" + w["id"], json=w, headers=LOCAL)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.import_rows([row])["existing"], 1)
        w = self.works()[0]
        self.assertEqual(w["links"][1]["original_url"], row["url"])
        self.assertEqual(w["links"][1]["paths"], [row["path"]])
        backup = self.client.get("/api/backup").get_json()
        self.assertEqual(self.restore(backup,"replace","REEMPLAZAR").status_code, 200)
        self.assertEqual(self.works()[0], w)

    def test_capture_social_reference_and_existing_target(self):
        saved = self.post("capture", url="https://x.com/example/status/123")
        self.assertEqual(saved.status_code, 201)
        target = saved.get_json()["id"]
        w = self.works()[0]
        self.assertFalse(w["classified"])
        self.assertEqual((w["links"][0]["role"],w["links"][0]["origin"]), ("reference","X"))
        self.assertEqual(self.post("capture", url="https://reader.example", work_id=target, origin="WhatsApp").status_code, 201)
        self.assertEqual(self.post("capture", url="HTTPS://READER.example:443/").status_code, 409)
        self.assertEqual([link["role"] for link in self.works()[0]["links"]], ["reading","reference"])

    def test_batch_counts_status_independence_and_stale_selection(self):
        self.save()
        self.save(title="B", links=[])
        before = self.works()
        self.assertEqual(self.batch(before,"classified",False).get_json(), {"count":2})
        self.assertTrue(all(not w["classified"] and w["status"]=="reading" for w in self.works()))
        self.assertEqual(self.batch(before,"status","dropped").status_code,409)
        for operation, value in [("status","on_hold"),("type","novel"),("add_tag","grupo"),("remove_tag","relectura")]:
            self.assertEqual(self.batch(self.works(),operation,value).status_code,200)
        for w in self.works():
            self.assertEqual((w["type"],w["status"],w["tags"]), ("novel","on_hold",["Fantasía","grupo"]))

    def test_review_and_transactional_merge(self):
        rows = [{"title":"Torre Capítulo 10","url":"https://a.example/10","path":["Torre"]},
                {"title":"Torre Capítulo 20","url":"https://b.example/20","path":["Pendientes"]},
                {"title":"Torre 2","url":"https://a.example/sequel","path":["Torre"]}]
        self.import_rows(rows)
        groups = self.client.get("/api/review").get_json()["groups"]
        self.assertEqual(len(groups),1)
        first, second = [w for w in self.works() if w["id"] in groups[0]["ids"]]
        def edit(work, **changes):
            return self.client.put("/api/works/"+work["id"], headers=LOCAL,
                                   json={**work, "cover":image_cover(), **changes}).get_json()
        first = edit(first, chapter="7", notes="Una nota",tags=["azul"])
        second = edit(second, chapter="19",notes="Otra nota",tags=["rojo"])
        args = dict(versions={w["id"]:w["updated_at"] for w in (first,second)}, title="Mi torre",
                    type=first["id"], classified=first["id"], chapter=second["id"], status=first["id"],
                    cover=second["id"], preferred=second["links"][0]["url"], confirm=True)
        before = self.client.get("/api/backup").get_json()
        self.assertEqual(self.post("merge", **{**args,"preferred":"bad"}).status_code,400)
        self.assertEqual(self.client.get("/api/backup").get_json(),before)
        # Force failure after the secondary row and links have been removed.
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TRIGGER simulate_failure BEFORE UPDATE ON works BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertEqual(self.post("merge", **args).status_code, 409)
        self.assertEqual(self.client.get("/api/backup").get_json(), before)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP TRIGGER simulate_failure")
        result = self.post("merge", **args)
        self.assertEqual(result.status_code,200,result.get_json())
        merged = next(w for w in self.works() if w["title"]=="Mi torre")
        self.assertEqual(len(self.works()),2)
        self.assertEqual(merged["links"][0]["url"],args["preferred"])
        self.assertEqual(len(merged["bookmark_paths"]),2)
        self.assertEqual(set(merged["tags"]),{"azul","rojo"})
        self.assertEqual(merged["chapter"],"19")
        for value in ["7","Una nota","Otra nota"]:
            self.assertIn(value,merged["notes"])
        self.assertEqual(self.client.get(merged["cover"]["url"]).status_code,200)
        self.assertEqual(self.import_rows(rows)["created"],0)

    def test_v1_backup_and_unknown_data_rejection(self):
        self.import_rows(self.preview()["rows"])
        backup = self.client.get("/api/backup").get_json()
        backup["version"] = 1
        del backup["review_rejections"]
        for w in backup["works"]:
            del w["rating"], w["mal_ids"]
            del w["aliases"], w["alternate_covers"]
            del w["classified"]
            w["links"] = [{key:link[key] for key in ("url","paths","label")} for link in w["links"]]
        self.assertEqual(self.restore(backup,"replace","REEMPLAZAR").status_code,200)
        self.assertTrue(all(not w["classified"] and w["links"][0]["origin"]=="Edge" for w in self.works()))
        backup["works"][0]["unknown_field"] = 42
        self.assertEqual(self.restore(backup,"replace","REEMPLAZAR").status_code,400)

