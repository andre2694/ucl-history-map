"""
Fetches the goalscorers of a final from Wikipedia into
data/finals_scorers_override.json.

RSSSF is the scorer source for every season it publishes, but it lags the
football calendar by many months, so the season that just finished has a
final with no scorers. Wikipedia has it immediately, in a football-box
table whose cells are explicitly marked home/away (fhome/fhgoal,
faway/fagoal) -- which gives exact team attribution rather than the
squad-matching heuristics the RSSSF path needs.

build_data.py consults this file only where its own extraction came back
empty, so RSSSF stays authoritative wherever it has the data.

Run:  python scripts/fetch_final_scorers_wikipedia.py 2025-26
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
OUT = DATA / "finals_scorers_override.json"
API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "ucl-history-map/1.0 (github.com/andre2694/ucl-history-map)"}


def article_for(season: str) -> str:
    """'2025-26' -> '2026 UEFA Champions League final'."""
    end = int(season.split("-")[0]) + 1
    return f"{end} UEFA Champions League final"


def api(params):
    for attempt in range(5):
        resp = requests.get(API, params={**params, "format": "json"},
                            headers=HEADERS, timeout=45)
        if resp.status_code == 200:
            time.sleep(1.5)
            return resp.json()
        time.sleep(min(60, 5 * (2 ** attempt)))
    raise RuntimeError(f"Wikipedia API failed: {params.get('page')}")


def tidy(text: str) -> str:
    """"Dembélé 65'\xa0( pen. )" -> "Dembélé 65' (pen.)"."""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\(\s*", "(", text)
    text = re.sub(r"\s*\)", ")", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,")


def scrape(season: str):
    page = article_for(season)
    html = api({"action": "parse", "page": page, "prop": "text"})["parse"]["text"]["*"]
    box = BeautifulSoup(html, "html.parser").find("table", class_="fevent")
    if not box:
        raise SystemExit(f"no football-box found on {page}")
    cell = lambda cls: box.find("td", class_=cls) or box.find("th", class_=cls)
    home, away = cell("fhome"), cell("faway")
    hg, ag = cell("fhgoal"), cell("fagoal")
    if not (home and away):
        raise SystemExit(f"couldn't identify the two sides on {page}")
    return {
        "homeTeam": home.get_text(" ", strip=True),
        "awayTeam": away.get_text(" ", strip=True),
        "homeScorers": tidy(hg.get_text(" ", strip=True)) if hg else "",
        "awayScorers": tidy(ag.get_text(" ", strip=True)) if ag else "",
        "source": f"wikipedia:{page}",
    }


def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"
    key = season.replace("-", "–")            # match finals_raw.json's en dash
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    data[key] = scrape(season)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{key}: {data[key]['homeTeam']} [{data[key]['homeScorers']}] "
          f"v {data[key]['awayTeam']} [{data[key]['awayScorers']}]")
    print(f"-> {OUT.name}")


if __name__ == "__main__":
    main()
