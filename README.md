# European Cup & Champions League — History Map

**🔗 [Live site](https://andre2694.github.io/ucl-history-map/) · [Repo](https://github.com/andre2694/ucl-history-map)**

An interactive map of every club that has ever entered the European Cup /
UEFA Champions League (1955–56 → present) — 628 clubs, from a single
qualifying-round exit to lifting the trophy. Gold marks a title win,
silver a final reached without winning; every other club is a single blue
hue whose intensity encodes how deep its best-ever run went — semifinal
(brightest) → quarterfinal → round of 16 → a faint tone for group
stage/qualifying. Click a club to drill into its full history — for the
42 clubs that have reached a final, that includes score, venue, goal
scorers split by team, and a link to the Wikipedia match report.

100% static: Leaflet.js via CDN, no build step, no server, no API keys.
Deployed straight from `main` via GitHub Pages.

## Status

| | |
|---|---|
| **v0 — finals map** | ✅ shipped. |
| **v1 — full participation history** | ✅ shipped. All 628 clubs that have ever entered, geocoded, color-coded by best-ever round. |
| **v1.1 — further name cleanup** | Ongoing, low-priority — a handful of spelling/transliteration variants (e.g. Ferencváros/Ferencvárosi) still show as separate markers for the same club. See [ROADMAP.md](ROADMAP.md). |

## Features

- **Map**: auto-fits to the marker bounds, single-hue gold gradient by
  best-ever round reached (see color key above), white ring for title
  winners. Markers render weakest-run-first so notable clubs stay
  clickable on top when several clubs from the same city overlap.
- **Drill-down panel**: click any marker for that club's complete
  season-by-season history. A final appearance shows score, venue,
  opponent, team-attributed goal scorers, and a Wikipedia match-report
  link; any other round shows a plain season + round label.
- **Goal scorers, attributed by team.** Parsed from RSSSF's Final-round
  text (already cached locally, no extra network calls) and validated by
  summing each side's attributed goals against the known scoreline for
  *all 71 finals*. See `extract_scorers()` in
  [`build_data.py`](scripts/build_data.py) for the parsing details (three
  different RSSSF bracket formats, own-goal handling, name-spelling drift
  like "Alenichev"/"Alenitchev").
- **Full match report link** to the relevant Wikipedia final article for
  lineups, cards, and more detail than we scrape ourselves.

## Project layout

```
data/
  finals_raw.json          — one row per final: season, winner, score, runner-up, venue, city
  club_coords.json         — manual lat/lon + country lookup for the 42 finalist clubs
  clubs.json               — GENERATED: rich per-final data (scores/scorers/wiki links) for the 42
  raw_html/                 — GENERATED: cached RSSSF season pages (committed, so we don't
                               re-hit their server on every run)
  participation_raw.json   — GENERATED: round-by-round participation, all 70 seasons
  clubs_full.json          — GENERATED: aggregated by best-ever round, ~870 raw club names
  clubs_dedup.json         — GENERATED: cleaned + deduplicated, ~725 clean clusters
  club_country_hints.json  — GENERATED: country per club, mined from cached RSSSF pages
  geocode_cache.json       — GENERATED: geocoding results (Wikidata/Nominatim/manual), resumable cache
  clubs_final.json         — GENERATED: what the map actually reads — 628 clubs, all with coordinates
scripts/
  build_data.py             — finals_raw.json + club_coords.json + RSSSF final-round text
                              (scorers) -> clubs.json
  scrape_rsssf.py           — RSSSF season pages -> participation_raw.json
  build_full_data.py        — participation_raw.json -> clubs_full.json
  dedupe_full_data.py       — clubs_full.json -> clubs_dedup.json (cleanup + dedup)
  extract_country_hints.py  — cached RSSSF pages -> club_country_hints.json
  build_wikidata_index.py   — Wikidata SPARQL -> wikidata_club_index.json (bulk, ~24k clubs)
  match_clubs_to_index.py   — matches clubs_dedup.json against that index, locally
  geocode_clubs.py          — per-club Wikidata/Nominatim API fallback for what's left
  apply_geocoding.py        — merges geocode_cache.json into clubs_dedup.json -> clubs_final.json
index.html                  — the map
```

## Running locally

Browsers block `fetch()` on `file://` pages, so serve the folder instead of
double-clicking `index.html`:

```bash
python -m http.server 8000
```

Then open http://localhost:8000.

## Regenerating the data

```bash
python scripts/build_data.py             # rich finals data (scores + scorers) -> clubs.json
python scripts/scrape_rsssf.py           # refresh full participation history (~70s, hits rsssf.org)
python scripts/build_full_data.py        # aggregate participation_raw.json -> clubs_full.json
python scripts/dedupe_full_data.py       # clean + dedupe -> clubs_dedup.json
python scripts/extract_country_hints.py  # mine country hints from cached pages
python scripts/build_wikidata_index.py   # bulk SPARQL pull (~5 min, only needed occasionally)
python scripts/match_clubs_to_index.py   # match locally against that index (seconds)
python scripts/geocode_clubs.py          # per-club API fallback for whatever didn't match
python scripts/apply_geocoding.py        # merge everything -> clubs_final.json (what the map reads)
```

### Why geocoding is split into three steps

The obvious approach — ask Wikidata's web API about each club in turn —
needs 3–6 sequential round trips per club (fuzzy search, up to two
fallback searches, check the club for coordinates, fetch its venue, fetch
the venue's coordinates), each followed by a courtesy delay, plus
exponential backoff whenever Wikidata rate-limits. At ~800 clubs that ran
for *hours*.

`build_wikidata_index.py` inverts it: one SPARQL query per country returns
every football club there **with** coordinates and English aliases, so the
whole ~24k-club candidate set for Europe downloads in about five minutes.
`match_clubs_to_index.py` then matches our clubs against it locally, in
seconds, and only what it can't confidently match falls through to the
slow per-club path. In practice that's ~20% of the list rather than 100%.

Matching is deliberately conservative — a wrong coordinate is worse than a
missing one — and was validated by re-deriving clubs the slow path had
already resolved and comparing: **132/132 agreed, 0 disagreements.**
Getting there caught several classes of false match worth knowing about if
you touch that code: a generic alias swallowing every club sharing the
word (`Dynamo` → *Dynamo Kyiv* matched *Dynamo Rostov*), a nickname alias
crossing countries (*Hungerford Town*'s nickname is "Crusaders", which
outranked Belfast's actual **Crusaders F.C.**), and dotted acronyms
tokenizing wrong so `Crusaders F.C.` never reduced to `crusaders`.

Both geocoding steps write to the same resumable `geocode_cache.json`
keyed by club name, so re-running after a data refresh only processes
names that are new or changed.

## Data sources & attribution

- **[RSSSF](https://www.rsssf.org/)** — the only source with full round-by-round
  participation history back to 1955. A small volunteer-run archive; we
  cache every page we fetch (`data/raw_html/`) rather than re-scraping it
  on every run.
- **[Wikipedia](https://en.wikipedia.org/wiki/List_of_European_Cup_and_UEFA_Champions_League_finals)**
  — the finals list, cross-checked against news for the most recent season,
  and linked per-final for match reports.
- **[Wikidata](https://www.wikidata.org/)** — entity search for geocoding
  (see `geocode_clubs.py` for why a plain address geocoder doesn't work
  for club names).
- No official UEFA API exists for this kind of historical data (see
  [ROADMAP.md](ROADMAP.md) for what was actually available).

## Data notes / caveats

- Coordinates are city/venue-level, not survey-grade — good enough for a
  continental map. 24 clubs (mostly lower-profile ones automated
  geocoding couldn't resolve) have hand-verified coordinates, tagged
  `"source": "manual"` in `geocode_cache.json`.
- "Milan" and "Inter Milan" are kept as separate clubs (they are).
- The 1973–74 final (Bayern Munich vs Atlético Madrid) was drawn 1–1 and
  replayed; the replay score is folded into one record for simplicity.
- A handful of clubs still show as duplicate markers under different
  name spellings (see the v1.1 status row above) — known, low-priority.
