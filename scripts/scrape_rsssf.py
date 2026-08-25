"""
v1 scraper: pulls full round-by-round participation from RSSSF's European
Cup / Champions League season archive (https://www.rsssf.org/ec/).

Strategy (see ROADMAP.md "v1"):
  RSSSF's round-header anchor naming drifts across 70 years of hand-edited
  HTML (`ccqf` in old pages, `cqf` in new ones; `Quarter-Finals` vs
  `Quarterfinal`; ISO-8859-1 vs UTF-16 encoding...). Rather than enumerate
  every historical label, we rely on the one thing that's always true: the
  **Final is always the last round header on the page**. So every round is
  labelled by its *distance from the Final* (0 = Final, 1 = Semifinal, 2 =
  Quarterfinal, 3 = Round of 16, 4+ = earlier rounds), computed positionally.
  A club's "furthest round" for a season is just the round (by that
  distance) in which its name last appears in a match line.

  We deliberately don't parse scores/winners here -- participation doesn't
  need them, and it sidesteps most of RSSSF's line-format quirks. The one
  place we *do* need a winner (the Final) is cross-checked separately
  against data/finals_raw.json, which is hand-verified.

Caches raw HTML to data/raw_html/ so re-runs don't hammer rsssf.org.
Run:  python scripts/scrape_rsssf.py
"""
import json
import re
import time
import unicodedata
from html import unescape
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW_HTML = DATA / "raw_html"
RAW_HTML.mkdir(exist_ok=True)

BASE = "https://www.rsssf.org/ec/ec{slug}.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (ucl-history-map research scraper)"}

DIST_LABELS = {0: "Final", 1: "Semifinal", 2: "Quarterfinal", 3: "Round of 16"}

# RSSSF's 3-letter country/association codes -- generous by design, since
# unmatched lines are logged for QA rather than silently dropped. Includes
# historical entities (Urs=USSR, Tch=Czechoslovakia, Frg/Gdr=West/East
# Germany, Yug=Yugoslavia) and known spelling variants across eras.
COUNTRY_CODES = {
    "Alb","And","Arm","Aut","Aze","Bel","Bih","Bls","Blr","Bos","Bul","Cro",
    "Cyp","Cze","Den","Eng","Esp","Est","Fin","Fra","Fro","Far","Frg","Gdr",
    "Geo","Ger","Gib","Grc","Grk","Gre","Hun","Ire","Irl","Isl","Isr","Ita",
    "Kaz","Kos","Lat","Lit","Ltu","Lux","Mac","Mda","Mkd","Mlt","Mne","Mng",
    "Mol","Ned","Net","Nir","Nor","Pol","Por","Rom","Rus","Sco","Slo","Sln",
    "Sma","Smr","Srb","Sui","Svk","Svn","Swe","Tch","Tur","Ukr","Urs","Wal",
    "Yug","Fyr",
}
# code preceded by whitespace OR a lowercase/accented letter (handles RSSSF's
# occasional missing-space typos like "AmmóchostosCyp"), followed by
# whitespace/end.
CC_RE = re.compile(
    r"(?<=[\sa-zà-öø-ÿ])(" + "|".join(sorted(COUNTRY_CODES, key=len, reverse=True)) + r")(?=\s|$)",
    re.IGNORECASE,
)

TAG_RE = re.compile(r"<[^>]+>")
NOTE_REF_RE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹¹²³]")
GROUP_SUBHEADER_RE = re.compile(r"^Group\s+[A-Za-z0-9]+$", re.I)
# (HT-score) lines used for group-stage / old-final match reports, with an
# optional leading "Sep 14: " date prefix.
HT_SCORE_RE = re.compile(
    r"^(?:[A-Za-z]{3,9}\.?\s*\d{1,2}:\s*)?(.+?)\s+\(\d+\)\s*\d+\s+(.+?)\s+\(\d+\)\s*\d+"
)
# Round-header vocabulary, matched against a *fully cleaned, whole* line --
# works whether or not the original HTML wrapped it in an <a name> anchor,
# so it's robust across RSSSF's 70 years of formatting drift.
ROUND_HEADER_RE = re.compile(
    r"^(Preliminary Round|Qualifying (Round|Phase) ?\d*|Play-?offs?( Round)?|"
    r"Group (Phase|Stage) ?\d*|1/\d+[\s-]?Finals?|Round of \d+|"
    r"(First|Second|Third|Fourth) Round|Quarter-?finals?|Semi-?finals?|Final)\b",
    re.I,
)


def season_slug(start_year: int) -> str:
    """e.g. 1955 -> '195556', 1999 -> '199900', 2020 -> '202021'."""
    end2 = (start_year + 1) % 100
    return f"{start_year}{end2:02d}"


