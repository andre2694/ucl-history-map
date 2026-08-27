"""
Matches the clubs in data/clubs_dedup.json against the local bulk index
built by build_wikidata_index.py, writing results into
data/geocode_cache.json in the same shape geocode_clubs.py produces.

This replaces almost all of geocode_clubs.py's network work with local
string matching: the index already holds every European football club's
label, English aliases and coordinates, so the only clubs that still need
the slow per-club API path are the ones nothing here matches.

Matching is deliberately conservative -- a wrong coordinate is worse than
a missing one, and we already got burned once by fuzzy matching putting
Deportivo de La Coruña ~500km away in Vitoria-Gasteiz. So:
  - the country hint must agree when we have one (index entries carry a
    country, so this is an exact check, not the substring guesswork the
    web-API path had to do against free-text descriptions)
  - reserve/youth sides ("Benfica B", "Sheriff-2", "Porto II") are
    filtered out unless the club we're looking for is itself one
  - candidates are scored, and an ambiguous tie is left unmatched for the
    API fallback rather than guessed at

Run:  python scripts/match_clubs_to_index.py
"""
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from dedupe_full_data import cluster_key, CLUB_TYPE_TOKENS

# RSSSF files clubs under the country as it was AT THE TIME, so a Yugoslav-
# or Soviet-era entrant's hint won't equal the modern country Wikidata
# lists it under. Treat these as compatible rather than as a mismatch --
# without this, Vardar Skopje (hint: Serbia, actually North Macedonia) and
# Dnepr Dnipropetrovsk (hint: Russia, actually Ukraine) look like wrong-
# country matches and get rejected.
SUCCESSORS = {
    "Serbia": {"Serbia", "Croatia", "Bosnia and Herzegovina", "North Macedonia",
               "Slovenia", "Montenegro", "Kosovo", "Yugoslavia", "Serbia and Montenegro"},
    "Russia": {"Russia", "Ukraine", "Belarus", "Georgia", "Armenia", "Azerbaijan",
               "Kazakhstan", "Latvia", "Lithuania", "Estonia", "Moldova", "Soviet Union"},
    "Czech Republic": {"Czech Republic", "Slovakia", "Czechoslovakia"},
    "Slovakia": {"Slovakia", "Czech Republic", "Czechoslovakia"},
    "Germany": {"Germany", "East Germany"},
}
UK_NATIONS = {"England", "Scotland", "Wales", "Northern Ireland"}


def compatible_countries(hint: str):
    ok = SUCCESSORS.get(hint, {hint})
    if hint in UK_NATIONS:
        ok = ok | {"United Kingdom"}
    return ok


FUZZY_SAME_COUNTRY = 0.86   # "FC Basle" vs "FC Basel"
FUZZY_CROSS_COUNTRY = 0.93  # stricter when the country doesn't line up
SAME_CLUB_KM = 1.5          # two "rival" entries this close are one club, not a tie


def similarity(a: str, b: str) -> float:
    """Edit-distance ratio, but treat one name containing the other as
    strong evidence -- plain ratio narrowly under-scores exactly the cases
    we care about ("ferencvaros" inside "ferencvarosi tc" scores 0.85,
    "porto" inside "do porto" scores 0.77). Scaled by how much of the
    longer name is covered, so a short generic token can't hijack a long
    name. Ties between genuinely different clubs are still caught by the
    coordinate check in fuzzy_match()."""
    ratio = SequenceMatcher(None, a, b).ratio()
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    # Coverage floor matters: without it a generic one-word alias swallows
    # every club sharing that word -- "dynamo" is contained in "dynamo
    # kiev" and would score 0.95, which is how Dynamo Kyiv ended up
    # matched to Dynamo Rostov-on-Don.
    if len(short) >= 5 and short in long and len(short) / len(long) >= 0.6:
        ratio = max(ratio, 0.88 + 0.12 * len(short) / len(long))
    return ratio


def _km_apart(a, b) -> float:
    import math
    dlat = (a["lat"] - b["lat"]) * 111
    dlon = (a["lon"] - b["lon"]) * 111 * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlon)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# reserve / youth / women's sides that share a parent club's name
RESERVE_RE = re.compile(
    r"(\b(II|III|B|C|U\d{2}|Reserves?|Amateure|Youth|Women'?s?|Feminino|Femenino)\b"
    r"|[-\s]\d\b)\s*$", re.I)


