# European Cup & Champions League — History Map

An interactive map of every club that has reached a European Cup / UEFA
Champions League final (1955–56 → present), color-coded by best-ever result,
with a click-to-drill-down panel showing each club's full run of finals.

**Live demo:** deploy via GitHub Pages (Settings → Pages → Deploy from branch
`main`, folder `/ (root)`) — the whole thing is static, no build step, no
server.

## Status: v0 scaffold

This first pass covers **finals only** (winners & runners-up), pulled from
Wikipedia's [List of European Cup and UEFA Champions League finals](https://en.wikipedia.org/wiki/List_of_European_Cup_and_UEFA_Champions_League_finals)
and cross-checked against recent news for the 2025–26 season. It's enough to
answer "who has won it / been in a final" for all 42 clubs that have ever
reached one.

It does **not** yet include every club that merely *participated* (group
stage / early rounds) — that needs a richer source. See [ROADMAP.md](ROADMAP.md).

## Project layout

```
data/
  finals_raw.json     — one row per final: season, winner, score, runner-up, venue, city
  club_coords.json    — manual lat/lon + country lookup per club (v0; swap for geocoding API later)
  clubs.json          — GENERATED: per-club aggregation (titles, runner-ups, appearances)
scripts/
  build_data.py        — finals_raw.json + club_coords.json -> clubs.json
index.html              — the map (Leaflet.js via CDN, no build step)
```

## Regenerating the data

```bash
python scripts/build_data.py
```

Re-run this any time `data/finals_raw.json` or `data/club_coords.json` changes.

## Running locally

Browsers block `fetch()` on `file://` pages, so serve the folder instead of
double-clicking `index.html`:

```bash
python -m http.server 8000
```

Then open http://localhost:8000.

## Data notes / caveats

- Coordinates in `club_coords.json` are hand-curated (home stadium, city-level
  precision) — good enough for a continental map, not survey-grade.
- "Milan" and "Inter Milan" are kept as separate clubs (they are).
- The 1973–74 final (Bayern Munich vs Atlético Madrid) was drawn 1–1 and
  replayed; the replay score is folded into one record for simplicity.
- No official UEFA API exists for this kind of historical data — see
  ROADMAP.md for the sourcing plan for full participation history.
