# Roadmap

## v0 (done)
- [x] Repo scaffold, data schema, build pipeline
- [x] Finals dataset (winners/runners-up, 1955–56 → 2025–26), 42 clubs
- [x] Static Leaflet map on GitHub Pages, color by best result, click drill-down

## v1 — full participation history (the "smart drill-down")

**Status: scraping + aggregation done, geocoding not started.**

- [x] `scripts/scrape_rsssf.py` pulls round-by-round results for all 70
  completed seasons (1955–56 → 2024–25) from [RSSSF](https://www.rsssf.org/ec/),
  the only source with full-depth historical participation. Handles RSSSF's
  encoding drift (ISO-8859-1 vs UTF-16), inconsistent anchor tags, and
  round-name variation across 70 years by tagging each round with its
  *distance from the Final* (always the last round on the page) rather than
  trying to enumerate every historical label.
- [x] Validated: cross-checked every scraped Final's two clubs against the
  hand-verified `finals_raw.json` for all 71 seasons — **zero actual parsing
  errors**. Every apparent mismatch was a legitimate RSSSF-vs-Wikipedia
  spelling variant (Internazionale/Inter Milan, Bayern München/Bayern
  Munich, etc.), now canonicalized in `scripts/build_full_data.py` for the
  42 clubs we already track.
- [x] `data/clubs_full.json` — 907 unique club name-entries aggregated by
  best-ever round (Final/Semifinal/Quarterfinal/Round of 16/earlier) across
  all 70 seasons. Real club count is lower than 907: the long tail of
  clubs that only ever reached early rounds isn't name-canonicalized yet,
  so some are fragmented across spelling variants across decades — see
  v1.1 below.
- [ ] **Geocoding.** Only the 42 original finalists have coordinates
  (hand-curated). The other ~869 need a real geocoding pass — free option
  is OpenStreetMap Nominatim, but its usage policy caps requests at 1/sec,
  so ~869 clubs is ~15 minutes of rate-limited calls, and needs each club's
  home *city* (not always obvious from the RSSSF name alone — e.g.
  "Partizan" doesn't say Belgrade). This is a distinct, substantial chunk
  of work — do it as its own pass rather than folding into general cleanup.
- [ ] Map UI: swap binary gold/silver for a 4–5 tier color scale (winner →
  gold, finalist → silver, semifinal → bronze, QF/R16 → muted blue, group/
  qualifying → grey), plus a legend filter to toggle tiers on/off — needed
  once the marker count goes from 42 to ~900.

## v1.1 — name cleanup (found while building v1)
RSSSF spells the same club differently across decades (prefixes like "AC"/
"FC"/"SL", parenthetical city suffixes, transliteration drift for East
European clubs in particular). `ALIASES` in `build_full_data.py` currently
only covers the 42 known finalists. Extending it to the full ~900-name set
would need either a much bigger manual alias table or a fuzzy-matching pass
(e.g. normalize accents/prefixes, then cluster near-duplicates) — worth
doing before the v1 map ships broadly, since duplicate markers for the same
club look like a bug.

## v2 — polish
- Real club crests (need a licensing-safe source or simple generated badges)
- Search/filter by country or era
- Per-season "time slider" to replay history season by season (this pairs
  well with billsportsmaps-style single-season views)
- Scheduled GitHub Action to append each season's final automatically once
  it's played

## Non-goals (for now)
- Live/in-progress season scores — this project is about historical
  aggregation, not a live scoreboard. No backend needed as long as that
  holds.
