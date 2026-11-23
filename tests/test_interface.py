import os
import subprocess
import base64
from pathlib import Path
from threading import Thread
from unittest import skipUnless

from flask import Response
from werkzeug.serving import make_server

from app import create_app
from library import image_cover
from test_library import ApiCase, image_cover as sample_cover


BROWSER = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"


class InterfaceTest(ApiCase):
    @skipUnless(BROWSER.is_file(), "Requiere Chrome local para la prueba de interfaz")
    def test_edit_preserves_loaded_cards_and_viewport(self):
        self.import_rows([{"title":f"Serie {i:03}", "url":f"https://reader.example/{i}", "path":["Manga"]}
                          for i in range(240)])
        self.run_browser_test(create_app(self.database), "scroll_workflow.js")

    @skipUnless(BROWSER.is_file(), "Requiere Chrome local para la prueba de interfaz")
    def test_compact_cards_rating_mal_review_and_bulk_delete(self):
        self.save(title="Título muy largo " * 16 + "FIN", tags=["etiqueta muy larga " * 3],
                  chapter="Epílogo de un capítulo largo", rating=0)
        self.save(title="Torre local", links=[{"url":"https://myanimelist.net/manga/101/Tower","label":"MAL","role":"reference"}],
                  rating=4)
        self.save(title="Pequeña", links=[])
        self.run_browser_test(create_app(self.database), "release_workflow.js", viewport="650,850")

    @skipUnless(BROWSER.is_file(), "Requiere Chrome local para la prueba de interfaz")
    def test_filtered_bulk_and_group_confirmation_in_browser(self):
        rows = [{"title":f"Serie {i}", "url":f"https://reader.example/{i}", "path":["Manga","Manhua"]}
                for i in range(75)]
        for row in rows[:3]:
            row["path"].append("Torre compartida")
        rows[2]["url"] = "https://anilist.co/manga/123/torre-compartida"
        self.import_rows(rows)
        self.run_browser_test(create_app(self.database), "workflow.js")

    @skipUnless(BROWSER.is_file(), "Requiere Chrome local para la prueba de interfaz")
    def test_cover_search_batch_pause_and_quick_reading_actions(self):
        self.save(title="Lectura principal", links=[
            {"label":"Primario", "url":"https://reader.example/primary", "health":"working"},
            {"label":"Espejo", "url":"https://reader.example/alt", "health":"broken"},
            {"label":"Catálogo", "url":"https://anilist.co/manga/9/test", "role":"reference"},
        ])
        for title in ("Obra B", "Obra C", "Obra D"):
            self.save(title=title, links=[], status="planned")
        app = create_app(self.database)
        image = image_cover(base64.b64decode(sample_cover()["data"]))
        search_requests = []
        cover_requests = []

        class TestAniList:
            def search(self, query, _work):
                search_requests.append(query)
                return {"suggested":1, "candidates":[
                    {"id":id, "titles":{"romaji":name, "native":"塔"}, "aliases":["Otro título"], "format":"MANGA",
                     "country":"JP", "year":2022, "image":"/test-cover", "thumbnail":"/test-cover", "token":str(id)}
                    for id,name in ((1,"Primera edición"),(2,"Segunda edición"))
                ]}

            def cover(self, token):
                cover_requests.append(token)
                return image

        app.extensions["anilist"] = TestAniList()

        @app.get("/test-cover")
        def test_cover_image():
            return Response(image[2], mimetype=image[1])

        self.run_browser_test(app, "covers_workflow.js")
        self.assertEqual(search_requests[:2], ["Lectura principal", "Título alternativo"])
        self.assertEqual(len(search_requests), 4)
        self.assertEqual(cover_requests, ["2", "1"])
        self.assertEqual(sum(w["cover"] is not None for w in self.works()), 2)

    def run_browser_test(self, app, fixture, viewport="1200,900"):
        # Exercise the complete UI against a disposable server and a fresh browser profile.
        @app.get("/ui-test.js")
        def driver():
            script = Path(__file__).with_name("fixtures").joinpath(fixture).read_text(encoding="utf-8")
            return Response(script, mimetype="text/javascript")

        @app.after_request
        def load_driver(response):
            if response.mimetype == "text/html":
                response.set_data(response.get_data().replace(b"</body>", b'<script defer src="/ui-test.js"></script></body>'))
            return response

        with make_server("127.0.0.1", 0, app) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run([
                    str(BROWSER), "--headless", "--disable-gpu", "--disable-extensions", "--disable-sync",
                    "--disable-background-networking", "--disable-component-update", "--no-default-browser-check",
                    "--no-first-run", "--disable-crash-reporter", "--no-proxy-server",
                    "--window-size=" + viewport,
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                    "--user-data-dir=" + str(Path(self.temp.name) / "test-profile"),
                    "--virtual-time-budget=45000", "--dump-dom", f"http://127.0.0.1:{server.server_port}/"
                ], capture_output=True, timeout=60)
                dom = result.stdout.decode("utf-8", errors="replace")
                marker = dom.partition('data-test-result="')[2].partition('"')[0]
                self.assertEqual(marker, "ok", marker or result.stderr[-500:])
            finally:
                server.shutdown()
                thread.join()
