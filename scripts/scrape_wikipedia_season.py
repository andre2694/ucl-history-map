"""
!! WORK IN PROGRESS -- NOT WIRED INTO THE PIPELINE. !!

Superseded in design by the era-table approach (see ROADMAP.md "v2"):
this script still infers rounds from a single generic season shape, which
is the exact pattern that produced the round-labelling bugs. Kept because
its Wikipedia plumbing (section lookup, club-link extraction, football-box
vs wikitable handling) is the groundwork for the rewrite.

Known incomplete: a rate-limit (HTTP 429) mid-run leaves the Final
unscraped, which silently demotes the two finalists to semifinalists. Its
output was deliberately NOT merged into participation_raw.json for that
reason.

Scrapes ONE season of the Champions League from Wikipedia into the same
shape scrape_rsssf.py produces, and merges it into
data/participation_raw.json.

WHY, given we already scrape RSSSF: RSSSF is the only free source with
uniform depth back to 1955, but it lags the football calendar by many
months -- the season that just finished simply isn't there yet. Wikipedia
has it immediately, in clean per-round wikitables. So: RSSSF remains the
backbone for history, Wikipedia fills the current season until RSSSF
publishes it. (Wikipedia is also cleaner per-season, but its 70 season
articles vary in structure far more than RSSSF's do, so it isn't a
drop-in replacement for the whole archive.)

Round names are emitted already canonicalised (Qualifying / Group stage /
Knockout play-off / Round of 16 / Quarterfinal / Semifinal / Final) to
match canonical_round() in build_full_data.py, and distFromFinal counts
back from the final exactly as the RSSSF scraper does.

Run:  python scripts/scrape_wikipedia_season.py            # default season
      python scripts/scrape_wikipedia_season.py 2025-26
"""
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "ucl-history-map/1.0 (github.com/andre2694/ucl-history-map)"}

# Ordered oldest -> newest. Each entry is (canonical round name, article,
# section heading to find). The modern "league phase" format is what
# 2024-25 onward uses; the knockout rounds live in a separate article.
def season_plan(season: str):
    en_dash = season.replace("-", "–")
    main = f"{en_dash} UEFA Champions League"
    ko = f"{en_dash} UEFA Champions League knockout phase"
    return [
        ("Qualifying",        main, "First qualifying round"),
        ("Qualifying",        main, "Second qualifying round"),
        ("Qualifying",        main, "Third qualifying round"),
        ("Qualifying",        main, "Play-off round"),
        ("Group stage",       main, "Table"),
        ("Knockout play-off", ko,   "Knockout phase play-offs"),
        ("Round of 16",       ko,   "Round of 16"),
        ("Quarterfinal",      ko,   "Quarter-finals"),
        ("Semifinal",         ko,   "Semi-finals"),
        ("Final",             ko,   "Final"),
    ]


_session = requests.Session()
_section_cache = {}
HTML_CACHE = DATA / "wiki_section_html"


def _cache_path(page, heading):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{page}__{heading}")[:120]
    return HTML_CACHE / f"{safe}.html"


def _api(params):
    last = None
    for attempt in range(6):
        try:
            resp = _session.get(API, params={**params, "format": "json"},
                                headers=HEADERS, timeout=45)
        except requests.RequestException as e:
            last = e
            time.sleep(min(90, 5 * (2 ** attempt)))
            continue
        if resp.status_code == 200:
            time.sleep(2.0)     # Wikipedia 429s readily over a run of requests
            return resp.json()
        last = f"HTTP {resp.status_code}"
        time.sleep(min(90, 5 * (2 ** attempt)))
    raise RuntimeError(f"Wikipedia API failed ({last}): {params}")


def sections_of(page):
    if page not in _section_cache:
        data = _api({"action": "parse", "page": page, "prop": "sections"})
        _section_cache[page] = data["parse"]["sections"]
    return _section_cache[page]


def section_html(page, heading):
    """HTML of the first section whose heading matches, else None.
    Cached to disk -- Wikipedia rate-limits aggressively, and refining the
    parser shouldn't mean re-downloading everything each attempt."""
    HTML_CACHE.mkdir(exist_ok=True)
    cached = _cache_path(page, heading)
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    for s in sections_of(page):
        if s["line"].strip().lower() == heading.lower():
            data = _api({"action": "parse", "page": page,
                         "section": s["index"], "prop": "text"})
            html = data["parse"]["text"]["*"]
            cached.write_text(html, encoding="utf-8")
            return html
    return None


