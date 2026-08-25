"""
Builds data/clubs.json from data/finals_raw.json + data/club_coords.json.

This is the v0 pipeline: it only knows about European Cup / Champions League
FINALS (1955-56 to present), so "best result" here only distinguishes
Winner vs Runner-up. The full drill-down (round-of-16, quarterfinal, group
stage participation etc. for every club that ever entered, not just
finalists) needs a richer source -- see ROADMAP.md.

Run:  python scripts/build_data.py
"""
import re
import json
from pathlib import Path
from collections import defaultdict

from scrape_rsssf import fetch, season_slug, extract_cc_section, find_round_headers, clean_line

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# --- goal-scorer extraction (reuses the RSSSF Final-round text already
# cached by scrape_rsssf.py -- no extra network calls). We deliberately
# don't attribute scorers to a team: RSSSF's two goal-line formats (an
# explicit "TeamAbbrev:" prefix pre-2000s, a bracketed "[min' Name, ...]"
# list from the 2000s on) would need a fragile heuristic to split reliably,
# and a wrong team label is worse than no team label. A flat chronological
# list is still useful and never mis-attributes anyone.
_STOP_RE = re.compile(r"^(Referee|Attendance|Penalty shoot|.*\(trainer)", re.I)
_SKIP_RE = re.compile(r"(won|win) .* on penalties", re.I)
_BRACKET_RE = re.compile(r"^\[(.+)\]$")
_OLD_GOAL_RE = re.compile(r"^(\d+)['+,]*\s+[\d\-]+\s+[A-Za-z]{1,4}:\s*(.+)$")
_BRACKET_ITEM_RE = re.compile(r"(\d+)['+,]*\s*(.+)")


def extract_scorers(start_year: int):
    """Return e.g. 'Di Stéfano 14\', 79\' · Rial 30\' · Marquitos 67\'' or
    '' if there were no goals in normal play (e.g. a 0-0 decided on pens)."""
    html = fetch(season_slug(start_year))
    section = extract_cc_section(html)
    lines = section.split("\n")
    headers = find_round_headers(lines)
    if not headers:
        return ""
    final_start = headers[-1][0]
    chunk = [clean_line(l) for l in lines[final_start: final_start + 40]]
    chunk = [l for l in chunk if l]
    if len(chunk) < 2:
        return ""

    raw_goals = []
    for line in chunk[1:]:  # [0] is the round header itself
        if _STOP_RE.match(line):
            break
        if _SKIP_RE.search(line):
            continue
        m = _BRACKET_RE.match(line)
        if m:
            for item in m.group(1).split(","):
                im = _BRACKET_ITEM_RE.match(item.strip())
                if im:
                    raw_goals.append((int(im.group(1)), im.group(2).strip()))
            continue
        m2 = _OLD_GOAL_RE.match(line)
        if m2:
            raw_goals.append((int(m2.group(1)), m2.group(2).strip()))

    # group repeated goals by the same player into "Name 30', 79'"
    by_name = {}
    order = []
    for minute, name in raw_goals:
        name = re.sub(r"\bog\b", "(o.g.)", name)
        if name not in by_name:
            by_name[name] = []
            order.append(name)
        by_name[name].append(minute)

    return " · ".join(
        f"{name} " + ", ".join(f"{m}'" for m in sorted(by_name[name]))
        for name in order
    )


def wiki_url(season: str) -> str:
    """Wikipedia's per-final article, e.g. '1955–56' -> '1956 European Cup
    final', '1999–2000' -> '2000 UEFA Champions League final'. The
    competition was rebranded for the 1992-93 season."""
    start = int(season.split("–")[0])
    final_year = start + 1
    comp = "European Cup final" if start <= 1991 else "UEFA Champions League final"
    return "https://en.wikipedia.org/wiki/" + f"{final_year} {comp}".replace(" ", "_")


def main():
    finals = json.loads((DATA / "finals_raw.json").read_text(encoding="utf-8"))
    coords = json.loads((DATA / "club_coords.json").read_text(encoding="utf-8"))

    clubs = defaultdict(lambda: {"titles": 0, "runnerUps": 0, "appearances": []})

    for f in finals:
        winner, runner = f["winner"], f["runnerUp"]
        start_year = int(f["season"].split("–")[0])
        try:
            scorers = extract_scorers(start_year)
        except Exception:
            scorers = ""  # e.g. season not yet on RSSSF -- degrade gracefully
        clubs[winner]["titles"] += 1
        clubs[winner]["appearances"].append({
            "season": f["season"], "result": "Winner", "score": f["score"],
            "opponent": runner, "venue": f["venue"], "city": f["city"],
            "wikiUrl": wiki_url(f["season"]), "scorers": scorers,
        })
        clubs[runner]["runnerUps"] += 1
        clubs[runner]["appearances"].append({
            "season": f["season"], "result": "Runner-up", "score": f["score"],
            "opponent": winner, "venue": f["venue"], "city": f["city"],
            "wikiUrl": wiki_url(f["season"]), "scorers": scorers,
        })

    missing_coords = sorted(set(clubs) - set(coords))
    if missing_coords:
        raise SystemExit(f"Missing coordinates for: {missing_coords}")

    out = []
    for name, rec in sorted(clubs.items()):
        best = "Winner" if rec["titles"] > 0 else "Runner-up"
        appearances = sorted(rec["appearances"], key=lambda a: a["season"])
        out.append({
            "name": name,
            "country": coords[name]["country"],
            "lat": coords[name]["lat"],
            "lon": coords[name]["lon"],
            "bestResult": best,
            "titles": rec["titles"],
            "runnerUps": rec["runnerUps"],
            "appearances": appearances,
        })

    (DATA / "clubs.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(out)} clubs to data/clubs.json")

if __name__ == "__main__":
    main()
