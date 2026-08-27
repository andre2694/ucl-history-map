"""
Cleans + deduplicates data/clubs_full.json before geocoding.

The v1 participation scraper (scrape_rsssf.py) optimizes for correctly
identifying *rounds reached*, not for producing clean unique club names --
across 70 years of hand-edited HTML, the same club shows up under many
spellings ("AEK", "AEK (Athens)", "AEK (Athinai)", "AEK (Athina)", ...) and
a small fraction of "names" are actually parsing artifacts (score
fragments, footnote text, a city name split off onto its own line and
misread as a club). Geocoding the raw 899 names as-is would waste API
calls on garbage and put duplicate markers on the map for the same club.

This pass:
  1. Drops entries that don't look like a club name at all.
  2. Clusters remaining entries by a normalized (core name, city) key --
     city normalization uses a small manually-verified alias table (see
     CITY_ALIASES) rather than aggressive fuzzy matching, because a bare
     club name like "Dynamo" or "Partizan" is used by several *genuinely
     different* real clubs across different countries -- the city is often
     the only disambiguator, so under-merging is the safe failure mode,
     over-merging is not.
  3. Merges each cluster's appearances and recomputes bestRound.

Run:  python scripts/dedupe_full_data.py
"""
import json
import re
import unicodedata
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DIST_LABELS = {0: "Final", 1: "Semifinal", 2: "Quarterfinal", 3: "Round of 16"}

CLUB_TYPE_TOKENS = {
    "fc", "ac", "sc", "sk", "ck", "as", "ask", "ks", "kf", "fk", "cf", "cd",
    "ss", "ssd", "us", "ud", "cs", "bc", "bk", "if", "ik", "aa", "sv", "tsv",
    "vfb", "vfl", "bv", "kv", "nk", "rc", "rcd", "1", "sd", "cfr", "uc",
    "ca", "ce", "cp", "ol", "gks", "mtk", "spvgg", "kfum", "ssc", "sl",
    # further club-type/legal-form prefixes found via the "core name
    # doesn't match a known-good club because a prefix wasn't stripped"
    # class of bug (RSC Anderlecht not reducing to match bare
    # "Anderlecht" was the one that surfaced this gap) -- inherently a
    # long tail across European naming conventions, not exhaustive
    "rsc", "rfc", "raec", "rwdm", "kaa", "krc", "kfc", "ksc", "ksk", "ksv",
    "fsv", "sg", "gd", "ao", "ae", "pae", "mks", "lks", "ogc", "es",
}

# Same city, different language/transliteration -- verified by hand against
# the actual data (see the frequency scan this was built from). Keys are
# already accent-folded lowercase.
CITY_ALIASES = {
    "athinai": "athens", "athinia": "athens", "athina": "athens", "athinai.": "athens",
    "tirana": "tirane",
    "peiraias": "piraeus", "pireus": "piraeus",
    "nicosia": "lefkosia", "lefkosia": "lefkosia",
    "limassol": "lemesos",
    "kobenhavn": "copenhagen",
    "beograd": "belgrade",
    "wien": "vienna",
    "praha": "prague",
    "lisboa": "lisbon",
    "munchen": "munich",
    "moskva": "moscow",
    "bucuresti": "bucharest",
    "zagreb": "zagreb",
    "pawla": "paola",  # Maltese/English names for the same town (Raħal Ġdid)
}


def _fold(s: str) -> str:
    s = (s.replace("ø", "o").replace("Ø", "O")
           .replace("æ", "ae").replace("Æ", "AE")
           .replace("ð", "d").replace("Ð", "D")
           .replace("þ", "th").replace("Þ", "Th")
           .replace("ß", "ss"))
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


_PAREN_RE = re.compile(r"\(([^)]+)\)")
_LEADING_NUM_RE = re.compile(r"^\d+\.?\s*")


def looks_bogus(name: str) -> bool:
    if re.search(r"\d+[:.]\d+|\d+\.[A-Z]", name):
        return True  # score fragments: "0:0", "66.Gilewicz"
    if re.match(r"^[(*+%]", name):
        return True  # stray footnote/reference markers
    if len(name) < 3 or len(name) > 45:
        return True
    if name.count(",") >= 2:
        return True  # prose fragments
    if re.search(r"\b(had|were|was|their|for|from|awarded|banned)\b", name, re.I):
        return True  # leaked footnote prose
    if _PAREN_RE.fullmatch(name.strip()):
        return True  # just "(City)" with no club name -- a mis-split fragment
    return False


def cluster_key(name: str):
    m = _PAREN_RE.search(name)
    city = _fold(m.group(1)) if m else ""
    city = CITY_ALIASES.get(city, city)
    core = _PAREN_RE.sub("", name)
    core = _LEADING_NUM_RE.sub("", core)
    core = _fold(core)
    words = [w for w in re.split(r"[^a-z0-9]+", core) if w]
    while words and words[0] in CLUB_TYPE_TOKENS:
        words.pop(0)
    while words and words[-1] in CLUB_TYPE_TOKENS:
        words.pop()
    return " ".join(words), city


def pick_canonical(names_with_city):
    """Prefer a name that carries a parenthetical city hint (useful for
    geocoding later), then the longest, then alphabetically first."""
    def score(n):
        return (1 if _PAREN_RE.search(n) else 0, len(n))
    return sorted(names_with_city, key=lambda n: (-score(n)[0], -score(n)[1], n))[0]


def _country_of(entry):
    return entry.get("country") or entry.get("countryHint")


