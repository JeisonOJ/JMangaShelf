"""Build explainable duplicate suggestions from saved bookmark metadata."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from itertools import combinations
from urllib.parse import unquote, urlsplit

from bookmarks import reference_hint, review_url, title_key, title_suggestion
from library import list_works

GENERIC_TITLES = {"manga", "manhwa", "manhua", "novela", "novel", "leer manga", "manga online", "inicio", "home"}
GENERIC_FOLDERS = GENERIC_TITLES | {
    "favorites bar", "barra de favoritos", "leyendo", "finalizados", "guardados", "favoritos", "pendientes",
    "con anime", "sin anime", "manga emision", "manga emision sin anime", "en emision", "reading",
    "completed", "finished", "bookmarks", "favorites", "completados", "por leer", "novelas", "otros",
}
NOVEL_ROUTES = {"novel", "novels", "novela", "novelas", "light-novel", "light-novels", "webnovel"}
COMIC_ROUTES = {"manga", "manhwa", "manhua", "comic", "comics"}
SERIES_ROUTES = NOVEL_ROUTES | COMIC_ROUTES | {"serie", "series", "title"}
EDITION_WORDS = r"(?:novel|novela|light novel|web novel|novela ligera|manga|manhwa|manhua|anime)"
NOVEL_WORDS = r"(?:novel|light novel|web novel|novela(?: ligera)?)"
CHAPTER_WORDS = r"(?:ch(?:apter)?\.?|cap(?:[ií]tulo)?\.?)"


def work_titles(work):
    return [work["title"], *work["aliases"], *(title for link in work["links"] for title in link["original_titles"])]


def match_evidence(kind, value, matches, **extra):
    return {"kind": kind, "value": value, "matches": {identifier: sorted(values) for identifier, values in matches.items()}, **extra}


def evidence_digest(evidence):
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def matching_titles_and_urls(works):
    """Keep v0.3.0 evidence and digest ordering for existing rejection decisions."""
    matches = defaultdict(lambda: defaultdict(set))
    for work in works:
        for title in work_titles(work):
            key = " ".join(unicodedata.normalize("NFC", title_suggestion(title)["title"]).casefold().split())
            if key not in GENERIC_TITLES and sum(c.isalnum() for c in key) >= 3 and not key.isdecimal():
                matches["title", key][work["id"]].add(title)
        for link in work["links"]:
            if link["role"] == "reading":
                matches["url", review_url(link["url"])][work["id"]].add(link["url"])
    groups = defaultdict(list)
    for (kind, key), members in sorted(matches.items()):
        if len(members) >= 2:
            groups[tuple(sorted(members))].append(match_evidence(kind, key, members))
    return groups


def route(url):
    return [unquote(part).casefold() for part in urlsplit(url).path.split("/") if part]


def source_format(work, link):
    parts = route(link["url"])
    labels = [work["title"], *link["original_titles"]]
    host = urlsplit(link["url"]).hostname
    novelcool = host == "novelcool.com" or host.endswith(".novelcool.com")
    if parts and parts[0] == "anime":
        return "anime"
    novel_routes = NOVEL_ROUTES - {"novel", "novels"} if novelcool else NOVEL_ROUTES
    if set(parts[:3]) & novel_routes or any(re.search(rf"\(\s*{NOVEL_WORDS}\s*\)", title, re.I) for title in labels):
        return "novel"
    if not novelcool and any(
        re.search(rf"\s{NOVEL_WORDS}\b(?=\s*[,(\[|–—-]|\s+{CHAPTER_WORDS}\s+\d|\s*$)", title, re.I)
        for title in labels
    ):
        return "novel"
    if work["type"] == "novel":
        # A classified novel with a comic route is ambiguous and needs manual review.
        return "mixed" if set(parts[:3]) & COMIC_ROUTES else "novel"
    comic_route = bool(set(parts[:3]) & COMIC_ROUTES)
    comic_title = any(re.search(r"\(\s*(?:manga|manhwa|manhua|comic)\s*\)", label, re.I) for label in labels)
    if novelcool and not (comic_route or comic_title):
        # NovelCool uses /novel/ and "Novel, Capítulo" for mixed content.
        return "unknown"
    return "comic" if work["type"] in COMIC_ROUTES or comic_route or comic_title else "unknown"


def folder_title_key(title):
    title = title_suggestion(title)["title"]
    # These suffixes are anchored reader metadata. Preserve sequel and volume labels.
    title = re.sub(r"\s+[-–—|·]\s+(?:mangadex|lector xd|zonatmo|tumangaonline|"
                   r"(?:manga\s+[-–|]\s+)?myanimelist(?:\.net)?|mangas no sekai|asura scans|manga freak|mangafreak)\s*$",
                   "", title, flags=re.I)
    title = re.sub(rf"[\s·,–—-]+\[?{CHAPTER_WORDS}\s*-?\s*\d+(?:[.,]\d+)?\]?\s*$", "", title, flags=re.I)
    title = re.sub(rf"\s*\({EDITION_WORDS}\)\s*$", "", title, flags=re.I)
    return title_key(title)


def series_slug_keys(url):
    parts = route(url)
    for index, part in enumerate(parts[:2]):
        if part in SERIES_ROUTES and index + 1 < len(parts):
            rest = parts[index + 1:]
            if rest[0].isdecimal() and len(rest) > 1:
                rest = rest[1:]
            slug = re.sub(r"_\d{8,}$|\.html?$", "", rest[0])
            keys = {title_key(slug)}
            if part == "title":
                short_id = re.fullmatch(r"[a-z0-9]*[0-9][a-z0-9]{2,5}-(.+)", slug)
                if short_id:
                    keys.add(title_key(short_id[1]))
                if re.fullmatch(r"[a-f0-9-]{36}", slug) and len(rest) > 1:
                    keys.add(title_key(rest[1]))
            return keys
    return set()


def matches_folder(key, name):
    if key == name:
        return True
    # Some romanizations differ only in spacing. Keep short or numbered names exact.
    compact = name.replace(" ", "")
    return (len(compact) >= 12 and not any(c.isdecimal() for c in compact)
            and key.replace(" ", "") == compact)


def folder_evidence(works, strong_groups):
    direct = defaultdict(lambda: defaultdict(list))
    parents = set()
    for work in works:
        for path in work["bookmark_paths"]:
            parents.update(tuple(path[:i]) for i in range(1, len(path)))
        for link in work["links"]:
            for path in link["paths"]:
                if path:
                    direct[tuple(path)][work["id"]].append((work, link))
                    parents.update(tuple(path[:i]) for i in range(1, len(path)))
    for path, members in sorted(direct.items()):
        name = title_key(path[-1])
        if (len(members) < 2 or path in parents or name in GENERIC_FOLDERS or len(name) < 4
                or name.isdecimal() or re.search(r"^(?:del creador|del autor|por ver|varios|coleccion)\b", name)):
            continue
        buckets = defaultdict(dict)
        for identifier, entries in members.items():
            for work, link in entries:
                hint = link["reference_hint"]
                kind = source_format(work, link)
                if hint == "search" or kind in {"anime", "mixed"}:
                    continue
                titles = [work["title"], *work["aliases"], *link["original_titles"]]
                matching = {title for title in titles if matches_folder(folder_title_key(title), name)}
                slug = any(matches_folder(key, name) for key in series_slug_keys(link["url"]))
                found = buckets[kind].setdefault(identifier, {"titles": set(), "urls": set(), "uncertain": set(),
                                                             "reading": False, "anchor": False})
                found["titles"].update(matching)
                found["urls"].add(link["url"])
                if kind == "unknown" and not hint:
                    found["uncertain"].add(link["url"])
                found["reading"] |= link["role"] == "reading" and not hint
                found["anchor"] |= bool(matching or slug)
        # An unknown format can join a single identified format, never bridge two.
        known = [kind for kind in ("comic", "novel") if buckets[kind]]
        if len(known) == 1:
            for identifier, clues in buckets.pop("unknown", {}).items():
                found = buckets[known[0]].setdefault(identifier, {"titles": set(), "urls": set(), "uncertain": set(),
                                                                 "reading": False, "anchor": False})
                found["titles"].update(clues["titles"])
                found["urls"].update(clues["urls"])
                found["uncertain"].update(clues["uncertain"])
                found["reading"] |= clues["reading"]
                found["anchor"] |= clues["anchor"]
        for kind, clues in buckets.items():
            anchors = {identifier for identifier, clue in clues.items() if clue["anchor"]}
            supported = anchors | {identifier for group in strong_groups if anchors.intersection(group) for identifier in group}
            clues = {identifier: clue for identifier, clue in clues.items() if identifier in supported}
            if len(clues) < 2 or (kind == "unknown" and len(known) == 2):
                continue
            if not any(c["titles"] for c in clues.values()) or not any(c["reading"] for c in clues.values()):
                continue
            titles = {identifier: c["titles"] for identifier, c in clues.items() if c["titles"]}
            uncertain = {identifier: sorted(c["uncertain"]) for identifier, c in clues.items() if c["uncertain"]}
            yield (tuple(sorted(clues)), [
                match_evidence("folder", path[-1], {identifier: c["urls"] for identifier, c in clues.items()},
                               path=path, uncertain=uncertain),
                match_evidence("folder_title", path[-1], titles, path=path),
            ])


def pair_key(left, right):
    return evidence_digest(["pair", *sorted((left, right))])


def title_partitions(ids, by_id):
    partitions = defaultdict(list)
    for identifier in ids:
        work = by_id[identifier]
        formats = {source_format(work, link) for link in work["links"] if link["reference_hint"] != "search"}
        if not work["links"]:
            formats.add("novel" if work["type"] == "novel" else "unknown")
        known = formats - {"unknown"}
        if len(known) == 1 and known <= {"comic", "novel"}:
            partitions[next(iter(known))].append(identifier)
        elif formats == {"unknown"}:
            partitions["unknown"].append(identifier)
    known = [kind for kind in ("comic", "novel") if partitions[kind]]
    if len(known) == 1:
        partitions[known[0]].extend(partitions.pop("unknown", []))
    return (tuple(sorted(members)) for members in partitions.values() if len(members) >= 2)


def candidate_review(db):
    works = list_works(db)
    original = matching_titles_and_urls(works)
    rejected = {row[0] for row in db.execute("SELECT signature FROM review_rejections")}
    legacy_pairs = defaultdict(set)
    projected = defaultdict(list)
    for ids, evidence in original.items():
        signature = evidence_digest(evidence)
        if signature in rejected:
            for pair in combinations(ids, 2):
                legacy_pairs[pair].add(signature)
        # A v0.3.0 pair can still be recognized when a third entry gains identical evidence.
        if rejected:
            for pair in combinations(ids, 2):
                projected[pair].extend({**clue, "matches": {i: clue["matches"][i] for i in pair}} for clue in evidence)
    for pair, evidence in projected.items():
        signature = evidence_digest(sorted(evidence, key=lambda item: (item["kind"], item["value"])))
        if signature in rejected:
            legacy_pairs[pair].add(signature)

    # Base evidence remains independent. A shared work alone cannot join two groups.
    grouped = defaultdict(list)
    by_id = {w["id"]: w for w in works}
    for ids, evidence in original.items():
        for clue in evidence:
            if clue["kind"] == "title":
                host_keys = {title_key(link["website"].removeprefix("www.").split(".")[0])
                             for identifier in ids for link in by_id[identifier]["links"]}
                if title_key(clue["value"]) in host_keys | GENERIC_FOLDERS:
                    continue
                for compatible in title_partitions(ids, by_id):
                    grouped[compatible].append({**clue, "matches": {i: clue["matches"][i] for i in compatible}})
            elif reference_hint(clue["value"]):
                continue
            else:
                grouped[ids].append(clue)

    for ids, evidence in folder_evidence(works, tuple(grouped)):
        grouped[ids].extend(evidence)

    pending, dismissed = [], []
    for ids, evidence in grouped.items():
        blockers = set()
        for pair in combinations(ids, 2):
            key = pair_key(*pair)
            if key in rejected:
                blockers.add(key)
            blockers.update(legacy_pairs.get(pair, ()))
        folder = next((clue for clue in evidence if clue["kind"] == "folder"), None)
        if folder:
            for subset, clues in grouped.items():
                if len(subset) < len(ids) and set(subset).issubset(ids):
                    evidence = evidence + [clue for clue in clues if clue["kind"] in {"title", "url"} and clue not in evidence]
        direct_matches = {identifier for clue in evidence if clue["kind"] in {"title", "url"} for identifier in clue["matches"]}
        group = {"ids": ids, "evidence": evidence, "signature": evidence_digest(evidence),
                 "title": folder["value"] if folder else evidence[0]["value"],
                 "folder_additions": sorted(set(ids) - direct_matches) if folder and direct_matches else []}
        if blockers:
            dismissed.append((group, blockers))
        else:
            pending.append(group)

    def visible(groups):
        # Suppress contained proposals only when the larger group is visible in this same list.
        expanded = [set(g["ids"]) for g in groups if any(e["kind"] == "folder" for e in g["evidence"])]
        return sorted((g for g in groups if not any(set(g["ids"]) < ids for ids in expanded)),
                      key=lambda g: (not any(e["kind"] == "url" for e in g["evidence"]), g["title"].casefold(), g["ids"]))

    return visible(pending), dismissed