# Cell text that is a score, a date, a position number etc. -- never a club
_NOT_A_CLUB = re.compile(r"^[\d\s\-–—:().]*$|^[A-Z]?\d+$|match|report|agg", re.I)


def clubs_in_html(html):
    """Every club named in a section's wikitables.

    Club cells render as a flag icon plus a link to the club's article, so
    we take link titles rather than cell text: the visible text is often
    abbreviated ("Arsenal") while the title is canonical ("Arsenal F.C."),
    and it avoids picking up scores, dates and footnote markers. Links to
    associations, competitions and other seasons are filtered out."""
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    # "wikitable" covers tie/standings tables; the final (and other single
    # matches) render as a football-box "fevent" table instead, which is
    # why looking only for wikitables found no finalists at all.
    tables = soup.find_all("table", class_="wikitable") + soup.find_all("table", class_="fevent")
    for table in tables:
        for a in table.find_all("a"):
            title = (a.get("title") or "").strip()
            text = a.get_text(" ", strip=True)
            if not title or not text or _NOT_A_CLUB.match(text):
                continue
            # Knockout sections also list goalscorers, substitutes and rule
            # articles ("Penalty shoot-out"), which is how the round of 16
            # came back with 71 "clubs". Wikipedia renders a TEAM with a
            # flag icon beside it and nothing else in these tables does, so
            # require the link's own cell to carry one.
            cell = a.find_parent(["td", "th"])
            if cell is None or not cell.find(class_="flagicon"):
                continue
            low = title.lower()
            if any(bad in low for bad in (
                    "football association", "uefa", "champions league",
                    "europa league", "talk:", "special:", "template:",
                    "stadium", "arena", "wikipedia:", "list of", "federation",
                    "premier league", "la liga", "serie a", "bundesliga",
                    "ligue 1", "eredivisie", "primeira liga")):
                continue
            found.add(title)
    return found


def strip_disambiguator(title: str) -> str:
    """'Arsenal F.C.' -> 'Arsenal', 'FC Bayern Munich' -> 'Bayern Munich'.
    Keeps a parenthetical only when it disambiguates two real clubs."""
    name = re.sub(r"\s*\((?:football club|association football club)\)\s*$", "", title, flags=re.I)
    name = re.sub(r"\s+(F\.?C\.?|A\.?F\.?C\.?|S\.?C\.?|B\.?K\.?|F\.?K\.?)\s*$", "", name)
    name = re.sub(r"^(F\.?C\.?|A\.?F\.?C\.?|S\.?K\.?|F\.?K\.?)\s+", "", name)
    return name.strip()


def scrape_season(season: str):
    plan = season_plan(season)
    final_idx = len(plan) - 1
    club_round = {}

    for idx, (round_name, page, heading) in enumerate(plan):
        html = section_html(page, heading)
        if html is None:
            print(f"  ! section not found: {page} § {heading}")
            continue
        clubs = clubs_in_html(html)
        dist = final_idx - idx
        for raw in clubs:
            name = strip_disambiguator(raw)
            prev = club_round.get(name)
            # a club appears in every round it played, so the LAST (deepest)
            # one it shows up in is the round it actually reached
            if prev is None or dist < prev["distFromFinal"]:
                club_round[name] = {"roundName": round_name, "distFromFinal": dist}
        print(f"  {round_name:18} {heading:26} -> {len(clubs):3} clubs")
    return club_round


def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
    print(f"Scraping {season} from Wikipedia...")
    result = scrape_season(season)
    if not result:
        raise SystemExit("nothing scraped -- check the season/article names")

    # Refuse to write a partial season. A rate limit part-way through once
    # left the Final unscraped, which silently demoted the two finalists to
    # semifinalists -- worse than having no data for the season at all.
    finalists = [n for n, v in result.items() if v["roundName"] == "Final"]
    if len(finalists) != 2:
        raise SystemExit(
            f"refusing to write: expected 2 finalists, found {len(finalists)} "
            f"({finalists}). The run was probably cut short -- try again.")

    path = DATA / "participation_raw.json"
    participation = json.loads(path.read_text(encoding="utf-8"))
    participation[season] = result
    # keep seasons in chronological order
    ordered = {k: participation[k] for k in sorted(participation)}
    path.write_text(json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8")

    by_round = {}
    for v in result.values():
        by_round[v["roundName"]] = by_round.get(v["roundName"], 0) + 1
    print(f"\n{len(result)} clubs for {season}: {by_round}")
    print(f"Merged into {path.name}")


if __name__ == "__main__":
    main()
