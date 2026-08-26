"""
Harvests a club-name -> country mapping from the RSSSF pages already cached
by scrape_rsssf.py (no new network calls). Knockout/qualifying-round lines
always carry an explicit 3-letter country code next to each club name
(group-stage lines don't, so this only sees clubs that played at least one
qualifying/knockout tie -- which is nearly everyone, at least once across
70 seasons). Used to give the geocoder a country to disambiguate against
(e.g. "Sparta" alone is ambiguous; "Sparta, Netherlands" isn't).

Run:  python scripts/extract_country_hints.py
"""
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

from scrape_rsssf import (
    fetch, season_slug, extract_cc_section, find_round_headers, clean_line,
    find_country_codes, NOTE_REF_RE, ROUND_HEADER_RE,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# RSSSF 3-letter code -> country name, for building geocoder queries.
CODE_TO_COUNTRY = {
    "Alb": "Albania", "And": "Andorra", "Arm": "Armenia", "Aut": "Austria",
    "Aze": "Azerbaijan", "Bel": "Belgium", "Bih": "Bosnia and Herzegovina",
    "Bos": "Bosnia and Herzegovina", "Bls": "Belarus", "Blr": "Belarus",
    "Bul": "Bulgaria", "Cro": "Croatia", "Cyp": "Cyprus",
    "Cze": "Czech Republic", "Tch": "Czech Republic", "Den": "Denmark",
    "Eng": "England", "Esp": "Spain", "Est": "Estonia", "Fin": "Finland",
    "Fra": "France", "Fro": "Faroe Islands", "Far": "Faroe Islands",
    "Frg": "Germany", "Gdr": "Germany", "Geo": "Georgia", "Ger": "Germany",
    "Gib": "Gibraltar", "Grc": "Greece", "Grk": "Greece", "Gre": "Greece",
    "Hun": "Hungary", "Ire": "Ireland", "Irl": "Ireland", "Isl": "Iceland",
    "Isr": "Israel", "Ita": "Italy", "Kaz": "Kazakhstan", "Kos": "Kosovo",
    "Lat": "Latvia", "Lit": "Lithuania", "Ltu": "Lithuania",
    "Lux": "Luxembourg", "Mac": "North Macedonia", "Mkd": "North Macedonia",
    "Fyr": "North Macedonia", "Mda": "Moldova", "Mol": "Moldova",
    "Mlt": "Malta", "Mne": "Montenegro", "Mng": "Montenegro",
    "Ned": "Netherlands", "Net": "Netherlands", "Nir": "Northern Ireland",
    "Nor": "Norway", "Pol": "Poland", "Por": "Portugal", "Rom": "Romania",
    "Rus": "Russia", "Sco": "Scotland", "Slo": "Slovenia", "Sln": "Slovenia",
    "Svn": "Slovenia", "Srb": "Serbia", "Yug": "Serbia", "Sui": "Switzerland",
    "Svk": "Slovakia", "Swe": "Sweden", "Tur": "Turkey", "Ukr": "Ukraine",
    "Urs": "Russia", "Wal": "Wales", "Smr": "San Marino", "Sma": "San Marino",
}


def parse_pair_with_codes(raw_line: str):
    line = clean_line(raw_line)
    if not line or ROUND_HEADER_RE.match(line):
        return None
    score_start = re.search(r"\(\d+\)\s*\d+|\d+\s*-\s*\d+", line)
    head = line[: score_start.start()] if score_start else line
    codes = find_country_codes(head)
    if len(codes) < 2:
        return None
    a = NOTE_REF_RE.sub("", head[: codes[0].start()]).strip(" -¹")
    b = NOTE_REF_RE.sub("", head[codes[0].end(): codes[1].start()]).strip(" -¹")
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return (a, codes[0].group(1)), (b, codes[1].group(1))


def main():
    counters = defaultdict(Counter)
    for start_year in range(1955, 2026):
        slug = season_slug(start_year)
        cache = DATA / "raw_html" / f"ec{slug}.html"
        if not cache.exists():
            continue
        html = fetch(slug)  # cache hit, no network
        section = extract_cc_section(html)
        if not section:
            continue
        lines = section.split("\n")
        headers = find_round_headers(lines)
        if not headers:
            continue
        for raw_line in lines[headers[0][0]:]:
            result = parse_pair_with_codes(raw_line)
            if result:
                for name, code in result:
                    counters[name][code] += 1

    hints = {}
    unresolved_codes = Counter()
    for name, code_counts in counters.items():
        code = code_counts.most_common(1)[0][0]
        country = CODE_TO_COUNTRY.get(code.capitalize())
        if country:
            hints[name] = country
        else:
            unresolved_codes[code] += 1

    (DATA / "club_country_hints.json").write_text(
        json.dumps(hints, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"Wrote country hints for {len(hints)} club-name variants")
    if unresolved_codes:
        print(f"Unresolved codes (not in CODE_TO_COUNTRY): {dict(unresolved_codes)}")


if __name__ == "__main__":
    main()
