"""
Builds data/clubs.json from data/finals_raw.json + data/club_coords.json.

This is the v0 pipeline: it only knows about European Cup / Champions League
FINALS (1955-56 to present), so "best result" here only distinguishes
Winner vs Runner-up. The full drill-down (round-of-16, quarterfinal, group
stage participation etc. for every club that ever entered, not just
finalists) needs a richer source -- see ROADMAP.md.

Run:  python scripts/build_data.py
"""
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

def main():
    finals = json.loads((DATA / "finals_raw.json").read_text(encoding="utf-8"))
    coords = json.loads((DATA / "club_coords.json").read_text(encoding="utf-8"))

    clubs = defaultdict(lambda: {"titles": 0, "runnerUps": 0, "appearances": []})

    for f in finals:
        winner, runner = f["winner"], f["runnerUp"]
        clubs[winner]["titles"] += 1
        clubs[winner]["appearances"].append({
            "season": f["season"], "result": "Winner", "score": f["score"],
            "opponent": runner, "venue": f["venue"], "city": f["city"],
        })
        clubs[runner]["runnerUps"] += 1
        clubs[runner]["appearances"].append({
            "season": f["season"], "result": "Runner-up", "score": f["score"],
            "opponent": winner, "venue": f["venue"], "city": f["city"],
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
