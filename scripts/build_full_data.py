"""
Aggregates data/participation_raw.json (RSSSF, round-by-round, v1) into
data/clubs_full.json: one record per club with their best-ever round across
ALL 70 scraped seasons (not just finals).

Validation of the scraper (see scripts/scrape_rsssf.py's docstring) found
zero actual parsing errors -- every apparent mismatch against the trusted
finals_raw.json was a legitimate spelling variant (RSSSF vs Wikipedia
naming: "Internazionale" vs "Inter Milan", "Bayern München" vs "Bayern
Munich", etc). ALIASES below canonicalizes those for the 42 clubs we
already track (and have coordinates for, via club_coords.json). The long
tail of clubs that only ever reached early rounds is NOT canonicalized here
-- see ROADMAP.md v1.1 for that follow-up, and note some of those will
still be fragmented across name variants across different decades.

Run:  python scripts/build_full_data.py
"""
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DIST_LABELS = {0: "Final", 1: "Semifinal", 2: "Quarterfinal", 3: "Round of 16"}

# RSSSF spelling/variant -> canonical name (matching data/club_coords.json)
ALIASES = {
    "Internazionale": "Inter Milan",
    "Inter": "Inter Milan",
    "SL Benfica": "Benfica",
    "Atletico Madrid": "Atlético Madrid",
    "AS Saint-Etienne": "Saint-Étienne",
    "Saint Etienne": "Saint-Étienne",
    "Bayern München": "Bayern Munich",
    "Borussia Monchengladbach": "Borussia Mönchengladbach",
    "Malmo FF": "Malmö FF",
    "FC Barcelona": "Barcelona",
    "Steaua Bucuresti": "Steaua București",
    "Steaua Bucharest": "Steaua București",
    "PSV (Eindhoven)": "PSV Eindhoven",
    "AC Milan": "Milan",
    "Milan AC": "Milan",
    "Olympique Marseille": "Marseille",
    "Red Star (Belgrade)": "Red Star Belgrade",
    "Valencia CF": "Valencia",
    "Paris Saint-Germain FC": "Paris Saint-Germain",
    "Club Brugge KV": "Club Brugge",
    "FC Porto": "Porto",
    "Ajax Amsterdam": "Ajax",
    "Feyenoord Rotterdam": "Feyenoord",
    "Sporting CP": "Sporting Lisbon",
    "Stade de Reims": "Reims",
    "UC Sampdoria": "Sampdoria",
    "Sampdoria UC": "Sampdoria",
    # spelling variants that escaped dedupe_full_data.py's clustering (a
    # digraph difference and a typo, respectively) but are unambiguously
    # the same club as one that already geocoded successfully
    "Djurgaardens IF": "Djurgardens IF",
    "Deportivo La Coruña": "Deportivo (La Coruña)",
    # NOTE: "AEK Larnaka" and "CSKA Moscow" are deliberately NOT aliased to
    # "AEK (Athinai)" / "CSKA (Sofia)" despite the shared prefix -- they are
    # genuinely different clubs (AEK is a common Greek-diaspora founding
    # name reused across cities; CSKA is a Soviet-bloc army-club naming
    # convention reused across countries). Found while scanning for this
    # exact "name-without-parens" duplication pattern -- worth the note so
    # a future cleanup pass doesn't merge them by mistake.
    "Anorthosis Famagusta": "Anorthosis (Famagusta)",
    "Anorthosis of Ammóchostas": "Anorthosis (Famagusta)",
    "Anorthosis of Ammóchostos": "Anorthosis (Famagusta)",
    "APOEL Lefkosia": "APOEL (Lefkosia)",
    "Apollon Lemesos": "Apollon (Limassol)",
    "Omonia Nicosia": "Omonia (Lefkosia)",
    "Servette FC Geneva": "Servette FC (Genève)",
    "Sutjeska Niksic": "Sutjeska (Niksic)",
    "Ferncvárosi TC": "Ferencvárosi TC",
    "Omonia Lefkosia": "Omonia (Lefkosia)",
}

# Parser artifacts that survived dedupe_full_data.py's looks_bogus() filter
# -- all traced to one RSSSF footnote describing a Panathinaikos
# crowd-trouble incident ("...having been hit by a beer can...") that got
# fragmented across several false "club name" extractions. Not clubs.
KNOWN_GARBAGE = {
    "Champions' League ma", "Cyprus, 4 years ago)", "Meier; France",
    "Sep 13: Dinamo Kyiv v. Panathinai", "The group winners",
    "[Banel Nicol", "[referees:", "abando", "at home to Panathinai",
    "by an object thrown f", "having been hit by a beer can. The ma",
    "home ma", "the crowd; ma", "the stands; ma", "the two b",
    "with Panathinai", "union", "x ma",
}


def canonical(name: str) -> str:
    name = name.strip()
    return ALIASES.get(name, name)


def main():
    participation = json.loads((DATA / "participation_raw.json").read_text(encoding="utf-8"))
    try:
        coords = json.loads((DATA / "club_coords.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        coords = {}

    # keyed by season (not a plain list) so that two raw-name spellings of
    # the same club aliasing together -- including RSSSF switching spelling
    # *mid-season* between its own round sections, seen with Deportivo (La
    # Coruña)/Deportivo La Coruña disagreeing on 2000-01 -- collapse to one
    # entry per season, keeping whichever reports the deeper (truer) round
    # rather than showing the same season twice with conflicting rounds.
    clubs = defaultdict(lambda: {"bestDist": 99, "by_season": {}})

    for season, club_map in participation.items():
        season_label = season.replace("-", "–", 1)  # match finals_raw.json style
        for raw_name, info in club_map.items():
            if raw_name in KNOWN_GARBAGE:
                continue
            name = canonical(raw_name)
            dist = info["distFromFinal"]
            rec = clubs[name]
            rec["bestDist"] = min(rec["bestDist"], dist)
            existing = rec["by_season"].get(season_label)
            if existing is None or dist < existing["distFromFinal"]:
                rec["by_season"][season_label] = {
                    "season": season_label,
                    "roundName": DIST_LABELS.get(dist, info["roundName"]),
                    "distFromFinal": dist,
                }

    out = []
    have_coords, missing_coords = 0, 0
    for name, rec in sorted(clubs.items()):
        appearances = sorted(rec["by_season"].values(), key=lambda a: a["season"])
        best_dist = rec["bestDist"]
        entry = {
            "name": name,
            # label the round the club actually got FURTHEST in -- not
            # appearances[0], which is merely their earliest season and can
            # name a much worse result (Sheriff Tiraspol's best is a 2021-22
            # group stage, but their first season was a 2001-02 qualifying exit)
            "bestRound": DIST_LABELS.get(
                best_dist,
                next(a["roundName"] for a in appearances if a["distFromFinal"] == best_dist)),
            "bestDistFromFinal": best_dist,
            "seasonsPlayed": len(appearances),
            "appearances": appearances,
        }
        if name in coords:
            entry["country"] = coords[name]["country"]
            entry["lat"] = coords[name]["lat"]
            entry["lon"] = coords[name]["lon"]
            have_coords += 1
        else:
            missing_coords += 1
        out.append(entry)

    (DATA / "clubs_full.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(out)} unique club names to data/clubs_full.json")
    print(f"  {have_coords} have coordinates already, {missing_coords} still need geocoding")


if __name__ == "__main__":
    main()
