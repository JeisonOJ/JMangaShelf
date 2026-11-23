import json
import sqlite3
import unittest
from contextlib import closing
from threading import Thread
from urllib.request import Request, build_opener, ProxyHandler

from werkzeug.serving import make_server

from app import create_app
from bookmarks import parse_bookmarks, review_url, title_suggestion
from test_library import ApiCase, LOCAL, image_cover, sample


class NormalizationTest(unittest.TestCase):
    def test_url_review_key(self):
        base = "https://reader.example/serie/3"
        self.assertEqual(review_url(base), review_url("HTTPS://WWW.Reader.Example:443/serie/3/?utm_medium=email"))
        for suffix in ("/4", "?id=3", "?lang=es", "#ch3", "?", "#", "//", "%2f", "?utm_source=x;id=2"):
            with self.subTest(suffix=suffix):
                self.assertNotEqual(review_url(base), review_url(base + suffix))
        for left, right in [
            ("?id=1&id=2", "?id=2&id=1"), ("?a=%2F", "?a=/"),
            ("https://a.example/1", "https://b.example/1"),
            ("https://www.sub.reader.example/1", "https://sub.reader.example/1"),
            ("https://reader.example:8080/1", "https://reader.example/1"),
            ("http://reader.example/1", "https://reader.example/1"),
        ]:
            with self.subTest(left=left):
                prefix = base if left.startswith("?") else ""
                self.assertNotEqual(review_url(prefix + left), review_url(prefix + right))
        self.assertEqual(review_url(base + "?id=3&utm_source=feed&lang=es#x"),
                         base + "?id=3&lang=es#x")

    def test_brands_chapters_and_meaningful_numbers(self):
        for raw, title, chapter in [
            ("¡Me cansé de ser un rastrero y Ahora soy Multimillonario! - ManhwaWeb",
             "¡Me cansé de ser un rastrero y Ahora soy Multimillonario!", ""),
            ("Kuroiwa Medaka Ni Watashi No Kawaii Ga Tsuujinai Capítulo 158 - Novel Cool",
             "Kuroiwa Medaka Ni Watashi No Kawaii Ga Tsuujinai", "158"),
            ("Read or Die Vol. 2 - manga Online in English", "Read or Die Vol. 2", ""),
            ("86 - Eighty Six Vol. 3", "86 - Eighty Six Vol. 3", ""),
            ("Capítulo 1 de mi vida", "Capítulo 1 de mi vida", ""),
            ("Novel Cool", "Novel Cool", ""),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(title_suggestion(raw), {"title": title, "chapter": chapter})


class ConsolidationTest(ApiCase):
    def post(self, path, **data):
        return self.client.post("/api/" + path, json=data, headers=LOCAL)

    def edit(self, work, **changes):
        result = self.client.put("/api/works/" + work["id"], json={**work, **changes}, headers=LOCAL)
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()

    def backup(self):
        return self.client.get("/api/backup").get_json()

    def groups(self):
        return self.client.get("/api/review").get_json()["groups"]

    def test_evidence_and_rejection_lifecycle(self):
        left = self.save(title="A Japanese Title", aliases=["La misma obra"], links=[
            {"label":"", "url":"https://www.reader.example/serie/3/?utm_source=mail"}])
        middle = self.save(title="La misma obra", links=[{"label":"", "url":"https://reader.example/serie/3"}])
        third = self.save(title="Otro título", aliases=["A Japanese Title"], links=[])
        unrelated = self.save(title="La misma obra 2", links=[])
        groups = self.groups()
        self.assertEqual({frozenset(g["ids"]) for g in groups}, {
            frozenset([left["id"], middle["id"]]), frozenset([left["id"], third["id"]])})
        self.assertFalse(any(unrelated["id"] in g["ids"] for g in groups))
        pair = next(g for g in groups if middle["id"] in g["ids"])
        self.assertEqual({e["kind"] for e in pair["evidence"]}, {"title","url"})
        before = self.works()
        self.assertEqual(self.post("review/reject", signature=pair["signature"]).status_code, 204)
        self.assertEqual(self.works(), before)
        self.edit(middle, notes="Una nota nueva")
        self.assertEqual(len(self.groups()), 1)
        stored = self.backup()
        self.assertEqual(self.restore(stored, "replace", "REEMPLAZAR").status_code, 200)
        self.assertEqual(self.backup(), stored)
        self.assertEqual(len(self.groups()), 1)
        self.edit(left, aliases=["Otra forma"])
        self.assertEqual(len(self.groups()), 1)
        self.assertEqual(self.post("review/reject", signature=pair["signature"]).status_code, 409)
        dismissed, = self.client.get("/api/review").get_json()["dismissed"]
        self.assertEqual(self.post("review/reconsider", signature=dismissed["signature"]).status_code, 204)
        self.assertEqual(len(self.groups()), 2)

    def test_folder_expands_pair_with_chapter_and_catalog_but_not_novel_or_sequel(self):
        path = ["Favorites bar", "Manga", "Cenizas de luna"]
        rows = [
            ("Cenizas de luna", "https://read.example/manga/cenizas-de-luna"),
            ("Cenizas de luna - ManhwaWeb", "https://mirror.example/manhwa/cenizas_de_luna_1735929841599"),
            ("Chapter 1", "https://read.example/manga/cenizas-de-luna/15-chapter-1"),
            ("Cenizas de luna (Manga)", "https://anilist.co/manga/567/cenizas-de-luna"),
            ("Cenizas de luna", "https://reader.example/novel/cenizas-de-luna"),
            ("Cenizas de luna 2", "https://new.example/manga/cenizas-de-luna-2"),
            ("Cenizas de luna", "https://www.google.com/search?q=cenizas+de+luna"),
        ]
        entries = "".join(f'<DT><A HREF="{url}">{title}</A>' for title, url in rows)
        html = ("<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><H3>Favorites bar</H3><DL>"
                "<DT><H3>Manga</H3><DL><DT><H3>Cenizas de luna</H3><DL>" + entries + "</DL></DL></DL></DL>")
        bookmarks, ignored = parse_bookmarks(html.encode())
        self.assertEqual(ignored, 0)
        self.assertEqual({tuple(row["path"]) for row in bookmarks}, {tuple(path)})
        self.assertEqual(self.import_rows(bookmarks)["created"], 7)
        by_url = {w["links"][0]["url"]: w for w in self.works()}
        # A catalog suggestion must stay a hint even when the existing role is reading.
        self.assertEqual((by_url[rows[3][1]]["links"][0]["reference_hint"],by_url[rows[3][1]]["links"][0]["role"]),
                         ("catalog","reading"))
        self.assertEqual(by_url[rows[-1][1]]["links"][0]["reference_hint"],"search")
        # One title/alias pair shares the confirmed title. The known novel and search hit are excluded.
        group, = self.groups()
        self.assertCountEqual(group["ids"], [by_url[url]["id"] for _, url in rows[:4]])
        self.assertEqual(next(e["path"] for e in group["evidence"] if e["kind"] == "folder"), path)
        self.assertIn(by_url[rows[2][1]]["id"], group["folder_additions"])
        self.assertEqual(self.import_rows(bookmarks), {"created":0,"enriched":0,"existing":7})
        self.assertNotIn("reference_hint", self.backup()["works"][0]["links"][0])

    def test_generic_ambiguous_parent_and_sibling_folders(self):
        rows = []
        for i, folder in enumerate(["Manga","Leyendo","Finalizados","Guardados","Manga Emisión","Manga Emisión Sin Anime",
                                     "Manhwa","Manhua","Con Anime","Sin Anime","Joyas raras"]):
            rows.extend([
                {"title":folder, "url":f"https://one.example/manga/series-{i}", "path":["Manga",folder]},
                {"title":f"Obra ajena {i}", "url":f"https://two.example/manga/otra-{i}", "path":["Manga",folder]},
            ])
        rows.extend([
            {"title":"Una misma hoja (Manga)", "url":"https://one.example/manga/una-misma-hoja", "path":["Edge","Uno","Una misma hoja"]},
            {"title":"Una misma hoja - MangaDex", "url":"https://two.example/manga/una-misma-hoja", "path":["Edge","Dos","Una misma hoja"]},
            {"title":"Carpeta padre (Manga)", "url":"https://one.example/manga/carpeta-padre", "path":["Edge","Carpeta padre"]},
            {"title":"Carpeta padre - MangaDex", "url":"https://two.example/manga/carpeta-padre", "path":["Edge","Carpeta padre"]},
            {"title":"Serie de la subcarpeta", "url":"https://three.example/manga/otra", "path":["Edge","Carpeta padre","Subcarpeta"]},
        ])
        self.import_rows(rows)
        self.assertEqual(self.groups(), [])

    def test_legacy_rejection_survives_folder_and_identical_title_expansion(self):
        path = ["Edge", "Manga", "Reino de carbón"]
        rows = [{"title":"Reino de carbón", "url":f"https://{host}.example/manga/reino-de-carbon", "path":path}
                for host in ("original", "mirror", "third")]
        self.import_rows([{**row, "path":[]} for row in rows[:2]])
        pair, = self.groups()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO review_rejections VALUES(?)", (pair["signature"],))
        self.import_rows(rows)
        self.assertEqual(self.groups(), [])
        review = self.client.get("/api/review").get_json()
        group, = review["dismissed"]
        self.assertEqual(len(group["ids"]),3)
        self.assertIn("folder", {e["kind"] for e in group["evidence"]})
        self.assertEqual(self.post("review/reconsider", signature=group["signature"]).status_code,204)
        self.assertEqual(len(self.groups()),1)

    def test_equivalent_url_and_alias_evidence_join_edge_folder(self):
        path = ["Manga", "Crónicas de Sal"]
        rows = [{"title":"Crónicas de Sal", "url":"https://read.example/manga/cronicas-de-sal", "path":path},
                {"title":"Página de lectura", "url":"https://www.read.example/manga/cronicas-de-sal?utm_source=bookmark", "path":path},
                {"title":"Salt Chronicles", "url":"https://alt.example/title/salt-chronicles", "path":path}]
        self.import_rows(rows)
        alternate = next(w for w in self.works() if w["title"] == "Salt Chronicles")
        self.edit(alternate, aliases=["Crónicas de Sal"])
        group, = self.groups()
        self.assertEqual(len(group["ids"]),3)
        self.assertEqual({e["kind"] for e in group["evidence"]}, {"title","url","folder","folder_title"})

    def test_novelcool_generic_branding_and_route_leave_format_unknown(self):
        path = ["Favorites bar", "Manga", "Atlas de prueba"]
        novelcool = "https://es.novelcool.com/novel/Atlas-de-prueba.html"
        self.import_rows([
            {"title":"Atlas de prueba", "url":"https://reader.example/manga/atlas-de-prueba", "path":path},
            {"title":"Atlas De Prueba Novel, Capítulo 56 - Novel Cool - Lee novelas ligeras en línea gratis.",
             "url":novelcool, "path":path},
        ])
        group, = self.groups()
        folder = next(e for e in group["evidence"] if e["kind"] == "folder")
        entry = next(w for w in self.works() if w["links"][0]["url"] == novelcool)
        self.assertEqual(folder["uncertain"], {entry["id"]:[novelcool]})
        self.assertEqual(entry["links"][0]["role"], "reading")
        self.edit(entry, type="novel", classified=True)
        self.assertEqual(self.groups(), [])

    def test_reviewed_three_way_merge_preserves_sources_covers_and_rolls_back(self):
        rows = [{"title":f"Torre Capítulo {n}", "path":["Edge", f"Carpeta {n}"],
                 "url":f"https://lector{n}.example/torre"} for n in (1, 2, 3)]
        self.import_rows(rows)
        self.assertEqual(len(self.groups()[0]["ids"]), 3)
        works = []
        for i, work in enumerate(self.works()):
            links = work["links"]
            links[0]["health"] = ["suspicious","broken","working"][i]
            cover = image_cover() if i != 1 else {"kind":"url","url":"https://cover.example/2"}
            works.append(self.edit(work, title=f"Torre {i}", notes=f"Nota {i}", chapter=str(i), aliases=[f"Alias {i}"],
                                   status=["planned","reading","completed"][i], cover=cover, tags=[f"Tag {i}"], links=links))
        a,b,c = works
        data = dict(versions={w["id"]:w["updated_at"] for w in works}, canonical=c["id"], title="Torre",
                    preferred=b["links"][0]["url"], status=c["id"], chapter=b["id"], type=a["id"],
                    classified=c["id"], cover=c["id"])
        before = self.backup()
        preview = self.post("merge/preview", **data).get_json()
        self.assertEqual(preview["alternate_count"], 2)
        self.assertEqual(self.post("merge", **data).status_code, 400)
        self.assertEqual(self.backup(), before)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON links BEGIN SELECT RAISE(ABORT,'test'); END")
        self.assertEqual(self.post("merge", **data, confirm=True).status_code, 409)
        self.assertEqual(self.backup(), before)
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("DROP TRIGGER fail_insert")
        self.assertEqual(self.post("merge", **data, confirm=True).get_json()["id"], c["id"])
        self.assertEqual(self.import_rows(rows), {"existing":3,"enriched":0,"created":0})
        merged, = self.works()
        self.assertEqual((merged["id"],merged["chapter"],merged["status"]), (c["id"],"1","completed"))
        self.assertEqual(merged["links"][0], b["links"][0])
        self.assertCountEqual(merged["links"], [w["links"][0] for w in works])
        self.assertCountEqual(merged["bookmark_paths"], [r["path"] for r in rows])
        for w in works:
            self.assertIn(w["title"], merged["aliases"])
            self.assertIn(w["aliases"][0], merged["aliases"])
            self.assertIn(w["tags"][0], merged["tags"])
            self.assertIn(w["notes"], merged["notes"])
        out = self.backup()
        self.assertCountEqual([out["works"][0]["cover"], *out["works"][0]["alternate_covers"]],
                              [w["cover"] for w in before["works"]])
        self.assertEqual(self.restore(out, "replace", "REEMPLAZAR").status_code, 200)
        self.assertEqual(self.backup(), out)
        for cover in self.works()[0]["alternate_covers"]:
            if cover["kind"] == "local":
                self.assertEqual(self.client.get(cover["url"]).mimetype, "image/webp")
        self.assertEqual(self.post("merge", **data, confirm=True).status_code, 409)

    def test_folder_rules_preview_and_confirmation(self):
        paths = [["Manga","Manhwa","Finalizados"], ["Manga"], ["Leyendo"], ["Manga","Manhua"], ["Manhua","Novela"]]
        self.import_rows([{"title":f"Título {i}", "url":f"https://e.example/{i}", "path":path} for i,path in enumerate(paths)])
        for w in self.works():
            if w["bookmark_paths"] == [["Manga"]]:
                self.edit(w, type="novel", chapter="21")
        before = self.works()
        data = dict(operation="folder_type", versions={w["id"]:w["updated_at"] for w in before})
        preview = self.post("batch/preview", **data).get_json()
        self.assertEqual({x["title"]:x["fields"] for x in preview["changes"]},
                         {"Título 0":{"type":"manhwa"}, "Título 3":{"type":"manhua"}})
        self.assertEqual(self.post("batch", **data).status_code, 400)
        self.assertEqual(self.works(), before)
        self.assertEqual(self.post("batch", **data, confirm=True).get_json(), {"count":2})
        for old, new in zip(before, self.works(), strict=True):
            self.assertEqual((new["chapter"],new["classified"],new["status"]), (old["chapter"],False,"planned"))
        self.assertEqual(self.post("batch", **data, confirm=True).status_code, 409)

    def test_restore_v2_and_reject_malformed_v3(self):
        self.save()
        v2 = self.backup()
        del v2["review_rejections"]
        v2["version"] = 2
        for w in v2["works"]:
            del w["rating"], w["mal_ids"]
            del w["aliases"], w["alternate_covers"]
            for link in w["links"]:
                del link["health"]
        self.assertEqual(self.restore(v2,"replace","REEMPLAZAR").status_code, 200)
        self.assertEqual(self.works()[0]["links"][0]["health"], "unchecked")
        before = self.backup()
        for key, value in (("aliases","bad"),("alternate_covers",[None])):
            invalid = json.loads(json.dumps(before))
            invalid["works"][0][key] = value
            self.assertEqual(self.restore(invalid,"replace","REEMPLAZAR").status_code, 400)
        self.assertEqual(self.backup(),before)

    def test_loopback_http_smoke(self):
        with make_server("127.0.0.1", 0, create_app(self.database)) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                opener = build_opener(ProxyHandler({}))
                def fetch(path, data=None):
                    req = Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                  headers={**LOCAL, "Content-Type":"application/json"})
                    with opener.open(req, timeout=5) as response:
                        return response.read()
                self.assertIn(b"v1.0.0", fetch("/"))
                for asset in ("app.js", "organization.js", "covers.js", "mal-import.js", "style.css"):
                    self.assertTrue(fetch("/static/" + asset))
                works = [json.loads(fetch("/api/works", sample(title=f"Torre {i}", links=[], aliases=["La Torre"])))
                         for i in range(3)]
                self.assertEqual(len(json.loads(fetch("/api/review"))["groups"][0]["ids"]), 3)
                data = {k:works[0]["id"] for k in ("canonical","status","chapter","type","classified","cover")}
                data.update(versions={w["id"]:w["updated_at"] for w in works}, title="La Torre", preferred="")
                self.assertIn("work",json.loads(fetch("/api/merge/preview",data)))
                fetch("/api/merge",{**data,"confirm":True})
                self.assertEqual(len(json.loads(fetch("/api/backup"))["works"]),1)
            finally:
                server.shutdown()
                thread.join()
