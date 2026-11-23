import base64
import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from anilist import AniList, SEARCH_URL, USER_AGENT, safe_image_url
from test_library import ApiCase, LOCAL, image_cover, sample


IMAGE = "https://s4.anilist.co/file/anilistcdn/media/manga/cover/large/bx123-example.jpg"


def media(identifier, name, **changes):
    return {"id": identifier, "title": {"romaji": name, "native": "塔"}, "synonyms": [],
            "format": "MANGA", "countryOfOrigin": "JP", "startDate": {"year": 2021},
            "coverImage": {"large": IMAGE}, **changes}


def graphql(*items):
    return io.BytesIO(json.dumps({"data": {"Page": {"media": list(items)}}}).encode())


class AniListTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.cache = Path(self.temp.name) / "anilist-covers"
        self.service = AniList(self.cache)
        self.client.application.extensions["anilist"] = self.service

    def search(self, query, work=None):
        data = {"query": query}
        if work:
            data["work_id"] = work["id"]
        return self.client.post("/api/anilist/search", json=data, headers=LOCAL)

    def select(self, work, token, confirm=True):
        return self.client.patch(f"/api/works/{work['id']}/cover", headers=LOCAL,
                                 json={"token": token, "updated_at": work["updated_at"], "confirm": confirm})

    def test_search_alias_has_unique_compatible_match_and_constructs_graphql(self):
        work = self.save(type="manga", aliases=["Crónicas Torre"])
        with patch.object(self.service, "open", return_value=graphql(
                media(1, "La torre 2"), media(10, "Native title", synonyms=["Cronicas Torre"], title={
                    "romaji": "Japanese title", "english": "The Tower", "native": "塔"}))) as remote:
            response = self.search("Título modificado", work)
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["suggested"], 10)
            self.assertEqual(payload["candidates"][1]["titles"]["native"], "塔")
            request = remote.call_args.args[0]
            self.assertEqual((request.full_url, request.get_method()), (SEARCH_URL, "POST"))
            self.assertIn("type: MANGA", json.loads(request.data)["query"])
            self.assertEqual(json.loads(request.data)["variables"], {"search": "Título modificado"})
        self.assertIsNone(self.works()[0]["cover"])
        self.assertFalse(self.cache.exists())

    def test_ambiguity_wrong_format_and_empty_results(self):
        work = self.save(type="manga")
        for entries, expected in [([media(1, "La torre"), media(2, "La torre", format="NOVEL")], None),
                                  ([media(2, "La torre", format="NOVEL")], None),
                                  ([media(2, "La torre", coverImage={})], None),
                                  ([], None)]:
            self.service.next_search = 0
            with patch.object(self.service, "open", return_value=graphql(*entries)):
                result = self.search("La torre", work).get_json()
                self.assertEqual(result["suggested"], expected)
                self.assertEqual(len(result["candidates"]), len(entries))

    def test_rate_limit_cooldown_bounded_retry_and_invalid_json(self):
        error = HTTPError(SEARCH_URL, 429, "Limit", {"Retry-After": "27"}, None)
        with patch.object(self.service, "open", side_effect=error) as opener:
            first = self.search("La torre")
            self.assertEqual(first.status_code, 429)
            self.assertEqual(first.headers["Retry-After"], "27")
            self.assertEqual(self.search("Retry").status_code, 429)
            opener.assert_called_once()
        self.service.next_search = 0
        with patch("anilist.time.sleep") as sleep, patch.object(self.service, "open", side_effect=URLError("offline")) as opener:
            self.assertEqual(self.search("La torre").status_code, 502)
            self.assertEqual(opener.call_count, 2)
            sleep.assert_called_once()
        self.service.next_search = 0
        with patch.object(self.service, "open", return_value=io.BytesIO(b'{"data":null}')):
            self.assertEqual(self.search("La torre").status_code, 502)

    def test_live_403_signature_regression_and_upstream_diagnostic(self):
        problem = {"title":"Error 1010: Access denied", "error_code":1010, "error_name":"browser_signature_banned"}

        def anilist(request, *, timeout):
            if request.get_header("User-agent") != USER_AGENT:
                raise HTTPError(SEARCH_URL, 403, "Forbidden", {"CF-Ray":"test-ray"},
                                io.BytesIO(json.dumps(problem).encode()))
            return graphql(media(123, "Torre del ejemplo",
                                 coverImage={"medium":IMAGE.replace("/large/", "/small/"), "extraLarge":IMAGE}))

        with patch.object(self.service, "open", side_effect=anilist):
            result = self.search("Torre").get_json()
            candidate, = result["candidates"]
            self.assertEqual(candidate["id"], 123)
            self.assertIn("/small/", candidate["thumbnail"])
        self.service.next_search = 0
        with patch("anilist.USER_AGENT", "Python-urllib"), patch.object(self.service, "open", side_effect=anilist) as remote:
            with self.assertLogs("anilist", level="WARNING") as log:
                response = self.search("Torre")
            self.assertEqual(response.status_code, 502)
            self.assertIn("HTTP 403", response.get_json()["error"])
            self.assertIn("1010", log.output[0])
            self.assertIn("test-ray", log.output[0])
            remote.assert_called_once()

    def test_graphql_error_is_distinct_from_empty_and_malformed_results(self):
        for raw, status, expected in [
            (b'{"data":{"Page":{"media":[]}}}', 200, None),
            (b'{"errors":[{"status":400,"message":"Invalid argument"}]}', 502, "GraphQL"),
            (b'{"data":{"Page":{"media":[{}]}}}', 502, "formato no válido"),
        ]:
            self.service.next_search = 0
            with patch.object(self.service, "open", return_value=io.BytesIO(raw)):
                response = self.search("Torre")
            self.assertEqual(response.status_code, status)
            if expected:
                self.assertIn(expected, response.get_json()["error"])
            else:
                self.assertEqual(response.get_json()["candidates"], [])

    def test_confirm_cache_and_json_backup_without_cache(self):
        first = self.save()
        second = self.save(title="Segunda", links=[])
        with patch.object(self.service, "open", return_value=graphql(media(4, "La torre"))):
            token = self.search("La torre", first).get_json()["candidates"][0]["token"]
        self.assertEqual(self.select(first, token, False).status_code, 400)
        self.assertEqual(self.select(first, "fabricated").status_code, 400)
        self.assertFalse(self.cache.exists())
        with patch.object(self.service, "open", return_value=io.BytesIO(base64.b64decode(image_cover()["data"]))) as remote:
            a = self.select(first, token)
            b = self.select(second, token)
            self.assertEqual((a.status_code,b.status_code), (200,200))
            remote.assert_called_once()
        file, = self.cache.iterdir()
        self.assertRegex(file.name, r"^[a-f0-9]{64}\.webp$")
        saved = a.get_json()
        for key in ("links", "aliases", "status", "chapter", "notes", "bookmark_paths"):
            self.assertEqual(first[key], saved[key])
        original = self.client.get(saved["cover"]["url"]).data
        backup = self.client.get("/api/backup").get_json()
        file.unlink()
        self.assertEqual(self.restore(backup,"replace","REEMPLAZAR").status_code, 200)
        self.assertEqual(self.client.get(saved["cover"]["url"]).data, original)
        self.assertFalse(any(self.cache.iterdir()))

    def test_corrupt_image_write_failure_stale_and_existing_cover_preserved(self):
        work = self.save(cover=image_cover())
        p = "/api/works/" + work["id"]
        candidate = {"kind":"anilist", "token":self.service.tokens.dumps({"url":IMAGE}), "confirm":True}
        old = self.client.get(work["cover"]["url"]).data
        self.assertEqual(self.select(work, candidate["token"]).status_code, 409)
        with patch.object(self.service, "open", return_value=io.BytesIO(b"<html>")):
            r = self.client.put(p, json={**work, "cover":candidate}, headers=LOCAL)
            self.assertEqual(r.status_code, 502)
        self.assertFalse(self.cache.exists())
        with patch.object(self.service, "open", return_value=io.BytesIO(base64.b64decode(image_cover()["data"]))), \
                patch("anilist.os.replace", side_effect=OSError("disk")):
            self.assertEqual(self.client.put(p, json={**work, "cover":candidate}, headers=LOCAL).status_code, 502)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(self.client.get(work["cover"]["url"]).data, old)
        with patch.object(self.service, "open") as remote:
            r = self.client.put(p, json={**work, "cover":candidate, "updated_at":"stale"}, headers=LOCAL)
            self.assertEqual(r.status_code, 409)
            remote.assert_not_called()

    def test_only_expected_cdn_images_get_selection_tokens(self):
        for url in (IMAGE.replace("s4.anilist.co", "localhost"), IMAGE.replace("https:", "http:"),
                    IMAGE.replace("large/", "large/../"), IMAGE.replace("large/", "large/%2e%2e/"),
                    IMAGE.replace("bx123-example.jpg", "default.jpg"), IMAGE + "?redirect=1",
                    IMAGE.replace("s4.anilist.co", "s4.anilist.co@other.example")):
            with self.subTest(url=url):
                self.assertIsNone(safe_image_url(url))
        self.assertEqual(safe_image_url(IMAGE), IMAGE)
        with patch.object(self.service, "open", return_value=graphql(media(4, "La torre", coverImage={"large":"http://127.0.0.1/x.jpg"}))):
            self.assertIsNone(self.search("La torre").get_json()["candidates"][0]["token"])

    def test_quick_chapter_updates_only_progress_with_optimistic_lock(self):
        work = self.save(aliases=["Alt"], links=[{"label":"Preferida", "url":"https://reader.example/a", "health":"broken"},
                                                  {"label":"Alterna", "url":"https://reader.example/b"},
                                                  {"label":"Wiki", "url":"https://info.example/a", "role":"reference"}])
        path = f"/api/works/{work['id']}/chapter"
        data = {"chapter":" Epílogo ", "updated_at":work["updated_at"]}
        result = self.client.patch(path,json=data,headers=LOCAL).get_json()
        self.assertEqual(result["chapter"], "Epílogo")
        self.assertEqual(self.client.patch(path,json=data,headers=LOCAL).status_code,409)
        self.assertEqual(self.client.patch(path,json={"chapter":"x"*81,"updated_at":result["updated_at"]},headers=LOCAL).status_code,400)
        self.assertEqual({k:v for k,v in result.items() if k not in {"chapter","updated_at"}},
                         {k:v for k,v in work.items() if k not in {"chapter","updated_at"}})
        self.assertEqual(self.client.patch(path,json={"chapter":"","updated_at":result["updated_at"]},headers=LOCAL).get_json()["chapter"],"")
