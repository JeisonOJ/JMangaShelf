import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import create_app
from bookmarks import parse_bookmarks, web_url

FIXTURE = Path(__file__).parent / "fixtures" / "edge.html"
LOCAL = {"X-JMangaShelf": "1"}


def sample(**changes):
    return {
        "title": "La torre", "type": "manhwa", "status": "reading", "chapter": "24.5",
        "notes": "Volver al prólogo.\nRevisar traducción.", "tags": ["Fantasía", "relectura"],
        "links": [{"label": "Primario", "url": "https://reader.example/torre"},
                  {"label": "Alternativo", "url": "https://mirror.example/torre"}],
        **changes,
    }


def image_cover():
    image = io.BytesIO()
    Image.new("RGBA", (12, 19), "#ad86dd").save(image, "PNG")
    return {"kind": "local", "data": base64.b64encode(image.getvalue()).decode("ascii")}


class BookmarksTest(unittest.TestCase):
    def test_nested_paths_duplicates_and_root(self):
        rows, ignored = parse_bookmarks(FIXTURE.read_bytes())
        self.assertEqual((len(rows), ignored), (5, 1))
        self.assertEqual(rows[0]["path"], ["Barra de favoritos", "Lecturas & series"])
        self.assertEqual(rows[2]["path"], ["Barra de favoritos", "Pendientes"])
        self.assertEqual(rows[0]["url"], rows[2]["url"])
        self.assertNotEqual(rows[2]["url"], rows[3]["url"])
        self.assertEqual(rows[-1]["path"], [])
        self.assertEqual(rows[-1]["url"], "https://novels.example/")

    def test_parser_accepts_utf16_and_rejects_normal_web_page(self):
        rows, ignored = parse_bookmarks(FIXTURE.read_text(encoding="utf-8").encode("utf-16"))
        self.assertEqual((len(rows), ignored), (5, 1))
        with self.assertRaises(ValueError):
            parse_bookmarks(b"<html><dl><a href='https://example.org'>General</a></dl></html>")

    def test_url_normalization_keeps_reader_identifiers(self):
        self.assertEqual(web_url(" HTTP://EXAMPLE.org:80/?z=2&a=1#C "), "http://example.org/?z=2&a=1#C")
        self.assertEqual(web_url("https://example.org/a?#"), "https://example.org/a?#")
        for url in ("file:///test", "https://me@example.org", "https://example.org\\x", "https://example.org:80000"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                web_url(url)


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "library.sqlite3"
        app = create_app(self.database)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def save(self, **changes):
        result = self.client.post("/api/works", json=sample(**changes), headers=LOCAL)
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()

    def works(self):
        return self.client.get("/api/works").get_json()

    def preview(self):
        result = self.client.post("/api/bookmarks/preview",
                                  data={"file": (io.BytesIO(FIXTURE.read_bytes()), "edge.html")}, headers=LOCAL)
        self.assertEqual(result.status_code, 200)
        return result.get_json()

    def import_rows(self, rows):
        result = self.client.post("/api/bookmarks/import",
                                  json={"rows": rows, "type": "other", "status": "planned"}, headers=LOCAL)
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def restore(self, data, mode="merge", confirm=""):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        return self.client.post("/api/backup/restore", headers=LOCAL, data={
            "file": (io.BytesIO(data), "backup.json"), "mode": mode, "confirm": confirm,
        })

class LibraryTest(ApiCase):
    def test_crud_links_and_persistence(self):
        work = self.save()
        self.assertEqual(work["chapter"], "24.5")
        self.assertEqual(len(work["links"]), 2)
        url = f"/api/works/{work['id']}"
        update = sample(chapter="Epílogo", status="completed", tags=["Relectura"], links=[])
        update["updated_at"] = work["updated_at"]
        result = self.client.put(url, json=update, headers=LOCAL)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()["created_at"], work["created_at"])
        self.assertEqual(self.client.put(url, json=update, headers=LOCAL).status_code, 409)
        restarted = create_app(self.database).test_client().get("/api/works").get_json()[0]
        self.assertEqual((restarted["status"], restarted["chapter"], restarted["tags"], restarted["links"]),
                         ("completed", "Epílogo", ["Relectura"], []))
        self.assertEqual(self.client.delete(url, headers=LOCAL).status_code, 204)
        self.assertEqual(self.works(), [])

    def test_validation_and_conflicts_leave_data_unchanged(self):
        existing = self.save()
        for bad in (sample(title=" "), sample(status=[]), sample(type="comic"), sample(cover={"kind": "local", "data": "oops"})):
            self.assertEqual(self.client.post("/api/works", json=bad, headers=LOCAL).status_code, 400)
        self.assertEqual(self.client.post("/api/works", json=sample(title="Otra torre"), headers=LOCAL).status_code, 409)
        self.assertEqual(self.works(), [existing])

    def test_local_request_guard(self):
        self.assertEqual(self.client.post("/api/works", json=sample()).status_code, 403)
        self.assertEqual(self.client.post("/api/works", json=sample(), headers={
            **LOCAL, "Origin": "https://foreign.example",
        }).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "foreign.example"}).status_code, 400)
        self.assertEqual(self.works(), [])

    def test_preview_selection_and_idempotency(self):
        preview = self.preview()
        rows = preview["rows"]
        self.assertEqual(preview["ignored"], 1)
        self.assertEqual([row["similar"] for row in rows], [True, True, False, False, False])
        self.assertTrue(rows[2]["repeated"])
        self.assertEqual(self.works(), [])
        selection = [rows[0], rows[1], rows[2], rows[-1]]
        self.assertEqual(self.import_rows(selection), {"created": 3, "enriched": 0, "existing": 0})
        self.assertEqual(self.import_rows(selection), {"created": 0, "enriched": 0, "existing": 3})
        self.assertTrue(self.preview()["rows"][0]["existing"])
        work = next(item for item in self.works() if item["links"][0]["url"] == rows[0]["url"])
        self.assertEqual(work["bookmark_paths"], [rows[0]["path"], rows[2]["path"]])
        self.assertEqual(work["links"][0]["paths"], work["bookmark_paths"])
        result = self.client.put(f"/api/works/{work['id']}", json=sample(links=[]), headers=LOCAL)
        self.assertEqual(result.get_json()["bookmark_paths"], work["bookmark_paths"])

    def test_later_selection_enriches_existing_url(self):
        rows = self.preview()["rows"]
        self.assertEqual(self.import_rows([rows[0]])["created"], 1)
        self.assertEqual(self.import_rows([rows[2]])["enriched"], 1)
        self.assertEqual(self.works()[0]["links"][0]["paths"], [rows[0]["path"], rows[2]["path"]])

    def test_backup_round_trip_local_cover_and_import_metadata(self):
        work = self.save(cover=image_cover())
        self.import_rows(self.preview()["rows"])
        self.save(title="Otra obra", links=[], cover={"kind": "url", "url": "https://covers.example/a.png"})
        export = self.client.get("/api/backup")
        self.assertIn("attachment", export.headers["Content-Disposition"])
        image = self.client.get(work["cover"]["url"])
        self.assertEqual(image.mimetype, "image/webp")
        self.assertTrue(image.data)
        result = self.restore(export.data)
        self.assertEqual(result.get_json(), {"added": 0, "skipped": 6})
        result = self.restore(export.data, "replace", "REEMPLAZAR")
        self.assertEqual(result.get_json(), {"added": 6, "skipped": 0})
        self.assertEqual(self.client.get("/api/backup").get_json(), export.get_json())
        self.assertEqual(self.client.get(work["cover"]["url"]).data, image.data)

    def test_invalid_or_unconfirmed_backup_never_overwrites(self):
        self.save()
        export = self.client.get("/api/backup").get_json()
        self.assertEqual(self.restore(export, "replace").status_code, 400)
        self.assertEqual(self.restore(b'{"broken"', "replace", "REEMPLAZAR").status_code, 400)
        export["works"].append({**export["works"][0], "status": "bad"})
        self.assertEqual(self.restore(export, "replace", "REEMPLAZAR").status_code, 400)
        self.assertEqual(len(self.works()), 1)

    def test_backup_merge_preserves_existing_url_under_another_id(self):
        work = self.save()
        export = self.client.get("/api/backup").get_json()
        export["works"][0]["id"] = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        export["works"][0]["title"] = "Título desde la copia"
        self.assertEqual(self.restore(export).get_json(), {"added": 0, "skipped": 1})
        self.assertEqual(self.works(), [work])