def absorb_bare_into_city(out, merge_log):
    """RSSSF sometimes writes a club with its city qualifier and sometimes
    without ("Olympiakos (Peiraías)" in most seasons, bare "Olympiakos" in
    four others). Those cluster separately because the city is part of the
    key, leaving a fragment that then geocodes on its own -- bare
    "Olympiakos" landed on Olympiacos VOLOS, a different club 250km away.

    So: fold a city-less cluster into a city-qualified one when they share
    a core name AND a country, and exactly one such candidate exists. The
    country requirement is what keeps this safe -- bare "Olympiakos"
    (Greece) must not absorb into "Olympiakos (Nicosia)" (Cyprus), and
    with two same-core clusters in different countries the ambiguity
    check leaves everything alone."""
    by_core = defaultdict(list)
    for c in out:
        if c["_city"]:
            by_core[c["_core"]].append(c)

    absorbed = set()
    for c in out:
        if c["_city"] or c["_core"] not in by_core:
            continue
        cands = [t for t in by_core[c["_core"]]
                 if _country_of(t) and _country_of(t) == _country_of(c)]
        if len(cands) != 1:
            continue  # nothing to absorb into, or genuinely ambiguous
        target = cands[0]
        seen = {(a["season"], a.get("distFromFinal")) for a in target["appearances"]}
        for a in c["appearances"]:
            if (a["season"], a.get("distFromFinal")) not in seen:
                target["appearances"].append(a)
        target["appearances"].sort(key=lambda a: a["season"])
        target["seasonsPlayed"] = len(target["appearances"])
        best = min(a["distFromFinal"] for a in target["appearances"])
        target["bestDistFromFinal"] = best
        target["bestRound"] = next(a["roundName"] for a in target["appearances"]
                                   if a["distFromFinal"] == best)
        merge_log.append((target["name"], [c["name"] + " (city-less fragment)"]))
        absorbed.add(id(c))
    return [c for c in out if id(c) not in absorbed]


def main():
    clubs = json.loads((DATA / "clubs_full.json").read_text(encoding="utf-8"))
    # the 42 hand-curated finalists already have a canonical spelling
    # (club_coords.json / finals_raw.json) -- when a cluster matches one of
    # them, force that exact spelling so this dedup pass doesn't invent a
    # second identity ("AC Fiorentina") for a club we already track
    # ("Fiorentina"), which would show up as two overlapping markers later.
    known_coords = json.loads((DATA / "club_coords.json").read_text(encoding="utf-8"))
    country_hints = {}
    hints_path = DATA / "club_country_hints.json"
    if hints_path.exists():
        country_hints = json.loads(hints_path.read_text(encoding="utf-8"))
    known_by_key = {cluster_key(name): name for name in known_coords}
    # the 42-list often omits the city ("Partizan", not "Partizan (Belgrade)")
    # since it didn't need disambiguating in that curated context -- also
    # index by core name alone so it still matches a scraped cluster that
    # does carry a city qualifier
    known_by_core = {cluster_key(name)[0]: name for name in known_coords
                      if not cluster_key(name)[1]}

    bogus = [c for c in clubs if looks_bogus(c["name"])]
    kept = [c for c in clubs if not looks_bogus(c["name"])]

    clusters = defaultdict(list)
    for c in kept:
        clusters[cluster_key(c["name"])].append(c)

    out = []
    merge_log = []
    for key, members in clusters.items():
        names = [m["name"] for m in members]
        canonical = known_by_key.get(key) or known_by_core.get(key[0]) or pick_canonical(names)
        if len(members) > 1 or canonical not in names:
            merge_log.append((canonical, sorted(set(names) - {canonical})))

        appearances = []
        seen = set()
        for m in members:
            for a in m["appearances"]:
                dedupe_key = (a["season"], a.get("distFromFinal"))
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                appearances.append(a)
        appearances.sort(key=lambda a: a["season"])
        best_dist = min(a["distFromFinal"] for a in appearances)

        merged = {
            "name": canonical,
            # the appearance that actually reached best_dist, not the
            # earliest one -- see build_full_data.py for why
            "bestRound": next(a["roundName"] for a in appearances
                              if a["distFromFinal"] == best_dist),
            "bestDistFromFinal": best_dist,
            "seasonsPlayed": len(appearances),
            "appearances": appearances,
        }
        # look up by the FINAL canonical name, not the raw members -- a
        # member's own name (e.g. "FK Partizan (Beograd)") may not match
        # club_coords.json even though the cluster's canonical name does
        if canonical in known_coords:
            c = known_coords[canonical]
            merged["country"], merged["lat"], merged["lon"] = c["country"], c["lat"], c["lon"]
        else:
            for m in members:
                if "lat" in m:
                    merged["country"], merged["lat"], merged["lon"] = m["country"], m["lat"], m["lon"]
                    break
            if "country" not in merged:
                hints = Counter(country_hints[n] for n in names if n in country_hints)
                if hints:
                    merged["countryHint"] = hints.most_common(1)[0][0]
        merged["_core"] = key[0]
        merged["_city"] = key[1]
        out.append(merged)

    out = absorb_bare_into_city(out, merge_log)
    for c in out:
        c.pop("_core", None)
        c.pop("_city", None)

    out.sort(key=lambda c: c["name"])
    (DATA / "clubs_dedup.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Input:  {len(clubs)} names")
    print(f"Dropped as bogus: {len(bogus)}")
    print(f"Output: {len(out)} clusters (from {len(kept)} clean names, "
          f"{len(kept) - len(out)} merged as duplicates)")
    print(f"\n{len(merge_log)} clusters had merges. Largest 15:")
    for canonical, variants in sorted(merge_log, key=lambda x: -len(x[1]))[:15]:
        print(f"  {canonical!r} <- {variants}")
    print(f"\nSample of dropped bogus names:")
    for c in bogus[:15]:
        print(f"  {c['name']!r}")


if __name__ == "__main__":
    main()
