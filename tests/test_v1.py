import io
import json
from pathlib import Path

from PIL import Image

from test_library import ApiCase, LOCAL


class PublicReleaseTest(ApiCase):
    def test_browser_html_preview_and_confirmation(self):
        for browser, toolbar in [("chrome", "Barra de marcadores"), ("firefox", "Toolbar")]:
            with self.subTest(browser=browser):
                raw = Path(__file__).with_name("fixtures").joinpath(browser + ".html").read_bytes()
                preview = self.client.post("/api/bookmarks/preview", headers=LOCAL,
                                           data={"file": (io.BytesIO(raw), browser + ".html")}).get_json()
                first, second = preview["rows"]
                self.assertEqual(first["path"], [toolbar, "Lecturas & series"])
                self.assertEqual(second["path"], [])
                self.assertEqual(first["original_title"], "Atlas de papel")
                if browser == "chrome":
                    self.assertTrue(first["similar"])
                    self.assertEqual(self.works(), [])
                else:
                    self.assertTrue(first["existing"])
                self.import_rows(preview["rows"])
                self.assertEqual(len(self.works()), 2)
        link = next(w["links"][0] for w in self.works() if w["links"][0]["url"].endswith("#1"))
        self.assertEqual(link["origin"], "Navegador")
        self.assertEqual(len(link["paths"]), 2)

    def test_public_icons_and_manifest(self):
        def asset(path):
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200)
                return response.get_data()
            finally:
                response.close()
        with Image.open(io.BytesIO(asset("/favicon.ico"))) as icon:
            self.assertEqual(icon.ico.sizes(), {(n, n) for n in (16, 24, 32, 48, 64, 128, 256)})
        manifest = json.loads(asset("/static/manifest.webmanifest"))
        self.assertEqual(manifest["name"], "JMangaShelf")
        for size in (32, 180, 192, 512):
            with Image.open(io.BytesIO(asset(f"/static/icons/marca-{size}.png"))) as icon:
                self.assertEqual(icon.size, (size, size))
        self.assertTrue(all(asset("/static/" + item["src"]) for item in manifest["icons"]))
        self.assertIn(b'rel="apple-touch-icon"', asset("/"))
