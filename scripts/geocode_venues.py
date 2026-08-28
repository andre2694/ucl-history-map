"""
Builds data/final_venues.json -- every stadium that has hosted a European
Cup / Champions League final, with coordinates and the finals it held.

Venues are keyed by (venue, city), never by name alone: "Olympiastadion"
has hosted finals in both Munich and Berlin, and several grounds share
generic names like "Olympic Stadium".

Only ~34 distinct venues exist across all 71 finals, so unlike the ~700
clubs this needs no bulk SPARQL index -- a per-venue Wikidata entity
search is fast enough. Stadiums, unlike clubs, usually carry coordinates
(P625) directly rather than via a linked venue.

Many are listed under their modern name while finals_raw.json records the
historical one (Heysel -> King Baudouin, Praterstadion -> Ernst-Happel,
Neckarstadion -> MHPArena). Wikidata's search covers those as aliases, so
the old names generally resolve; anything that doesn't is reported for
hand-checking rather than silently guessed at.

Run:  python scripts/geocode_venues.py
"""
import json
import re
import time
import unicodedata
from collections import OrderedDict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "venue_geocode_cache.json"
OUT = DATA / "final_venues.json"

API = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "ucl-history-map/1.0 (github.com/andre2694/ucl-history-map)"}
DELAY = 1.2

VENUE_DESC_RE = re.compile(r"stadium|arena|sports venue|football ground|stadion", re.I)

# Same building, recorded under the name it carried at the time. Merging
# these keeps one marker per physical ground rather than two on the same
# spot: Vienna's Praterstadion was renamed Ernst-Happel-Stadion in 1992,
# so the four finals played there belong to a single venue.
VENUE_ALIASES = {
    ("Praterstadion", "Vienna"): ("Ernst-Happel-Stadion", "Vienna"),
}

# Wikidata search can't resolve these; coordinates verified by hand.
MANUAL_COORDS = {
    ("NSC Olimpiyskiy Stadium", "Kyiv"): (50.4333, 30.5219),
}


def _fold(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def wd(params):
    backoff = 5
    for _ in range(5):
        resp = requests.get(API, params={**params, "format": "json"},
                            headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            time.sleep(DELAY)
            return resp.json()
        time.sleep(float(resp.headers.get("Retry-After", backoff)))
        backoff *= 2
    raise RuntimeError(f"Wikidata failed: {params}")


def coords_of(qid):
    claims = wd({"action": "wbgetclaims", "entity": qid, "property": "P625"}
                ).get("claims", {}).get("P625")
    if not claims:
        return None
    v = claims[0]["mainsnak"]["datavalue"]["value"]
    return v["latitude"], v["longitude"]


def geocode(venue, city):
    for query in (f"{venue} {city}", venue, f"{venue} stadium"):
        hits = wd({"action": "wbsearchentities", "search": query,
                   "language": "en", "type": "item", "limit": 8}).get("search", [])
        venues = [h for h in hits if VENUE_DESC_RE.search(h.get("description") or "")]
        if not venues:
            continue
        # prefer a hit whose description names the right city -- generic
        # stadium names repeat across countries
        preferred = [h for h in venues
                     if _fold(city) in _fold(h.get("description") or "")] or venues
        for hit in preferred:
            got = coords_of(hit["id"])
            if got:
                return {"lat": got[0], "lon": got[1], "qid": hit["id"],
                        "matchedLabel": hit.get("label"), "source": "wikidata"}
    return None


def rich_finals_index():
    """(club, season) -> that club's rich record for the final it played:
    team-attributed scorers and the Wikipedia match report. Built by
    build_data.py, so the venue view reuses exactly what the club view
    shows rather than re-deriving it."""
    path = DATA / "clubs.json"
    if not path.exists():
        return {}
    index = {}
    for club in json.loads(path.read_text(encoding="utf-8")):
        for a in club["appearances"]:
            index[(club["name"], a["season"])] = a
    return index


def _final_record(f, rich):
    # the winner's own record carries both sides: "scorers" is theirs,
    # "opponentScorers" the runner-up's
    got = rich.get((f["winner"], f["season"]), {})
    record = {"season": f["season"], "winner": f["winner"],
              "runnerUp": f["runnerUp"], "score": f["score"]}
    for src, dest in (("scorers", "winnerScorers"),
                      ("opponentScorers", "runnerUpScorers"),
                      ("unassignedScorers", "unassignedScorers"),
                      ("wikiUrl", "wikiUrl")):
        if got.get(src):
            record[dest] = got[src]
    return record


def main():
    finals = json.loads((DATA / "finals_raw.json").read_text(encoding="utf-8"))
    rich = rich_finals_index()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    venues = OrderedDict()
    for f in finals:
        key = (f["venue"], f["city"])
        key = VENUE_ALIASES.get(key, key)
        venues.setdefault(key, []).append(f)

    misses = []
    for (venue, city), hosted in venues.items():
        key = f"{venue}||{city}"
        if (venue, city) in MANUAL_COORDS and "lat" not in cache.get(key, {}):
            lat, lon = MANUAL_COORDS[(venue, city)]
            cache[key] = {"lat": lat, "lon": lon, "source": "manual",
                          "matchedLabel": venue}
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        if key not in cache:
            try:
                cache[key] = geocode(venue, city) or {"error": "no match"}
            except requests.RequestException as e:
                cache[key] = {"error": str(e)}
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
            status = "OK  " if "lat" in cache[key] else "MISS"
            print(f"  {status} {venue}, {city} -> {cache[key].get('matchedLabel', cache[key].get('error'))}")
        if "lat" not in cache[key]:
            misses.append((venue, city))

    out = []
    for (venue, city), hosted in venues.items():
        hit = cache[f"{venue}||{city}"]
        if "lat" not in hit:
            continue
        out.append({
            "venue": venue, "city": city,
            "lat": hit["lat"], "lon": hit["lon"],
            "qid": hit.get("qid"), "matchedLabel": hit.get("matchedLabel"),
            "finalsHosted": len(hosted),
            "finals": [_final_record(f, rich) for f in hosted],
        })
    out.sort(key=lambda v: (-v["finalsHosted"], v["venue"]))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(out)}/{len(venues)} venues geocoded -> {OUT.name}")
    if misses:
        print(f"unresolved (need hand-checking): {misses}")


if __name__ == "__main__":
    main()