def _fold(s: str) -> str:
    s = (s.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("Æ", "AE")
          .replace("ð", "d").replace("Ð", "D").replace("þ", "th").replace("Þ", "Th")
          .replace("ß", "ss"))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    # Drop periods BEFORE splitting on punctuation, so dotted acronyms stay
    # one token: "F.C." -> "fc" (strippable as a club-type word) rather
    # than "f c" (which isn't). Without this "Crusaders F.C." never reduces
    # to "crusaders", and loses the key to a rival club's nickname alias.
    s = s.replace(".", "")
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def norm_core(name: str) -> str:
    """Club name reduced to its distinctive words: accents folded, city
    parenthetical dropped, club-type prefixes/suffixes stripped."""
    core = re.sub(r"\([^)]*\)", " ", name)
    core = re.sub(r"^\d+\.\s*", "", core)
    words = [w for w in _fold(core).split() if w]
    while words and words[0] in CLUB_TYPE_TOKENS:
        words.pop(0)
    while words and words[-1] in CLUB_TYPE_TOKENS:
        words.pop()
    return " ".join(words)


def city_of(name: str):
    m = re.search(r"\(([^)]+)\)", name)
    return _fold(m.group(1)) if m else None


def build_lookup(index):
    """normalized-name -> [entries]. Both labels and aliases are indexed,
    since RSSSF's spelling often matches an alias rather than the label
    ("Sporting CP (Lisboa)" -> alias "Sporting Lisbon")."""
    lookup = {}
    for entry in index:
        names = [entry["label"], *entry["aliases"]]
        entry["_isReserve"] = bool(RESERVE_RE.search(entry["label"]))
        keys = set()
        for n in names:
            keys.update(k for k in (_fold(n), norm_core(n)) if k)
        entry["_keys"] = keys  # reused by fuzzy_match
        # keys from the official label are trustworthy; alias-only keys
        # include nicknames, which collide across clubs and countries
        # ("Crusaders" is also Hungerford Town's nickname)
        entry["_labelKeys"] = {k for k in (_fold(entry["label"]), norm_core(entry["label"])) if k}
        # every place name the entry mentions, for the city check below
        entry["_places"] = _fold(" ".join([entry["label"], *entry["aliases"]]))
        for key in keys:
            lookup.setdefault(key, []).append(entry)
    return lookup


def city_conflicts(name: str, entry) -> bool:
    """True when our club name names a city the candidate never mentions.
    RSSSF qualifies ambiguous clubs precisely because the bare name is
    shared -- "Bohemians (Dublin)" exists to distinguish it from Cork
    Bohemians, so ignoring that qualifier throws away the disambiguator
    that was put there for us."""
    city = city_of(name)
    if not city or len(city) < 4:
        return False
    if city in entry["_places"]:
        return False
    # tolerate endonym/exonym pairs the index spells differently
    return not any(similarity(city, w) >= 0.8 for w in entry["_places"].split())


def match_one(name, country_hint, lookup):
    """Return (entry, how) or (None, reason)."""
    want_reserve = bool(RESERVE_RE.search(name))
    city = city_of(name)

    for key, how in ((_fold(re.sub(r"\([^)]*\)", " ", name)), "exact"),
                     (norm_core(name), "core")):
        if not key:
            continue
        cands = lookup.get(key, [])
        if not cands:
            continue
        # dedupe by qid -- the same entry is indexed under several keys
        seen, uniq = set(), []
        for c in cands:
            if c["qid"] not in seen:
                seen.add(c["qid"])
                uniq.append(c)
        pool = [c for c in uniq if c["_isReserve"] == want_reserve] or uniq
        if country_hint:
            ok = compatible_countries(country_hint)
            in_country = [c for c in pool if c["country"] in ok]
            if not in_country:
                continue  # candidates exist but none in the right country -- distrust
            pool = in_country
        # a candidate matched only through a nickname alias, whose label
        # doesn't correspond at all, is far weaker evidence -- drop those
        # if any candidate matches on its actual label
        on_label = [c for c in pool if key in c["_labelKeys"]]
        if on_label:
            pool = on_label
        pool = [c for c in pool if not city_conflicts(name, c)] or []
        if not pool:
            continue
        if len(pool) == 1:
            return pool[0], how
        if city:
            near = [c for c in pool if city in _fold(c["label"])]
            if len(near) == 1:
                return near[0], how + "+city"
        # several equally plausible clubs -- leave for the API fallback
        # rather than pick one arbitrarily
        return None, f"ambiguous({len(pool)})"
    return None, "no match"


