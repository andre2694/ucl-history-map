# Roadmap

## v0 (done)
- [x] Repo scaffold, data schema, build pipeline
- [x] Finals dataset (winners/runners-up, 1955–56 → 2025–26), 42 clubs
- [x] Static Leaflet map on GitHub Pages, color by best result, click drill-down

## v1 — full participation history (the "smart drill-down")

**Status: data pipeline 100% done (628/628 clubs geocoded). Not wired into the map UI yet.**

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
- [x] **Name cleanup** (`scripts/dedupe_full_data.py`). The raw 899 names
  included ~57 parser artifacts (score fragments, footnote text, split-off
  city names) and ~100 duplicate spelling/transliteration variants (AEK
  Athens alone had 6). Clustered by a normalized (core name, city) key —
  city normalization uses a small verified alias table rather than
  aggressive fuzzy matching, since a bare name like "Partizan" or "Dynamo"
  is shared by genuinely different real clubs and the city is often the
  only disambiguator. → `data/clubs_dedup.json`, 745 clean clusters.
- [x] **Country hints** (`scripts/extract_country_hints.py`). Mined the
  country codes RSSSF attaches to knockout-round matches — already cached
  locally, no new network calls — to disambiguate geocoder queries
  ("Sparta, Netherlands" vs. just "Sparta"). 96% coverage.
- [x] **Geocoding** (`scripts/geocode_clubs.py` + `apply_geocoding.py`).
  First attempt used Nominatim (OSM's address geocoder) directly on club
  names — unreliable, since club abbreviations usually aren't real place
  names ("AEK" matched a random hamlet in the Netherlands, "AB" matched
  the Canadian province of Alberta). Replaced with **Wikidata entity
  search**: match the club as an actual entity (this also correctly
  resolves historical renames, e.g. "17 Nentori" → modern "KF Tirana"),
  then follow its home venue/headquarters to get coordinates. Nominatim
  survives only as a last-resort fallback searching a city name, never a
  club abbreviation.
- [x] **Final cleanup.** Found and fixed a real bug along the way: the
  country-code regex's permissive fallback was matching "Kos" (Kosovo)
  inside "Olympia**kos**", silently truncating every Olympiakos appearance
  for as long as the scraper existed. Also dropped 18 confirmed parser
  artifacts (traced to one RSSSF footnote), aliased 3 duplicate spelling
  variants, and hand-added coordinates for 24 real clubs geocoding
  couldn't resolve (Basel, Donetsk, several Yerevan/Minsk/Almaty-area
  clubs, San Marino sides) — clearly tagged `source: manual`.
  → `data/clubs_final.json`, **628/628 clubs geocoded (100%)**, up from
  the original 42.
- [ ] **Map UI.** `clubs_final.json` isn't wired into `index.html` yet.
  Needs: swap binary gold/silver for a multi-tier color scale (winner →
  gold, finalist → silver, semifinal → bronze, QF/R16 → muted blue, group/
  qualifying → grey) plus a legend filter to toggle tiers, since the
  marker count goes from 42 to ~600. Also needs a decision on how to merge
  this with the finals-only `clubs.json` (which has the rich per-final
  data — scores, scorers, wiki links — that `clubs_final.json` doesn't).

## v2 — migrate the data backbone to Wikipedia (in progress)

**Why.** Nearly every bug this project has hit came from one root cause:
inferring what a round *meant* from its position, or from loose pattern
matching over RSSSF's hand-typed text. A partial list — Víkingur's first
round reported as a quarterfinal, HB Tórshavn's preliminary
mini-tournament read as the real semifinal, Sheriff Tiraspol's group
stage vanishing on a regex collision, the 2024-25 "League Stage" and
1992-93 "UEFA Champions' League Group A" headings going unrecognised —
plus a whole separate family of club-name bugs (Deportivo, Benfica,
Anderlecht, Olympiakos, CSKA's former names, a "ĳ" ligature). Each fix
was sound; the *approach* keeps generating them.

**The change.** Wikipedia has an article for every season back to
1955-56, and two properties that remove the guesswork:
  - rounds are named in section headings, so a round is whatever
    Wikipedia says it is — no positional inference;
  - club cells are LINKS to canonical articles ("Arsenal F.C.", "FC
    Bayern Munich"), which is a stable identity rather than free text.
    That alone should eliminate most name-fragmentation bugs, and those
    titles map to Wikidata QIDs, making geocoding exact instead of
    fuzzy-matched.

**Design (per the "hardcode the eras" call).** An explicit era table
mapping each era's ordered round sequence and flagging which of those
rounds are qualification — no generic shape-inference across formats:

| Era | Round sequence (qualification marked *) |
|---|---|
| 1955-56 → 1991-92 | Preliminary* → First round → Second round → QF → SF → Final |
| 1992-93 → 2002-03 | Qualifying* → group stage(s) → (QF) → SF → Final |
| 2003-04 → 2023-24 | Qualifying/play-off* → group → R16 → QF → SF → Final |
| 2024-25 → | Qualifying/play-off* → league phase → KO play-off → R16 → QF → SF → Final |

Those boundaries are to be **derived empirically**, not from memory:
`scripts/wiki_fetch_sections.py` caches every season's section headings
so the table can be written against what the articles actually contain.

- [x] `wiki_fetch_sections.py` — cache all season section headings
- [ ] Read the cached headings; write the era/round table
- [ ] Season scraper driven by that table (supersedes the WIP
      `scrape_wikipedia_season.py`, which still infers a single generic
      shape and is not wired in)
- [ ] Use club article titles as identity; resolve to Wikidata QIDs
- [ ] Re-run geocoding against QIDs (exact, no fuzzy matching)
- [ ] Validate against the RSSSF pipeline, which is **kept as a
      cross-check oracle** — the finals are independently known, so any
      disagreement is a bug in one of them

Note: Wikipedia rate-limits (HTTP 429), so every step needs the same
local caching the RSSSF scraper uses.

## v3 — polish
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
