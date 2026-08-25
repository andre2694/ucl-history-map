# European Cup & Champions League — History Map

**🔗 [Live site](https://andre2694.github.io/ucl-history-map/) · [Repo](https://github.com/andre2694/ucl-history-map)**

An interactive map of every club that has reached a European Cup / UEFA
Champions League final (1955–56 → present), color-coded by best-ever result.
Click a club to drill into its full run of finals — score, venue, goal
scorers split by team, and a link to the Wikipedia match report.

100% static: Leaflet.js via CDN, no build step, no server, no API keys.
Deployed straight from `main` via GitHub Pages.

## Status

| | |
|---|---|
| **v0 — finals map** | ✅ shipped. All 42 clubs that have ever reached a final, gold/silver by best result. |
| **v1 — full participation scrape** | ✅ data ready ([`clubs_full.json`](data/clubs_full.json), 899 club names across all 70 completed seasons), ⏳ not wired into the map yet. |
| **v1 — geocoding the other ~860 clubs** | ⏸️ parked. Only the 42 finalists have coordinates so far — see [ROADMAP.md](ROADMAP.md). |

## Features

- **Map**: auto-fits to the marker bounds (no hardcoded center/zoom), gold
  markers for clubs that have won it, silver for finalist-only clubs.
- **Drill-down panel**: click any marker for that club's complete run of
  finals — season, score, venue, opponent.
- **Goal scorers, attributed by team.** Parsed from RSSSF's Final-round
  text (already cached locally, no extra network calls) and validated by
  summing each side's attributed goals against the known scoreline for
  *all 71 finals* — a mismatch drops the team split for that one final
  rather than risk showing a wrong label. See `extract_scorers()` in
  [`build_data.py`](scripts/build_data.py) for the parsing details (three
  different RSSSF bracket formats, own-goal handling, name-spelling drift
  like "Alenichev"/"Alenitchev").
- **Full match report link** to the relevant Wikipedia final article for
  lineups, cards, and more detail than we scrape ourselves.

## Project layout

```
data/
  finals_raw.json         — one row per final: season, winner, score, runner-up, venue, city
  club_coords.json        — manual lat/lon + country lookup for the 42 finalist clubs
  clubs.json              — GENERATED: what the map actually reads (finals-only, v0)
  raw_html/                — GENERATED: cached RSSSF season pages (committed, so we don't
                              re-hit their server on every run)
  participation_raw.json  — GENERATED: round-by-round participation, all 70 seasons (v1, not
                              yet wired into the map — see ROADMAP.md)
  clubs_full.json         — GENERATED: v1 aggregated by best-ever round, 899 club names,
                              only 38 have coordinates so far
scripts/
  build_data.py            — finals_raw.json + club_coords.json + RSSSF final-round text
                              (scorers) -> clubs.json
  scrape_rsssf.py          — RSSSF season pages -> participation_raw.json
  build_full_data.py       — participation_raw.json -> clubs_full.json
index.html                 — the map
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
python scripts/build_data.py        # what the live map reads (finals + scorers)
python scripts/scrape_rsssf.py      # refresh full participation history (v1, ~70s, hits rsssf.org)
python scripts/build_full_data.py   # aggregate it (depends on scrape_rsssf.py's output)
```

Re-run `build_data.py` any time `data/finals_raw.json` or `data/club_coords.json` changes.

## Data sources & attribution

- **[RSSSF](https://www.rsssf.org/)** — the only source with full round-by-round
  participation history back to 1955. A small volunteer-run archive; we
  cache every page we fetch (`data/raw_html/`) rather than re-scraping it
  on every run.
- **[Wikipedia](https://en.wikipedia.org/wiki/List_of_European_Cup_and_UEFA_Champions_League_finals)**
  — the finals list, cross-checked against news for the most recent season,
  and linked per-final for match reports.
- No official UEFA API exists for this kind of historical data (see
  [ROADMAP.md](ROADMAP.md) for what was actually available).

## Data notes / caveats

- Coordinates in `club_coords.json` are hand-curated (home stadium,
  city-level precision) — good enough for a continental map, not
  survey-grade.
- "Milan" and "Inter Milan" are kept as separate clubs (they are).
- The 1973–74 final (Bayern Munich vs Atlético Madrid) was drawn 1–1 and
  replayed; the replay score is folded into one record for simplicity.
