"""
Step 1 of the Wikipedia pipeline: cache the SECTION HEADINGS of every
season article (1955-56 -> present) to data/wiki_sections.json.

This exists so the era/round table can be built from what Wikipedia
actually contains rather than from memory. The previous RSSSF pipeline
tried to infer what a round MEANT from its position relative to the final,
which broke every time the competition changed shape -- Víkingur's 1992-93
first-round exit was reported as a quarterfinal, small clubs' preliminary
mini-tournaments were read as the real semifinal and final, and so on.
Here the round is whatever Wikipedia's own section heading says it is.

Article titles follow two conventions, split at the 1992-93 rebrand:
  1955-56 .. 1991-92   "{season} European Cup"
  1992-93 ..           "{season} UEFA Champions League"
plus, for recent seasons, a separate "... knockout phase" article the main
one transcludes from.

Wikipedia rate-limits (HTTP 429), so requests are paced and the result is
cached -- rerun freely, it only fetches what's missing.

Run:  python scripts/wiki_fetch_sections.py
"""
import json
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "wiki_sections.json"

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "ucl-history-map/1.0 (github.com/andre2694/ucl-history-map)"}
DELAY = 2.2  # Wikipedia 429s at ~1/s over a long run
FIRST_SEASON, LAST_SEASON = 1955, 2025


def season_label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def article_titles(start: int):
    """(main article, knockout article or None) for a season."""
    en = f"{start}–{(start + 1) % 100:02d}"
    if start <= 1991:
        return f"{en} European Cup", None
    return (f"{en} UEFA Champions League",
            f"{en} UEFA Champions League knockout phase")


def api(params):
    last = None
    for attempt in range(6):
        try:
            resp = requests.get(API, params={**params, "format": "json"},
                                headers=HEADERS, timeout=45)
        except requests.RequestException as e:
            last = e
        else:
            if resp.status_code == 200:
                time.sleep(DELAY)
                return resp.json()
            last = f"HTTP {resp.status_code}"
        time.sleep(min(120, 5 * (2 ** attempt)))
    # Don't abort the whole run for one page. Wikipedia rate-limits hard
    # over a long sequence of requests, and the cache is written per
    # season, so returning None lets the run continue and a re-run pick up
    # exactly what's still missing.
    print(f"  ! giving up on {params.get('page')} ({last}) -- will retry on re-run")
    return None


def fetch_sections(page):
    data = api({"action": "parse", "page": page, "prop": "sections"})
    if data is None or "error" in data:
        return None
    return [{"index": s["index"], "level": int(s["level"]), "line": s["line"]}
            for s in data["parse"]["sections"]]


def main():
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    for start in range(FIRST_SEASON, LAST_SEASON + 1):
        label = season_label(start)
        if label in cache:
            continue
        main_title, ko_title = article_titles(start)
        entry = {"main": {"title": main_title, "sections": fetch_sections(main_title)}}
        if entry["main"]["sections"] is None:
            print(f"{label}: MAIN ARTICLE MISSING ({main_title})")
        if ko_title:
            ko_sections = fetch_sections(ko_title)
            if ko_sections:
                entry["knockout"] = {"title": ko_title, "sections": ko_sections}
        if entry["main"]["sections"] is None:
            continue  # leave uncached so a re-run retries it
        cache[label] = entry
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        n_main = len(entry["main"]["sections"] or [])
        n_ko = len(entry.get("knockout", {}).get("sections", []))
        print(f"{label}: {n_main} sections" + (f" + {n_ko} knockout" if n_ko else ""))

    print(f"\nCached {len(cache)} seasons to {CACHE.name}")


if __name__ == "__main__":
    main()
