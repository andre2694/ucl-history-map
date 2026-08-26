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

    clubs = defaultdict(lambda: {"bestDist": 99, "appearances": []})

    for season, club_map in participation.items():
        season_label = season.replace("-", "–", 1)  # match finals_raw.json style
        for raw_name, info in club_map.items():
            if raw_name in KNOWN_GARBAGE:
                continue
            name = canonical(raw_name)
            dist = info["distFromFinal"]
            rec = clubs[name]
            rec["bestDist"] = min(rec["bestDist"], dist)
            rec["appearances"].append({
                "season": season_label,
                "roundName": DIST_LABELS.get(dist, info["roundName"]),
                "distFromFinal": dist,
            })

    out = []
    have_coords, missing_coords = 0, 0
    for name, rec in sorted(clubs.items()):
        appearances = sorted(rec["appearances"], key=lambda a: a["season"])
        best_dist = rec["bestDist"]
        entry = {
            "name": name,
            "bestRound": DIST_LABELS.get(best_dist, appearances[0]["roundName"] if best_dist >= 4 else "Early rounds"),
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
