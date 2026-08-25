# Roadmap

## v0 (done)
- [x] Repo scaffold, data schema, build pipeline
- [x] Finals dataset (winners/runners-up, 1955–56 → 2025–26), 42 clubs
- [x] Static Leaflet map on GitHub Pages, color by best result, click drill-down

## v1 — full participation history (the "smart drill-down")
The interesting version of this project is every club that ever *entered*,
not just the ~42 that reached a final. That needs round-by-round data per
season, which isn't on the finals table.

- **Source:** [RSSSF](https://www.rsssf.org/) has the most complete
  round-by-round archive of the competition back to 1955 (every entrant,
  every round, every score) — but it's plain HTML per season, not an API, so
  this means writing a scraper (season page → round → clubs) and normalizing
  club names (RSSSF's naming drifts across decades vs. modern club names).
- Cross-check against Wikipedia's per-season articles (e.g. "2005–06 UEFA
  Champions League") for the modern era, where the tables are cleaner.
- Extend `clubs.json` schema: instead of just `appearances: [finals]`, add
  `bestRound` per season (e.g. "Round of 16", "Group Stage", "Winner") so a
  club's marker can reflect furthest-ever progress even if they never reached
  a final.
- Map UI: color scale instead of binary gold/silver (e.g. winner → gold,
  finalist → silver, semifinal → bronze, earlier rounds → muted blue), plus a
  filter/legend to toggle rounds on and off.

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