def fuzzy_match(name, country_hint, by_country, all_entries):
    """Last resort before the slow API path: closest normalized-core match,
    scoped to compatible countries so 'Spartak' can't drift to the wrong
    country's Spartak. Threshold is deliberately high, and a near-tie
    between two different clubs is rejected."""
    want_reserve = bool(RESERVE_RE.search(name))
    key = norm_core(name)
    if not key or len(key) < 4:
        return None, "too short to fuzzy match"

    if country_hint:
        pool, threshold = [], FUZZY_SAME_COUNTRY
        for c in compatible_countries(country_hint):
            pool.extend(by_country.get(c, []))
        if not pool:
            pool, threshold = all_entries, FUZZY_CROSS_COUNTRY
    else:
        pool, threshold = all_entries, FUZZY_CROSS_COUNTRY

    scored = []
    for entry in pool:
        if entry["_isReserve"] != want_reserve or not entry["_keys"]:
            continue
        if city_conflicts(name, entry):
            continue
        best = max(similarity(key, k) for k in entry["_keys"])
        if best >= threshold:
            scored.append((best, entry))
    if not scored:
        return None, "no fuzzy match"
    scored.sort(key=lambda x: -x[0])
    top_score, top = scored[0]
    # Only a near-tie between clubs in DIFFERENT PLACES is a real
    # ambiguity. Wikidata often holds several entries for one club
    # ("Schalke 04" / "FC Schalke 04", a club and its stadium-sharing
    # alias), which score alike but sit at the same coordinates -- picking
    # either is correct.
    # ...and a rival whose NAME is essentially the top's name isn't a
    # different club either, however far apart the two entries' recorded
    # coordinates are (Wikidata may hang one off the stadium and the other
    # off the club's registered office). Require both a real distance and
    # a genuinely different name before calling it ambiguous.
    top_core = norm_core(top["label"])
    rivals = [e for s, e in scored[1:]
              if top_score - s < 0.02 and e["qid"] != top["qid"]
              and _km_apart(e, top) > SAME_CLUB_KM
              and similarity(top_core, norm_core(e["label"])) < 0.9]
    if rivals:
        return None, f"fuzzy tie ({top['label']} vs {rivals[0]['label']})"
    return top, f"fuzzy {top_score:.2f} -> {top['label']}"


def main():
    index = json.loads((DATA / "wikidata_club_index.json").read_text(encoding="utf-8"))
    clubs = json.loads((DATA / "clubs_dedup.json").read_text(encoding="utf-8"))
    cache_path = DATA / "geocode_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    lookup = build_lookup(index)
    by_country = {}
    for entry in index:
        by_country.setdefault(entry["country"], []).append(entry)
    targets = [c for c in clubs if "lat" not in c]

    matched = fuzzy = already = 0
    unmatched = []
    for club in targets:
        if club["name"] in cache and "lat" in cache[club["name"]]:
            already += 1
            continue
        entry, how = match_one(club["name"], club.get("countryHint"), lookup)
        if not entry:
            entry, how = fuzzy_match(club["name"], club.get("countryHint"), by_country, index)
            if entry:
                fuzzy += 1
        if entry:
            cache[club["name"]] = {
                "lat": entry["lat"], "lon": entry["lon"],
                "source": f"wikidata-bulk:{entry['coordSource']}",
                "qid": entry["qid"], "country": entry["country"],
                "matchedLabel": entry["label"], "matchType": how,
            }
            matched += 1
        else:
            unmatched.append((club["name"], club.get("countryHint"), how))

    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"index: {len(index)} clubs, {len(lookup)} lookup keys")
    print(f"targets: {len(targets)}  already cached: {already}  "
          f"newly matched: {matched} (of which {fuzzy} fuzzy)  "
          f"still unmatched: {len(unmatched)}")
    if unmatched:
        print("\nStill needing the API fallback (run geocode_clubs.py):")
        for name, hint, why in unmatched[:40]:
            print(f"  {name!r} ({hint}) -- {why}")
        if len(unmatched) > 40:
            print(f"  ... and {len(unmatched) - 40} more")


if __name__ == "__main__":
    main()
