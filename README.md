# Genres · IMDb-verified — Nuvio / Stremio catalog addon

Popular, Top Rated and New rows for 13 genres, movies and series — 78 catalogs — built for the **Genres**
collection in Nuvio. Rebuilt daily by GitHub Actions and served from GitHub Pages; no server.

## Why this exists

TMDB's TV taxonomy has no Horror, Thriller or Sport genre, and its keyword tags are noisy. A TMDB-only
"Top Rated Horror Series" row therefore fills up with anime and anthology shows that are not horror at all,
and "Top Rated" sorted by TMDB votes isn't really top rated.

This addon fixes both at the root:

| Row | How it is built |
|---|---|
| **Top Rated** | from IMDb's daily dataset: every title whose IMDb genres include the genre, with a vote floor (40k movies / 20k series; lower for small genres), ranked by IMDb's own chart formula — a weighted rating that pulls a score toward the genre average until enough people have voted, so a 9.2 from 45k fan votes doesn't outrank an 8.6 from 750k |
| **Popular** | TMDB's "popular right now" pools (global + per genre), kept only to titles whose **IMDb** genres include the genre |
| **New** | TMDB's newest releases of the last 18 months that people have already rated, same IMDb check, newest first |

Membership always comes from IMDb's genre taxonomy, which is complete for movies *and* series. TMDB is used
for what is popular / new, for artwork and descriptions, and for original language.

Rules applied everywhere:
- series rows drop talk shows, reality, game shows and news; documentaries are excluded from every genre except Sports
- adult titles are excluded; only `movie`, `tvSeries` and `tvMiniSeries` qualify (no TV movies, shorts or episodes)
- **Anime** = Animation + Japanese original language. The Anime folder gets exactly that, **Animated** gets the rest,
  and the other genres leave anime to the Anime folder (set the `ANIME_IN_GENRES` variable to `true` to change that)

## Install

1. Nuvio → Settings → Addons → add `https://<your-github-user>.github.io/<this-repo>/manifest.json`.
2. Import the Genres collection JSON whose folders point at this addon (`addonId: community.nuviogenres`,
   `catalogId: popular | top-rated | new`, `genre: <Genre>`).

The catalogs are **hidden from the Home page** on purpose (`showInHome: false` plus a required `genre` extra), so
installing the addon adds nothing to Home — the rows appear only inside the collection folders. In Nuvio's
collection editor the three catalogs show up with a genre picker, so you can also use them in your own folders.

Genres: Action, Adventure, Animated, Anime, Comedy, Crime, Drama, Fantasy, Horror, Mystery, Sci-Fi, Sports, Thrillers.
Catalog URLs: `catalog/<movie|series>/<popular|top-rated|new>/genre=<Genre>.json`.

## Deploy your own copy

1. Create a new **public** repository, push these files to `main`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Settings → Secrets and variables → Actions → New repository secret `TMDB_API_KEY` (free key from
   themoviedb.org/settings/api). Without it only the Top Rated rows are built, with metahub posters and no anime split.
4. Actions → "Build & publish Genres addon" → Run workflow. The first build takes a few minutes (it resolves
   several thousand TMDB ↔ IMDb ids once and caches them on the site); later builds are quick.

Optional variables: `ROW_SIZE` (default 100), `ANIME_IN_GENRES` (default `false`), `SITE_URL` (custom domain).

## Run locally

```
SITE_URL=https://you.github.io/nuvio-genres-addon TMDB_API_KEY=... python build.py
```

Downloads IMDb's `title.basics` and `title.ratings` datasets into `work/` (≈240 MB), writes `dist/`.

## Credits

Information courtesy of IMDb (https://www.imdb.com). Used with permission. · This product uses the TMDB API but
is not endorsed or certified by TMDB. · Artwork via TMDB and metahub (Stremio).
