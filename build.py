#!/usr/bin/env python3
"""Genres — IMDb-verified genre catalogs for Nuvio / Stremio, published as a static addon.

Why: TMDB's TV taxonomy has no Horror, Thriller or Sport genre and its keyword tags are noisy, so a TMDB-only
"Top Rated Horror Series" row ends up full of anime and anthology shows that have nothing to do with horror.
IMDb's genre taxonomy is complete for both movies and series, and IMDb publishes it (with ratings and vote
counts) as a daily dataset. This build uses IMDb as the single source of truth for *what belongs in a genre*
and uses TMDB only to know *what is popular / new right now* and for artwork:

  Top Rated <genre> Movies / Series   IMDb rating, with a vote floor so only widely-seen titles qualify.
  <genre> Essentials                  the genre's canon: the titles the most people have seen (IMDb vote count),
                                      any era, rating >= 6.0 — the staples a strict Top Rated row leaves out.
  Popular <genre> Movies / Series     TMDB "popular right now" pools, kept to titles whose IMDb genres include
                                      the genre.
  New <genre> Movies / Series         TMDB newest releases that people have already rated, same IMDb check.

Thirteen genres x eight rows = 104 genre catalogs, plus Top Rated / Essentials for 8 decades and 14 themes, served as /catalog/{type}/{row}/genre={Genre}.json so Nuvio's
collections can point at them (the catalogs are hidden from the Home page: showInHome=false and a required
genre extra). Rebuilt daily.

Environment:
  SITE_URL        public base URL of the deployed site (required)
  TMDB_API_KEY    TMDB v3 key (required for Popular / New rows, posters, descriptions and the anime split)
  ROW_SIZE        items per row (default 100)
  ANIME_IN_GENRES true to let anime (Japanese animation) appear in the other genres' rows too (default false:
                  anime lives in the Anime folder, Animated holds the rest, other genres skip it)
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
WORK = ROOT / "work"
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()
ROW_SIZE = int(os.environ.get("ROW_SIZE", "100"))
ANIME_IN_GENRES = os.environ.get("ANIME_IN_GENRES", "false").strip().lower() in ("1", "true", "yes")
TODAY = dt.datetime.now(dt.timezone.utc).date()
UA = "nuvio-genres-addon/1.0 (+https://github.com)"
TMDB = "https://api.themoviedb.org/3"
IMDB_DATASETS = "https://datasets.imdbws.com"
ADDON_ID = "community.nuviogenres"

csv.field_size_limit(10 ** 9)


def ids(*n: int) -> str:
    return "|".join(str(i) for i in n)


# ----------------------------------------------------------------------------------------------- genre table
# verified TMDB keyword ids (themoviedb.org/search/keyword) — recall helpers for TV genres TMDB cannot express
HORROR_TV_KW = ids(315058, 250593, 256183, 295907, 12339, 3358, 10224, 161261, 9712, 50009, 15032, 215959, 209568,
                   163053, 283085, 326396, 342626, 325319, 309060, 379671, 352581, 12377, 3133, 10541)
THRILLER_TV_KW = ids(316362, 12565, 355372, 217282, 209817, 288394, 319190, 10410, 298530, 5265, 470, 1562, 782, 13015,
                     226499, 302132, 254459, 298605, 10718, 10562, 2708, 3637, 4289, 383851, 332774, 1812, 272551,
                     14555, 209799, 9665, 235261, 14903, 4776, 2157, 209853, 10714, 366584, 381632, 1930, 6844, 1936)
SPORTS_KW = ids(6075, 333328, 167882, 300423, 294708, 367587, 209476, 579, 1480, 6483, 13042, 6496, 2070, 315138,
                233981, 242128, 268011, 298953, 6315, 206441, 331674, 7248, 274126, 215754, 8338, 7912, 650, 10671,
                7020, 166939, 11672, 342278, 201189, 169055)
ANIME_KW = ids(210024)

# TMDB genre ids
M = dict(action=28, adventure=12, animation=16, comedy=35, crime=80, drama=18, fantasy=14, horror=27, mystery=9648,
         scifi=878, thriller=53)
T = dict(action_adventure=10759, animation=16, comedy=35, crime=80, drama=18, mystery=9648, scifi_fantasy=10765)

NONFICTION_SERIES = {"Talk-Show", "Reality-TV", "Game-Show", "News"}

# key = genre option in the manifest, imdb = IMDb genre name, m/t = TMDB discover passes (each a dict of params)
# that build the candidate pools for Popular / New, floors = IMDb vote floors for Top Rated (movie, series)
GENRES = [
    dict(key="Action", imdb="Action", m=[dict(with_genres=M["action"])], t=[dict(with_genres=T["action_adventure"])],
         floors=(40000, 20000)),
    dict(key="Adventure", imdb="Adventure", m=[dict(with_genres=M["adventure"])],
         t=[dict(with_genres=T["action_adventure"])], floors=(40000, 20000)),
    dict(key="Animated", imdb="Animation", m=[dict(with_genres=M["animation"])], t=[dict(with_genres=T["animation"])],
         floors=(40000, 10000), lang="not-ja"),
    dict(key="Anime", imdb="Animation", m=[dict(with_genres=M["animation"], with_origin_country="JP")],
         t=[dict(with_genres=T["animation"], with_origin_country="JP"), dict(with_keywords=ANIME_KW)],
         floors=(15000, 10000), lang="ja"),
    dict(key="Comedy", imdb="Comedy", m=[dict(with_genres=M["comedy"])], t=[dict(with_genres=T["comedy"])],
         floors=(40000, 20000)),
    dict(key="Crime", imdb="Crime", m=[dict(with_genres=M["crime"])], t=[dict(with_genres=T["crime"])],
         floors=(40000, 20000)),
    dict(key="Drama", imdb="Drama", m=[dict(with_genres=M["drama"])], t=[dict(with_genres=T["drama"])],
         floors=(40000, 20000)),
    dict(key="Fantasy", imdb="Fantasy", m=[dict(with_genres=M["fantasy"])], t=[dict(with_genres=T["scifi_fantasy"])],
         floors=(40000, 10000)),
    dict(key="Horror", imdb="Horror", m=[dict(with_genres=M["horror"])],
         t=[dict(with_keywords=HORROR_TV_KW), dict(with_genres=ids(T["mystery"], T["scifi_fantasy"]))],
         floors=(40000, 10000)),
    dict(key="Mystery", imdb="Mystery", m=[dict(with_genres=M["mystery"])], t=[dict(with_genres=T["mystery"])],
         floors=(40000, 20000)),
    dict(key="Sci-Fi", imdb="Sci-Fi", m=[dict(with_genres=M["scifi"])], t=[dict(with_genres=T["scifi_fantasy"])],
         floors=(40000, 10000)),
    dict(key="Sports", imdb="Sport", m=[dict(with_keywords=SPORTS_KW)], t=[dict(with_keywords=SPORTS_KW)],
         floors=(8000, 2000), docs_ok=True),
    dict(key="Thrillers", imdb="Thriller", m=[dict(with_genres=M["thriller"])],
         t=[dict(with_keywords=THRILLER_TV_KW), dict(with_genres=ids(T["crime"], T["mystery"]))],
         floors=(40000, 10000)),
]
ROWS = [("popular", "Popular"), ("top-rated", "Top Rated"), ("new", "New"), ("essentials", "Essentials")]

# By Decade: pure IMDb (startYear). floors = (movie, series) vote floors — older decades have fewer voters.
DECADES = [
    dict(key="2020s", years=(2020, 2100), floors=(40000, 20000)),
    dict(key="2010s", years=(2010, 2019), floors=(40000, 20000)),
    dict(key="2000s", years=(2000, 2009), floors=(40000, 15000)),
    dict(key="1990s", years=(1990, 1999), floors=(40000, 10000)),
    dict(key="1980s", years=(1980, 1989), floors=(25000, 5000)),
    dict(key="1970s", years=(1970, 1979), floors=(20000, 3000)),
    dict(key="1960s", years=(1960, 1969), floors=(15000, 2000)),
    dict(key="1950s-and-before", years=(1800, 1959), floors=(10000, 1000)),
]

# Themes: candidates come from TMDB keyword discovery (IMDb publishes no keywords), ranking is IMDb's.
# kw = verified TMDB keyword ids (themoviedb.org/search/keyword); imdb = pure-IMDb rule instead of a TMDB pool.
KW = {
    "heist": ids(10051, 191845, 250043, 15363, 9728, 321964, 239663, 40903, 159413, 273612, 156031, 159747, 159859,
                 6168, 191604, 156236, 192261),
    "time_travel": ids(4379, 5455, 208757, 186760, 220800, 240807),
    "superhero": ids(9715, 155030, 180734, 272493, 265979, 158456),
    "zombies": ids(12377, 186565, 157351, 356202, 360949, 364488, 207891),
    "apocalypse": ids(12332, 4458, 359337, 384784, 10150, 34161, 677, 10689, 235668, 353811),
    "serial_killer": ids(10714, 227428, 14676, 12339),
    "whodunnit": ids(12570, 364800, 207046, 236429, 231596, 366584),
    "time_loop": ids(10854, 220046, 366519, 364334),
    "outer_space": ids(252634, 3801, 14626, 252937, 156039, 161176, 1612, 3388, 4040, 191132, 209280, 2902, 3386,
                       156836, 839, 7422, 12185, 226235, 195788, 260089),
    "mindfuck": ids(374835, 362567, 174089, 3972, 231173, 234868, 10597, 9887, 161891, 156761, 293381, 186789, 229787),
    "plot_twist": ids(275311, 373440, 326438, 335567, 331114, 12565, 12570, 364800, 207046, 184312, 381632, 174089),
    "true_crime": ids(33722, 9672, 307587, 379548, 379549),
}
THEMES = [
    dict(key="Mindfuck", kw=KW["mindfuck"], movie_genres=ids(878, 9648, 53, 18, 27), movies_only=True,
         floors=(10000, 5000)),
    dict(key="Plot-Twists", kw=KW["plot_twist"], movie_genres=ids(9648, 53, 80, 27, 18), tv_genres=ids(18, 9648, 80),
         floors=(10000, 5000)),
    dict(key="Heist", kw=KW["heist"], floors=(10000, 3000)),
    dict(key="Time-Travel", kw=KW["time_travel"], floors=(10000, 3000)),
    dict(key="Superhero", kw=KW["superhero"], floors=(10000, 5000)),
    dict(key="Zombies", kw=KW["zombies"], floors=(10000, 3000)),
    dict(key="Apocalypse", kw=KW["apocalypse"], floors=(10000, 3000)),
    dict(key="Serial-Killer", kw=KW["serial_killer"], floors=(10000, 3000)),
    dict(key="Whodunnit", kw=KW["whodunnit"], floors=(10000, 3000)),
    dict(key="Time-Loop", kw=KW["time_loop"], floors=(5000, 2000)),
    dict(key="Modern-Horror", imdb=dict(genre="Horror", years=(2000, 2100)), floors=(25000, 10000)),
    dict(key="Horror-Classics", imdb=dict(genre="Horror", years=(1800, 1999)), floors=(15000, 3000)),
    dict(key="Outer-Space", kw=KW["outer_space"], floors=(10000, 3000)),
    dict(key="True-Crime", kw=KW["true_crime"], movie_genres=ids(80, 99), tv_genres=ids(80, 99), docs_ok=True,
         floors=(10000, 3000)),
]
EXTRA_ROWS = [("top-rated", "Top Rated"), ("essentials", "Essentials")]
ESSENTIALS_MIN_RATING = 6.0
POPULAR_MIN_IMDB_VOTES = {"movie": 200, "series": 100}
NEW_MIN_IMDB_VOTES = {"movie": 200, "series": 100}       # "new" also means noticed: a few hundred IMDb voters
NEW_MAX_AGE_DAYS = 540


# ----------------------------------------------------------------------------------------------- http
def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(url: str):
    return json.loads(http_get(url).decode("utf-8"))


def tmdb(path: str, **params):
    """TMDB GET with 429 back-off; returns {} on a hard failure so one bad call never kills the build."""
    q = {"api_key": TMDB_KEY, **{k: v for k, v in params.items() if v is not None}}
    url = f"{TMDB}/{path}?{urllib.parse.urlencode(q)}"
    for attempt in range(5):
        try:
            return http_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After") or 2) + 0.5)
                continue
            if e.code == 404:
                return {}
            print(f"  ! tmdb {path}: HTTP {e.code}", file=sys.stderr)
            return {}
        except Exception as e:  # noqa: BLE001
            print(f"  ! tmdb {path}: {e}", file=sys.stderr)
            time.sleep(1 + attempt)
    return {}


# ----------------------------------------------------------------------------------------------- imdb datasets
def download(name: str) -> Path:
    WORK.mkdir(exist_ok=True)
    path = WORK / name
    if not path.exists() or (time.time() - path.stat().st_mtime) > 6 * 3600:
        print(f"downloading {name} …")
        path.write_bytes(http_get(f"{IMDB_DATASETS}/{name}", timeout=600))
    return path


def load_imdb() -> dict[str, dict]:
    """tconst -> {type, title, year, end, genres, rating, votes} for movies/series with >= 50 votes, non-adult."""
    ratings: dict[str, tuple[float, int]] = {}
    with gzip.open(download("title.ratings.tsv.gz"), "rt", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        next(reader)
        for tconst, rating, votes in reader:
            v = int(votes)
            if v >= 50:
                ratings[tconst] = (float(rating), v)
    titles: dict[str, dict] = {}
    with gzip.open(download("title.basics.tsv.gz"), "rt", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        next(reader)
        for row in reader:
            tconst = row[0]
            if tconst not in ratings or row[4] == "1":
                continue
            kind = {"movie": "movie", "tvSeries": "series", "tvMiniSeries": "series"}.get(row[1])
            if kind is None:
                continue
            rating, votes = ratings[tconst]
            titles[tconst] = {
                "type": kind, "title": row[2], "year": row[5] if row[5] != "\\N" else "",
                "end": row[6] if row[6] != "\\N" else "", "genres": row[8].split(",") if row[8] != "\\N" else [],
                "rating": rating, "votes": votes,
            }
    print(f"imdb: {len(titles):,} rated movies/series loaded")
    return titles


def release_info(rec: dict) -> str | None:
    if rec["type"] == "series" and rec["year"]:
        return f"{rec['year']}–{rec['end']}" if rec["end"] else f"{rec['year']}–"
    return rec["year"] or None


# ----------------------------------------------------------------------------------------------- tmdb cache
CACHE: dict = {"ext": {}, "find": {}}       # ext: "movie:123" -> imdb id ("" when none); find: imdb -> details


def load_cache() -> None:
    try:
        stamp = int(time.time())
        data = http_json(f"{SITE_URL}/cache.json?t={stamp}")
        if isinstance(data, dict) and "ext" in data and "find" in data:
            CACHE.update(data)
            print(f"cache: {len(CACHE['ext']):,} id mappings, {len(CACHE['find']):,} title records")
    except Exception as e:  # noqa: BLE001
        print(f"cache: starting empty ({e})")


def external_imdb(kind: str, tmdb_id: int) -> str:
    key = f"{'movie' if kind == 'movie' else 'tv'}:{tmdb_id}"
    if key not in CACHE["ext"]:
        data = tmdb(f"{'movie' if kind == 'movie' else 'tv'}/{tmdb_id}/external_ids")
        imdb = (data.get("imdb_id") or "").strip()
        CACHE["ext"][key] = imdb if imdb.startswith("tt") else ""
    return CACHE["ext"][key]


def find_details(kind: str, imdb: str) -> dict:
    """TMDB record for an IMDb id: poster, backdrop, overview, original language, title, date (cached)."""
    rec = CACHE["find"].get(imdb)
    if rec is None or rec.get("kind") != kind:
        data = tmdb(f"find/{imdb}", external_source="imdb_id") if TMDB_KEY else {}
        hits = data.get("movie_results" if kind == "movie" else "tv_results") or []
        h = hits[0] if hits else {}
        rec = {"kind": kind, "tmdb": h.get("id"), "poster": h.get("poster_path"), "backdrop": h.get("backdrop_path"),
               "overview": h.get("overview") or "", "lang": h.get("original_language") or "",
               "title": h.get("title") or h.get("name") or "",
               "date": h.get("release_date") or h.get("first_air_date") or "", "t": TODAY.isoformat()}
        CACHE["find"][imdb] = rec
    rec["t"] = TODAY.isoformat()
    return rec


def prune_cache() -> None:
    cutoff = (TODAY - dt.timedelta(days=120)).isoformat()
    for imdb in [k for k, v in CACHE["find"].items() if (v.get("t") or "") < cutoff]:
        del CACHE["find"][imdb]


# ----------------------------------------------------------------------------------------------- tmdb pools
def paged(kind: str, path: str, pages: int, **params) -> list[dict]:
    """Paged TMDB list (discover / trending) -> raw results in list order (dedup by id), released titles only."""
    out, seen = [], set()
    for page in range(1, pages + 1):
        data = tmdb(path, language="en-US", include_adult="false", page=page, **params)
        results = data.get("results") or []
        for r in results:
            date = (r.get("release_date") if kind == "movie" else r.get("first_air_date")) or ""
            if r.get("id") in seen or not date or date > TODAY.isoformat():
                continue
            seen.add(r["id"])
            r["_kind"] = kind
            r["_date"] = date
            out.append(r)
        if len(results) < 20 or page >= int(data.get("total_pages") or 1):
            break
    return out


def discover(kind: str, pages: int, **params) -> list[dict]:
    return paged(kind, f"discover/{'movie' if kind == 'movie' else 'tv'}", pages, **params)


def window_params(kind: str) -> dict:
    """Released within the last NEW_MAX_AGE_DAYS and already rated by some people on TMDB."""
    since = (TODAY - dt.timedelta(days=NEW_MAX_AGE_DAYS)).isoformat()
    if kind == "movie":
        return {"primary_release_date.lte": TODAY.isoformat(), "primary_release_date.gte": since, "vote_count.gte": 20}
    return {"first_air_date.lte": TODAY.isoformat(), "first_air_date.gte": since,
            "include_null_first_air_dates": "false", "vote_count.gte": 10}


def passes(kind: str, mode: str) -> list[tuple[int, dict]]:
    """(pages, discover params) for one pool. Popular = what people look at now; New = recent and notable."""
    if mode == "popular":
        return [(10, dict(sort_by="popularity.desc", **{"vote_count.gte": 50 if kind == "movie" else 20}))]
    w = window_params(kind)
    date_key = "primary_release_date.desc" if kind == "movie" else "first_air_date.desc"
    return [(6, dict(sort_by=date_key, **w)), (6, dict(sort_by="popularity.desc", **w))]


def build_pools() -> dict[tuple[str, str], list[dict]]:
    """(kind, mode) -> candidates with TMDB fields + imdb id.
    popular: TMDB trending-this-week (in rank order) + the popularity-sorted pool, globally and per genre.
    new:     newest releases in the window + the most popular releases in the window, globally and per genre."""
    pools: dict[tuple[str, str], dict[int, dict]] = {}
    for kind in ("movie", "series"):
        t = "movie" if kind == "movie" else "tv"
        for mode in ("popular", "new"):
            print(f"tmdb: {mode} {kind} pool …")
            bucket = pools.setdefault((kind, mode), {})
            jobs: list[tuple[int, dict]] = []
            if mode == "popular":
                for pos, r in enumerate(paged(kind, f"trending/{t}/week", 25)):
                    r["_trend"] = pos
                    bucket.setdefault(r["id"], r)
                jobs.append((50, passes(kind, "popular")[0][1]))
            else:
                jobs += [(40, p) for _n, p in passes(kind, "new")]
            for g in GENRES:
                for extra in g["m" if kind == "movie" else "t"]:
                    jobs += [(n, {**p, **extra}) for n, p in passes(kind, mode)]
            with ThreadPoolExecutor(max_workers=4) as ex:
                for results in ex.map(lambda job: discover(kind, job[0], **job[1]), jobs):
                    for r in results:
                        bucket.setdefault(r["id"], r)
    # resolve IMDb ids (threaded; cached across builds)
    todo = [(kind, r["id"]) for (kind, _m), b in pools.items() for r in b.values()]
    todo = list({(k, i) for k, i in todo if f"{'movie' if k == 'movie' else 'tv'}:{i}" not in CACHE["ext"]})
    print(f"tmdb: resolving {len(todo):,} new IMDb ids …")
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda ki: external_imdb(*ki), todo))
    out: dict[tuple[str, str], list[dict]] = {}
    for key, bucket in pools.items():
        items = []
        for r in bucket.values():
            imdb = external_imdb(key[0], r["id"])
            if imdb:
                r["_imdb"] = imdb
                items.append(r)
        out[key] = items
        print(f"  pool {key[1]:>7} {key[0]:>6}: {len(bucket):,} titles, {len(items):,} with IMDb ids")
    return out


# ----------------------------------------------------------------------------------------------- rows
def belongs(rec: dict, g: dict, kind: str) -> bool:
    if rec["type"] != kind or g["imdb"] not in rec["genres"]:
        return False
    if kind == "series" and NONFICTION_SERIES & set(rec["genres"]):
        return False
    if "Documentary" in rec["genres"] and not g.get("docs_ok"):
        return False
    return True


def lang_ok(g: dict, rec: dict, lang: str) -> bool:
    """Anime = Animation genre + Japanese original language. The Anime folder wants exactly that, Animated wants
    the rest, and every other genre leaves anime to the Anime folder unless ANIME_IN_GENRES is set."""
    if not TMDB_KEY:
        return True
    is_anime = lang == "ja" and "Animation" in rec["genres"]
    want = g.get("lang")
    if want == "ja":
        return is_anime
    if want == "not-ja":
        return not is_anime
    return ANIME_IN_GENRES or not is_anime


def meta_for(kind: str, imdb: str, rec: dict, tm: dict | None) -> dict:
    """Catalog item. tm = TMDB discover result (when we have one) else the cached find record."""
    poster = (tm or {}).get("poster_path") or (tm or {}).get("poster")
    backdrop = (tm or {}).get("backdrop_path") or (tm or {}).get("backdrop")
    overview = (tm or {}).get("overview") or ""
    name = (tm or {}).get("title") or (tm or {}).get("name") or rec["title"]
    return {
        "id": imdb, "type": kind, "name": name,
        "poster": f"https://image.tmdb.org/t/p/w500{poster}" if poster
        else f"https://images.metahub.space/poster/medium/{imdb}/img",
        "posterShape": "poster",
        "background": f"https://image.tmdb.org/t/p/w1280{backdrop}" if backdrop
        else f"https://images.metahub.space/background/medium/{imdb}/img",
        "description": overview,
        "releaseInfo": release_info(rec),
        "imdbRating": f"{rec['rating']:.1f}",
        "genres": rec["genres"],
    }


def rank_rows(kind: str, cands: list[tuple[str, dict]], floor: int, group: dict, mode: str) -> list[dict]:
    """Rank IMDb-backed candidates for one row.
    top-rated : IMDb's own chart formula — a Bayesian estimate that pulls a rating toward the group average until
                enough people have voted, so a 9.2 from 45k fan votes doesn't outrank an 8.6 from 750k — with a hard
                vote floor so only widely-seen titles qualify.
    essentials: the canon — the titles the most people have seen (IMDb vote count), rating >= 6.0."""
    cands = [(t, r) for t, r in cands if r["votes"] >= floor]
    if mode == "essentials":
        cands = [(t, r) for t, r in cands if r["rating"] >= ESSENTIALS_MIN_RATING]
        cands.sort(key=lambda x: (-x[1]["votes"], -x[1]["rating"]))
    else:
        if not cands:
            return []
        c_mean = sum(r["rating"] for _, r in cands) / len(cands)
        m = floor * 3

        def weighted(r: dict) -> float:
            v = r["votes"]
            return (v / (v + m)) * r["rating"] + (m / (v + m)) * c_mean

        cands.sort(key=lambda x: (-weighted(x[1]), -x[1]["votes"]))
    cands = cands[: int(ROW_SIZE * (2.5 if group.get("lang") else 1.5))]   # the anime split drops some candidates
    if TMDB_KEY:
        with ThreadPoolExecutor(max_workers=6) as ex:                       # warm the TMDB record cache
            list(ex.map(lambda tr: find_details(kind, tr[0]), [c for c in cands if c[0] not in CACHE["find"]]))
    out = []
    for t, r in cands:
        if len(out) >= ROW_SIZE:
            break
        d = find_details(kind, t)
        if not lang_ok(group, r, d["lang"]):
            continue
        out.append(meta_for(kind, t, r, d))
    return out


def row_top_rated(kind: str, g: dict, imdb: dict[str, dict]) -> list[dict]:
    floor = g["floors"][0 if kind == "movie" else 1]
    return rank_rows(kind, [(t, r) for t, r in imdb.items() if belongs(r, g, kind)], floor, g, "top-rated")


def row_essentials(kind: str, g: dict, imdb: dict[str, dict]) -> list[dict]:
    floor = g["floors"][0 if kind == "movie" else 1]
    return rank_rows(kind, [(t, r) for t, r in imdb.items() if belongs(r, g, kind)], floor, g, "essentials")


def row_from_pool(kind: str, g: dict, imdb: dict[str, dict], pool: list[dict], mode: str) -> list[dict]:
    floor = (POPULAR_MIN_IMDB_VOTES if mode == "popular" else NEW_MIN_IMDB_VOTES)[kind]
    keep = []
    for tm in pool:
        rec = imdb.get(tm["_imdb"])
        if not rec or rec["votes"] < floor or not belongs(rec, g, kind):
            continue
        if not lang_ok(g, rec, tm.get("original_language") or ""):
            continue
        keep.append((tm, rec))
    if mode == "popular":     # trending-this-week first (in TMDB's order), then everything else by popularity
        keep.sort(key=lambda x: (x[0].get("_trend", 10 ** 6), -(x[0].get("popularity") or 0)))
    else:
        keep.sort(key=lambda x: (x[0]["_date"], x[0].get("popularity") or 0), reverse=True)
    seen, out = set(), []
    for tm, rec in keep:
        if tm["_imdb"] in seen:
            continue
        seen.add(tm["_imdb"])
        out.append(meta_for(kind, tm["_imdb"], rec, tm))
        if len(out) >= ROW_SIZE:
            break
    return out


# ----------------------------------------------------------------------------------------------- decades / themes
def clean(rec: dict, kind: str, docs_ok: bool = False) -> bool:
    """The rules every row shares: right type, no nonfiction series, no documentaries (unless allowed)."""
    if rec["type"] != kind:
        return False
    if kind == "series" and NONFICTION_SERIES & set(rec["genres"]):
        return False
    if "Documentary" in rec["genres"] and not docs_ok:
        return False
    return True


def decade_cands(kind: str, dec: dict, imdb: dict[str, dict]) -> list[tuple[str, dict]]:
    lo, hi = dec["years"]
    out = []
    for t, r in imdb.items():
        if not r["year"].isdigit() or not (lo <= int(r["year"]) <= hi) or not clean(r, kind):
            continue
        out.append((t, r))
    return out


_theme_pools: dict[tuple[str, str], list[tuple[str, dict]]] = {}


def theme_cands(kind: str, th: dict, imdb: dict[str, dict]) -> list[tuple[str, dict]]:
    """Candidates for a theme: a pure-IMDb rule when one exists, otherwise TMDB keyword discovery (the most-voted
    and the most popular matches) joined to their IMDb records."""
    key = (kind, th["key"])
    if key in _theme_pools:
        return _theme_pools[key]
    docs_ok = th.get("docs_ok", False)
    if th.get("imdb"):
        lo, hi = th["imdb"]["years"]
        cands = [(t, r) for t, r in imdb.items()
                 if th["imdb"]["genre"] in r["genres"] and r["year"].isdigit() and lo <= int(r["year"]) <= hi
                 and clean(r, kind, docs_ok)]
    elif not TMDB_KEY:
        cands = []
    else:
        params = {"with_keywords": th["kw"]}
        genres = th.get("movie_genres" if kind == "movie" else "tv_genres")
        if genres:
            params["with_genres"] = genres
        raw = {}
        for sort_by, pages in (("vote_count.desc", 10), ("popularity.desc", 5)):
            for r in discover(kind, pages, sort_by=sort_by, **params):
                raw.setdefault(r["id"], r)
        todo = [(kind, i) for i in raw if f"{'movie' if kind == 'movie' else 'tv'}:{i}" not in CACHE["ext"]]
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(lambda ki: external_imdb(*ki), todo))
        cands, seen = [], set()
        for r in raw.values():
            imdb_id = external_imdb(kind, r["id"])
            rec = imdb.get(imdb_id)
            if not imdb_id or imdb_id in seen or not rec or not clean(rec, kind, docs_ok):
                continue
            seen.add(imdb_id)
            cands.append((imdb_id, rec))
    _theme_pools[key] = cands
    return cands


# ----------------------------------------------------------------------------------------------- build
def main() -> int:
    if not SITE_URL:
        print("SITE_URL is required", file=sys.stderr)
        return 2
    if not TMDB_KEY:
        print("TMDB_API_KEY not set: only the Top Rated rows will be built (metahub posters, no anime split)",
              file=sys.stderr)
    load_cache()
    imdb = load_imdb()
    pools = build_pools() if TMDB_KEY else {}

    for kind in ("movie", "series"):
        for row_id, _label in ROWS:
            (DIST / "catalog" / kind / row_id).mkdir(parents=True, exist_ok=True)
    summary: list[tuple[str, str, str, int, str]] = []
    for g in GENRES:
        for kind in ("movie", "series"):
            for row_id, label in ROWS:
                if row_id == "top-rated":
                    metas = row_top_rated(kind, g, imdb)
                elif row_id == "essentials":
                    metas = row_essentials(kind, g, imdb)
                elif TMDB_KEY:
                    metas = row_from_pool(kind, g, imdb, pools[(kind, row_id)], row_id)
                else:
                    continue
                path = DIST / "catalog" / kind / row_id / f"genre={g['key']}.json"
                path.write_text(json.dumps({"metas": metas}, ensure_ascii=False, indent=1), "utf-8")
                first = ", ".join(m["name"] for m in metas[:3])
                summary.append((g["key"], kind, label, len(metas), first))
                print(f"  {label:>9} {g['key']:<9} {kind:<6} {len(metas):>3}  {first}")
    for family, groups, cand_fn in (("decade", DECADES, decade_cands), ("theme", THEMES, theme_cands)):
        for kind in ("movie", "series"):
            for row_id, _label in EXTRA_ROWS:
                (DIST / "catalog" / kind / f"{family}-{row_id}").mkdir(parents=True, exist_ok=True)
        for grp in groups:
            for kind in ("movie", "series"):
                for row_id, label in EXTRA_ROWS:
                    path = DIST / "catalog" / kind / f"{family}-{row_id}" / f"genre={grp['key']}.json"
                    if kind == "series" and grp.get("movies_only"):
                        path.write_text(json.dumps({"metas": []}), "utf-8")
                        continue
                    floor = grp["floors"][0 if kind == "movie" else 1]
                    metas = rank_rows(kind, cand_fn(kind, grp, imdb), floor, grp, row_id)
                    path.write_text(json.dumps({"metas": metas}, ensure_ascii=False, indent=1), "utf-8")
                    first = ", ".join(m["name"] for m in metas[:3])
                    summary.append((grp["key"], kind, f"{family} {label}", len(metas), first))
                    print(f"  {family + ' ' + label:>17} {grp['key']:<17} {kind:<6} {len(metas):>3}  {first}")
    thin = [s for s in summary if s[3] < 20]
    if thin:
        print(f"  ! thin rows (<20 items): {[(s[0], s[1], s[2], s[3]) for s in thin]}", file=sys.stderr)

    prune_cache()
    (DIST / "cache.json").write_text(json.dumps(CACHE, separators=(",", ":")), "utf-8")
    options = [g["key"] for g in GENRES]
    now = dt.datetime.now(dt.timezone.utc)
    manifest = {
        "id": ADDON_ID,
        "version": f"1.{now.strftime('%Y%m%d')}.{now.strftime('%H%M')}",
        "name": "Genres · IMDb-verified",
        "description": "Popular, Top Rated, New and Essentials rows for 13 genres, movies and series. Membership comes from "
                       "IMDb's genre taxonomy (so Horror / Thriller / Sports series exist), Top Rated from IMDb "
                       "ratings with vote floors, Popular / New from TMDB. Built for Nuvio collections; the rows "
                       "stay off the Home page. Rebuilt daily. Information courtesy of IMDb (https://www.imdb.com). "
                       "Used with permission. This product uses the TMDB API but is not endorsed or certified by TMDB.",
        "logo": f"{SITE_URL}/logo.png",
        "resources": ["catalog"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [
            {"type": kind, "id": row_id, "name": f"{label} {'Movies' if kind == 'movie' else 'Series'}",
             "showInHome": False,
             "extra": [{"name": "genre", "isRequired": True, "options": options}],
             "extraRequired": ["genre"], "extraSupported": ["genre"]}
            for kind in ("movie", "series") for row_id, label in ROWS
        ] + [
            {"type": kind, "id": f"{family}-{row_id}",
             "name": f"{label} {'Movies' if kind == 'movie' else 'Series'} by {family.title()}",
             "showInHome": False,
             "extra": [{"name": "genre", "isRequired": True, "options": [g["key"] for g in groups]}],
             "extraRequired": ["genre"], "extraSupported": ["genre"]}
            for family, groups in (("decade", DECADES), ("theme", THEMES))
            for kind in ("movie", "series") for row_id, label in EXTRA_ROWS
        ],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    (DIST / ".nojekyll").write_text("")
    rows_html = "".join(
        f"<tr><td>{k}</td><td>{kind}</td><td>{label}</td><td style='text-align:right'>{n}</td>"
        f"<td style='color:#999'>{first}</td></tr>" for k, kind, label, n, first in summary)
    (DIST / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Genres · IMDb-verified — Nuvio addon</title>"
        "<style>td{padding:.2rem .6rem;border-bottom:1px solid #222;vertical-align:top}</style>"
        "<body style='font-family:system-ui;background:#111;color:#eee;padding:2rem;max-width:72rem'>"
        "<h1>Genres · IMDb-verified</h1><p>Install in Nuvio / Stremio with this manifest URL, then point your "
        "Genres collection folders at its rows:</p>"
        f"<p><code style='font-size:1.1rem'>{SITE_URL}/manifest.json</code></p>"
        f"<p>Built {now.strftime('%Y-%m-%d %H:%M UTC')} · {len(summary)} rows · "
        f"catalogs live at <code>catalog/&lt;movie|series&gt;/&lt;popular|top-rated|new|essentials&gt;/genre=&lt;Genre&gt;.json</code></p>"
        f"<table style='border-collapse:collapse;font-size:.9rem'>{rows_html}</table>"
        "<p style='color:#888;font-size:.9rem;margin-top:2rem'>Information courtesy of IMDb (https://www.imdb.com). "
        "Used with permission. · This product uses the TMDB API but is not endorsed or certified by TMDB. · "
        "Artwork via TMDB and metahub.</p></body>", "utf-8")
    print(f"done -> {DIST} ({manifest['version']}): {len(summary)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
