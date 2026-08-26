"""
Geocodes the clubs in data/clubs_dedup.json that don't have coordinates
yet.

First attempt was raw-text search against OpenStreetMap's Nominatim
address geocoder -- it was unreliable for club names/abbreviations, which
usually aren't real place names ("AEK" matched a hamlet in the
Netherlands, "AB" matched the Canadian province, "AA Gent" matched a
street in Accra, Ghana). Nominatim is an address geocoder, not an entity
search -- wrong tool for this.

Real approach: Wikidata entity search, which matches actual club identities
(including historical name changes -- "17 Nentori" correctly resolves to
the modern "K.F. Tirana"). Per club:
  1. wbsearchentities -- fuzzy label/alias search, ranked by relevance.
  2. Pick the first candidate whose description mentions a football
     club/team (Wikidata descriptions read like "association football
     club in Greece") -- cheap and reliable, no extra API call.
  3. That entity rarely has a direct coordinate (P625), so follow its home
     venue (P115) or headquarters (P159) to a place entity, then that
     entity's P625.
  4. Nominatim (city name only, never the club name) is a last-resort
     fallback for the handful Wikidata doesn't have.

Results are cached incrementally to data/geocode_cache.json (written after
every club) so an interrupted run just resumes -- already-cached names are
skipped.

Run:  python scripts/geocode_clubs.py
"""
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

from dedupe_full_data import cluster_key

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE_PATH = DATA / "geocode_cache.json"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "ucl-history-map-geocoder/1.0 "
                  "(github.com/andre2694/ucl-history-map; one-time research batch)"
}
DELAY = 1.2  # seconds between Wikidata calls; Nominatim calls sleep 1.1s (its policy)
MAX_RETRIES = 5
# Wikidata descriptions vary a lot ("association football club in Greece",
# but also just "sports club in Berne, Switzerland" for BSC Young Boys) --
# broad on purpose, since candidates are already ranked by search relevance
# for the club's actual name, so a same-named non-football "sports club"
# outranking the real match is a rare coincidence, not the common case.
FOOTBALL_DESC_RE = re.compile(
    r"\bfootball (club|team)\b|\bsoccer (club|team)\b|\bsports club\b|"
    r"\bassociation football\b|\bmulti-sports club\b", re.I)

_PAREN_RE = re.compile(r"\(([^)]+)\)")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def core_and_city(name: str):
    m = _PAREN_RE.search(name)
    city = m.group(1).strip() if m else None
    core = _PAREN_RE.sub("", name).strip()
    core = re.sub(r"^\d+\.\s*", "", core)
    return core, city


def _wd_get(params: dict):
    params = {**params, "format": "json"}
    backoff = 5
    for attempt in range(MAX_RETRIES):
        resp = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", backoff))
            time.sleep(wait)
            backoff *= 2
            continue
        resp.raise_for_status()
        time.sleep(DELAY)
        return resp.json()
    raise requests.RequestException(f"Wikidata still rate-limiting after {MAX_RETRIES} retries")


def wd_search_club(search_text: str, country_hint: str | None):
    data = _wd_get({"action": "wbsearchentities", "search": search_text,
                     "language": "en", "type": "item", "limit": 8})
    candidates = data.get("search", [])
    football = [c for c in candidates if FOOTBALL_DESC_RE.search(c.get("description") or "")]
    if not football:
        return None
    if country_hint:
        for c in football:
            if _fold(country_hint) in _fold(c.get("description") or ""):
                return c["id"]
    return football[0]["id"]


def wd_coords_of(qid: str):
    data = _wd_get({"action": "wbgetclaims", "entity": qid, "property": "P625"})
    claims = data.get("claims", {}).get("P625")
    if claims:
        v = claims[0]["mainsnak"]["datavalue"]["value"]
        return v["latitude"], v["longitude"]
    return None


def wd_linked_entity(qid: str, prop: str):
    data = _wd_get({"action": "wbgetclaims", "entity": qid, "property": prop})
    claims = data.get("claims", {}).get(prop)
    if claims:
        v = claims[0]["mainsnak"].get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("entity-type") == "item":
            return v["id"]
    return None


def geocode_via_wikidata(name: str, country_hint: str | None):
    core, city = core_and_city(name)
    # Wikidata's search is fussy about club-type abbreviations that don't
    # exactly match the label ("Sevilla CF" -> nothing, even though the
    # club is "Sevilla FC" and "Sevilla" alone finds it) -- strip common
    # club-type words from both ends as a further fallback query.
    bare = cluster_key(core)[0]
    qid = (wd_search_club(name, country_hint)
           or wd_search_club(core, country_hint)
           or (bare and wd_search_club(bare, country_hint)))
    if not qid:
        return None
    coords = wd_coords_of(qid)
    source = "club"
    if not coords:
        for prop, label in (("P115", "venue"), ("P159", "headquarters")):
            linked = wd_linked_entity(qid, prop)
            if linked:
                coords = wd_coords_of(linked)
                if coords:
                    source = label
                    break
    if not coords:
        return None
    lat, lon = coords
    return {"lat": lat, "lon": lon, "source": f"wikidata:{source}", "qid": qid}


def geocode_via_nominatim(name: str, country_hint: str | None):
    core, city = core_and_city(name)
    query = city or core  # never search the raw club abbreviation -- see docstring
    if country_hint:
        query = f"{query}, {country_hint}"
    resp = requests.get(NOMINATIM_URL, params={"q": query, "format": "jsonv2", "limit": 1},
                         headers=HEADERS, timeout=15)
    resp.raise_for_status()
    time.sleep(1.1)
    results = resp.json()
    if not results:
        return None
    return {"lat": float(results[0]["lat"]), "lon": float(results[0]["lon"]),
            "source": "nominatim", "displayName": results[0].get("display_name")}


def geocode_one(name: str, country_hint: str | None):
    try:
        result = geocode_via_wikidata(name, country_hint)
        if result:
            return result
        result = geocode_via_nominatim(name, country_hint)
        if result:
            return result
        return {"error": "no results from wikidata or nominatim"}
    except requests.RequestException as e:
        return {"error": str(e)}


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache_entry(cache: dict, name: str, entry: dict):
    cache[name] = entry
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    clubs = json.loads((DATA / "clubs_dedup.json").read_text(encoding="utf-8"))
    targets = [c for c in clubs if "lat" not in c]
    cache = load_cache()
    todo = [c for c in targets if c["name"] not in cache]
    print(f"{len(targets)} clubs need coordinates, {len(cache)} already cached, "
          f"{len(todo)} to geocode now.")

    for i, club in enumerate(todo, 1):
        entry = geocode_one(club["name"], club.get("countryHint"))
        save_cache_entry(cache, club["name"], entry)
        status = "OK" if "lat" in entry else "MISS"
        if i % 25 == 0 or status == "MISS":
            print(f"[{i}/{len(todo)}] {status:4} {club['name']!r} ({club.get('countryHint')}) -> "
                  f"{entry.get('source', entry.get('error'))}")

    ok = sum(1 for v in cache.values() if "lat" in v)
    via_nominatim = sum(1 for v in cache.values() if v.get("source") == "nominatim")
    print(f"\nDone. {ok}/{len(cache)} geocoded ({via_nominatim} via the lower-confidence "
          f"Nominatim fallback). Run apply_geocoding.py next to merge into clubs_dedup.json.")


if __name__ == "__main__":
    main()