def _decode(raw: bytes) -> str:
    """RSSSF pages are inconsistently encoded: older ones are ISO-8859-1,
    newer ones are UTF-16 (with or without a BOM). Sniff and decode."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    sample = raw[:200]
    if sample.count(b"\x00") > len(sample) * 0.3:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("iso-8859-1", errors="replace")


def fetch(slug: str) -> str:
    cache = RAW_HTML / f"ec{slug}.html"
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="replace")
    url = BASE.format(slug=slug)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    text = _decode(resp.content)
    cache.write_text(text, encoding="utf-8")  # normalize cache to utf-8
    time.sleep(1.0)  # be polite -- this is a small volunteer-run archive
    return text


def clean_line(raw: str) -> str:
    line = TAG_RE.sub(" ", raw)
    line = unescape(line)
    line = unicodedata.normalize("NFC", line)
    return re.sub(r"\s+", " ", line).strip()


def extract_cc_section(html: str) -> str:
    """Isolate the Champions League block: from the 'NAME="cc"' anchor up
    to the next competition's <H2> (UEFA/Europa/Conference/Super Cup)."""
    m = re.search(r'name="cc"', html, re.I)
    if not m:
        return ""
    start = m.start()
    m2 = re.search(r"<H2", html[start + 10:], re.I)
    end = start + 10 + m2.start() if m2 else len(html)
    return html[start:end]


def find_round_headers(lines):
    """Return ordered list of (line_index, round_text) for header lines,
    excluding group sub-headers ('Group A')."""
    headers = []
    for i, raw in enumerate(lines):
        text = clean_line(raw)
        if not text or GROUP_SUBHEADER_RE.match(text):
            continue
        if ROUND_HEADER_RE.match(text):
            headers.append((i, text))
    # drop a trailing 3rd-place-playoff header if present -- Final must be last
    while headers and re.search(r"third[\s-]place|3rd[\s-]place", headers[-1][1], re.I):
        headers.pop()
    return headers


def parse_clubs_from_line(raw_line: str):
    """Return (clubA, clubB) for a group-stage or knockout match/tie line."""
    line = clean_line(raw_line)
    if not line or ROUND_HEADER_RE.match(line):
        return None

    m = HT_SCORE_RE.match(line)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        return (a, b) if a and b and len(a) > 1 and len(b) > 1 else None

    if not CC_RE.search(line):
        return None
    score_start = re.search(r"\(\d+\)\s*\d+|\d+\s*-\s*\d+", line)
    head = line[: score_start.start()] if score_start else line
    codes = list(CC_RE.finditer(head))
    if len(codes) < 2:
        return None
    a = NOTE_REF_RE.sub("", head[: codes[0].start()]).strip(" -¹")
    b = NOTE_REF_RE.sub("", head[codes[0].end(): codes[1].start()]).strip(" -¹")
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    return a, b


def parse_season(start_year: int):
    slug = season_slug(start_year)
    html = fetch(slug)
    section = extract_cc_section(html)
    if not section:
        return None, {"season": start_year, "error": "no CL section found"}

    lines = section.split("\n")
    headers = find_round_headers(lines)
    if not headers:
        return None, {"season": start_year, "error": "no round headers found"}

    final_idx = len(headers) - 1
    boundaries = [h[0] for h in headers] + [len(lines)]
    round_names = [h[1] for h in headers]

    club_round = {}
    parsed_count = 0
    unparsed = []

    for i, line_start in enumerate(boundaries[:-1]):
        for raw_line in lines[line_start: boundaries[i + 1]]:
            pair = parse_clubs_from_line(raw_line)
            if pair:
                parsed_count += 1
                for club in pair:
                    if club not in club_round or i > club_round[club]:
                        club_round[club] = i
            else:
                stripped = clean_line(raw_line)
                is_header = ROUND_HEADER_RE.match(stripped)
                if stripped and len(stripped) > 3 and not is_header and not re.match(r"^[\-=\s]*$", stripped):
                    unparsed.append(stripped)

    result = {}
    for club, idx in club_round.items():
        dist = final_idx - idx
        result[club] = {
            "roundName": DIST_LABELS.get(dist, round_names[idx]),
            "distFromFinal": dist,
        }

    return result, {
        "season": start_year,
        "rounds_found": round_names,
        "matches_parsed": parsed_count,
        "unparsed_lines": len(unparsed),
        "sample_unparsed": unparsed[:6],
    }


def main():
    all_seasons = {}
    qa_report = []
    for start_year in range(1955, 2026):
        clubs, qa = parse_season(start_year)
        label = f"{start_year}-{(start_year+1)%100:02d}"
        if clubs:
            all_seasons[label] = clubs
        qa_report.append(qa)
        print(f"{label}: {qa.get('matches_parsed', 0)} matches, "
              f"{len(clubs) if clubs else 0} clubs, "
              f"{qa.get('unparsed_lines', 0)} unparsed"
              + (f"  [{qa['error']}]" if "error" in qa else ""))

    (DATA / "participation_raw.json").write_text(
        json.dumps(all_seasons, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA / "scrape_qa_report.json").write_text(
        json.dumps(qa_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {len(all_seasons)} seasons to data/participation_raw.json")


if __name__ == "__main__":
    main()
