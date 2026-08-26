"""
Merges data/geocode_cache.json (from geocode_clubs.py) into
data/clubs_dedup.json, producing data/clubs_final.json -- the complete v1
dataset, ready to plot on the map.

Also does one more dedup pass: two clusters that resolved to the *same*
Wikidata entity (e.g. bare "AEK" and "AEK (Athinai)", which the text-based
clustering in dedupe_full_data.py didn't merge because one has a
parenthetical city and the other doesn't) are the same real club and get
merged here, keyed by QID rather than name text -- entity identity is a
more reliable merge signal than name normalization ever fully can be.

Run:  python scripts/apply_geocoding.py
"""
import json
from pathlib import Path
from collections import defaultdict

from dedupe_full_data import cluster_key, CLUB_TYPE_TOKENS

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DIST_LABELS = {0: "Final", 1: "Semifinal", 2: "Quarterfinal", 3: "Round of 16"}


def shares_core_word(name_a: str, name_b: str) -> bool:
    """Guard against a bad Wikidata search match merging two genuinely
    different clubs that happen to geocode to the same place (seen: a
    pre-1992 club's search fell through to its unrelated same-city
    successor). Require the core (non-parenthetical) names to share at
    least one real word -- true club-identity matches always do
    ("AEK" / "AEK (Athinai)", "Sparta Praha" / "Sparta Prague"); a
    same-city coincidence like "B1903" / "FC Copenhagen" never will."""
    words_a = set(cluster_key(name_a)[0].split()) - CLUB_TYPE_TOKENS
    words_b = set(cluster_key(name_b)[0].split()) - CLUB_TYPE_TOKENS
    words_a = {w for w in words_a if len(w) > 2}
    words_b = {w for w in words_b if len(w) > 2}
    return bool(words_a & words_b)


def pick_canonical(names):
    def score(n):
        return (1 if "(" in n else 0, len(n))
    return sorted(names, key=lambda n: (-score(n)[0], -score(n)[1], n))[0]


def main():
    clubs = json.loads((DATA / "clubs_dedup.json").read_text(encoding="utf-8"))
    cache = json.loads((DATA / "geocode_cache.json").read_text(encoding="utf-8"))

    with_coords, still_missing = [], []
    for c in clubs:
        if "lat" in c:
            with_coords.append(c)
            continue
        hit = cache.get(c["name"])
        if hit and "lat" in hit:
            c["lat"], c["lon"] = hit["lat"], hit["lon"]
            c["geocodeSource"] = hit.get("source")
            if "country" in hit:
                c["country"] = hit["country"]
            c.pop("countryHint", None)
            with_coords.append(c)
        else:
            still_missing.append(c)

    # merge clusters that resolved to the same Wikidata entity
    by_qid = defaultdict(list)
    no_qid = []
    for c in with_coords:
        qid = cache.get(c["name"], {}).get("qid")
        if qid:
            by_qid[qid].append(c)
        else:
            no_qid.append(c)

    merged_out = list(no_qid)
    qid_merges = []
    qid_rejected = []
    for qid, members in by_qid.items():
        if len(members) == 1:
            merged_out.append(members[0])
            continue

        # connected components: only merge members that share a core word,
        # directly or transitively -- see shares_core_word() docstring
        groups = []
        for m in members:
            joined = None
            for g in groups:
                if any(shares_core_word(m["name"], other["name"]) for other in g):
                    if joined is None:
                        g.append(m)
                        joined = g
                    else:  # bridges two existing groups -> merge them
                        joined.extend(g)
                        groups.remove(g)
            if joined is None:
                groups.append([m])

        for group in groups:
            if len(group) == 1:
                merged_out.append(group[0])
                continue
            names = [m["name"] for m in group]
            canonical = pick_canonical(names)
            qid_merges.append((canonical, sorted(set(names) - {canonical})))
            appearances = []
            seen = set()
            for m in group:
                for a in m["appearances"]:
                    key = (a["season"], a.get("distFromFinal"))
                    if key not in seen:
                        seen.add(key)
                        appearances.append(a)
            appearances.sort(key=lambda a: a["season"])
            best_dist = min(a["distFromFinal"] for a in appearances)
            base = next(m for m in group if m["name"] == canonical)
            merged_out.append({
                "name": canonical,
                "bestRound": DIST_LABELS.get(best_dist, appearances[0]["roundName"]),
                "bestDistFromFinal": best_dist,
                "seasonsPlayed": len(appearances),
                "appearances": appearances,
                "lat": base["lat"], "lon": base["lon"],
                "geocodeSource": base.get("geocodeSource"),
                **({"country": base["country"]} if "country" in base else {}),
            })
        if len(groups) > 1:
            qid_rejected.append((qid, [m["name"] for m in members]))

    merged_out.sort(key=lambda c: c["name"])
    (DATA / "clubs_final.json").write_text(
        json.dumps(merged_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA / "clubs_still_missing_coords.json").write_text(
        json.dumps(still_missing, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"clubs_final.json: {len(merged_out)} clubs with coordinates "
          f"({len(qid_merges)} merged by matching Wikidata entity)")
    print(f"clubs_still_missing_coords.json: {len(still_missing)} clubs "
          f"geocoding couldn't resolve")
    if qid_merges:
        print("\nQID merges:")
        for canonical, variants in qid_merges[:20]:
            print(f"  {canonical!r} <- {variants}")
    if qid_rejected:
        print(f"\n{len(qid_rejected)} QID group(s) shared a Wikidata entity but had no "
              f"core-word overlap -- kept separate rather than risk a wrong merge:")
        for qid, names in qid_rejected:
            print(f"  {qid}: {names}")


if __name__ == "__main__":
    main()
