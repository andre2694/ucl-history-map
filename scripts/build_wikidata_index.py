"""
Builds data/wikidata_club_index.json -- a local index of European football
clubs with coordinates, pulled in bulk from Wikidata's SPARQL endpoint.

WHY: the original geocode_clubs.py hits the Wikidata *web API* once per
club, and needs 3-6 sequential round trips each (fuzzy search, sometimes
2 fallback searches, check the club for coordinates, fetch its venue,
fetch the venue's coordinates) -- each followed by a mandatory ~1.2s
courtesy sleep, plus exponential backoff whenever Wikidata 429s. That is
~5s/club at best and much worse when throttled: ~800 clubs took hours to
get a fraction of the way through.

SPARQL inverts this. One query returns EVERY football club in a country
along with its coordinates and English aliases, so the whole candidate
set for Europe arrives in ~55 queries (one per country, a few seconds
each) instead of several thousand round trips. Matching then happens
locally, for free.

Query shape notes (learned by testing against the live endpoint):
  - `wdt:P31/wdt:P279*` subclass traversal times out (504). Plain
    `wdt:P31` with an explicit VALUES list of club types is fine.
  - An OPTIONAL/COALESCE union over the three coordinate sources (venue /
    headquarters / the club's own P625) 502s. Running them as three
    separate simple queries and merging locally is both faster and more
    reliable.
  - A single query over all countries at once (VALUES ?country {...})
    502s. Per-country chunks stay well inside the endpoint's limits.

Run:  python scripts/build_wikidata_index.py
"""
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_PATH = DATA / "wikidata_club_index.json"

SPARQL_URL = "https://query.wikidata.org/sparql"
HEADERS = {
    "User-Agent": "ucl-history-map-indexer/1.0 "
                  "(github.com/andre2694/ucl-history-map; one-time bulk index build)"
}
DELAY = 1.0          # courtesy pause between SPARQL queries
MAX_RETRIES = 4

# Country name -> Wikidata QID. Includes the historical states some older
# clubs are still filed under (USSR/Yugoslavia/Czechoslovakia/East
# Germany), plus the UK both as a whole and as its home nations, since
# Wikidata is inconsistent about which one a British club's P17 points to.
COUNTRIES = {
    "Albania": "Q222", "Andorra": "Q228", "Armenia": "Q399", "Austria": "Q40",
    "Azerbaijan": "Q227", "Belarus": "Q184", "Belgium": "Q31",
    "Bosnia and Herzegovina": "Q225", "Bulgaria": "Q219", "Croatia": "Q224",
    "Cyprus": "Q229", "Czech Republic": "Q213", "Denmark": "Q35",
    "England": "Q21", "Estonia": "Q191", "Faroe Islands": "Q4628",
    "Finland": "Q33", "France": "Q142", "Georgia": "Q230", "Germany": "Q183",
    "Gibraltar": "Q1410", "Greece": "Q41", "Hungary": "Q28", "Iceland": "Q189",
    "Ireland": "Q27", "Israel": "Q801", "Italy": "Q38", "Kazakhstan": "Q232",
    "Kosovo": "Q1246", "Latvia": "Q211", "Liechtenstein": "Q347",
    "Lithuania": "Q37", "Luxembourg": "Q32", "Malta": "Q233", "Moldova": "Q217",
    "Monaco": "Q235", "Montenegro": "Q236", "Netherlands": "Q55",
    "North Macedonia": "Q221", "Northern Ireland": "Q26", "Norway": "Q20",
    "Poland": "Q36", "Portugal": "Q45", "Romania": "Q218", "Russia": "Q159",
    "San Marino": "Q238", "Scotland": "Q22", "Serbia": "Q403",
    "Slovakia": "Q214", "Slovenia": "Q215", "Spain": "Q29", "Sweden": "Q34",
    "Switzerland": "Q39", "Turkey": "Q43", "Ukraine": "Q212", "Wales": "Q25",
    # umbrella + historical states
    "United Kingdom": "Q145", "Soviet Union": "Q15180",
    "Yugoslavia": "Q36704", "Czechoslovakia": "Q33946",
    "East Germany": "Q16957", "Serbia and Montenegro": "Q37024",
}

# Wikidata is inconsistent about a club's "instance of": most are
# Q476028 (association football club), but plenty are filed only as a
# generic sports club or a football-team entity.
CLUB_TYPES = ["wd:Q476028", "wd:Q847017", "wd:Q15944511"]

# The three places a club's coordinates can hang off, most to least
# specific. Run separately -- a UNION/COALESCE over them 502s.
COORD_PATHS = [
    ("venue", "?club wdt:P115 ?place . ?place wdt:P625 ?coord ."),
    ("headquarters", "?club wdt:P159 ?place . ?place wdt:P625 ?coord ."),
    ("club", "?club wdt:P625 ?coord ."),
]

QUERY_TEMPLATE = """
SELECT ?club ?clubLabel ?lat ?lon (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?alts) WHERE {{
  VALUES ?type {{ {types} }}
  ?club wdt:P31 ?type ; wdt:P17 wd:{country} .
  {coord_path}
  BIND(geof:latitude(?coord) AS ?lat)
  BIND(geof:longitude(?coord) AS ?lon)
  OPTIONAL {{ ?club skos:altLabel ?alt FILTER(lang(?alt) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
GROUP BY ?club ?clubLabel ?lat ?lon
"""


def run_sparql(query: str):
    backoff = 5
    for _ in range(MAX_RETRIES):
        try:
            resp = requests.get(SPARQL_URL, params={"query": query, "format": "json"},
                                headers=HEADERS, timeout=180)
        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 2
            continue
        if resp.status_code == 200:
            time.sleep(DELAY)
            return resp.json()["results"]["bindings"]
        # 429/500/502/504 are all worth a retry -- the endpoint is shared
        # and sheds load under pressure
        time.sleep(float(resp.headers.get("Retry-After", backoff)))
        backoff *= 2
    return None


def main():
    index = {}      # qid -> entry (first coordinate source found wins)
    failures = []

    for country, qid in COUNTRIES.items():
        got = 0
        for source, coord_path in COORD_PATHS:
            query = QUERY_TEMPLATE.format(
                types=" ".join(CLUB_TYPES), country=qid, coord_path=coord_path)
            rows = run_sparql(query)
            if rows is None:
                failures.append(f"{country}/{source}")
                continue
            for b in rows:
                club_qid = b["club"]["value"].rsplit("/", 1)[-1]
                if club_qid in index:
                    continue  # earlier (more specific) coordinate source wins
                alts = b.get("alts", {}).get("value", "")
                index[club_qid] = {
                    "qid": club_qid,
                    "label": b["clubLabel"]["value"],
                    "aliases": [a for a in alts.split("|") if a],
                    "lat": float(b["lat"]["value"]),
                    "lon": float(b["lon"]["value"]),
                    "country": country,
                    "coordSource": source,
                }
                got += 1
        print(f"{country:26} +{got:4}  (total {len(index)})")

    OUT_PATH.write_text(json.dumps(list(index.values()), ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\nWrote {len(index)} clubs to {OUT_PATH.name}")
    if failures:
        print(f"WARNING: {len(failures)} queries failed after retries: {failures}")


if __name__ == "__main__":
    main()
