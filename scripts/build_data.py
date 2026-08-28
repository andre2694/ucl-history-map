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
from build_full_data import ALIASES

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# --- goal-scorer extraction (reuses the RSSSF Final-round text already
# cached by scrape_rsssf.py -- no extra network calls). Team attribution:
#   - pre-2000s lines carry an explicit "TeamAbbrev:" prefix. We map the two
#     abbrevs to winner/runner by matching goal counts against the known
#     scoreline (from finals_raw.json), falling back to initials overlap
#     when the scoreline is tied (e.g. 1-1 before penalties).
#   - 2000s-on lines are a bracketed "[min' Name, ...]" list with no team
#     marker, so we match each scorer's surname against the two post-match
#     squad-list blocks ("Club (trainer ...)" followed by the XI).
# Every final is validated at the end of main(): summed attributed goals per
# side must equal the known score, or team labels are dropped for that final
# rather than risk showing a wrong one (see VALIDATION output).
_SQUAD_HEADER_RE = re.compile(r"^(.+?)\s*\(trainer.*?\)\s*$", re.I)
# Older pages use "Club: Player1, Player2, ..." instead of "Club (trainer X)".
# Guarded to not collide with old-style goal lines, which always start with
# a minute (a digit), never a letter.
# The colon may end the line ("Liverpool:" with the XI on the next line),
# so don't require whitespace after it -- that miss left the 2001-02 and
# 2004-05 finals with no squads to attribute their scorers against.
_SQUAD_HEADER_RE2 = re.compile(r"^[A-Za-zÀ-ÿ][\w\.\-À-ÿ ]{2,35}:(\s|$)")
_END_OF_SQUADS_RE = re.compile(r"^(Referee|Attendance|Penalty|Index|NB:|Additional Match Details)", re.I)
_COACH_LINE_RE = re.compile(r"^(Tr\.?|Trainer|Coach)\s*:", re.I)
# "Label: ..." lines that are NOT a team's line-up. Without this, "yellow
# cards: Salgado (45+2), ..." was taken as the first squad of the 2001-02
# final, so its scorers had only bookings to match against.
_NOT_A_SQUAD_RE = re.compile(
    r"^((yellow|red)\s+cards?|penalt|referee|attendance|goals?|scorers?|"
    r"booked|sent\s+off|note|nb)", re.I)
_SKIP_RE = re.compile(r"(won|win) .* on penalties", re.I)
_BRACKET_RE = re.compile(r"^\[(.+)\]$")
_OLD_GOAL_RE = re.compile(r"^(\d+)['+,]*\s+[\d\-]+\s+([A-Za-z]{1,4}):\s*(.+)$")
# minute, optionally with stoppage time ("90+7"); captured as a string (not
# int) so display can keep the "+7" precision -- see _minute_sort_key()
_MIN = r"\d+(?:\+\d+)?"
_BRACKET_ITEM_RE = re.compile(rf"^({_MIN})[.'+,]*\s*([A-Za-zÀ-ÿ].*)$")
# rarer variant: "Filippo Inzaghi 45" (name first, one minute)
_BRACKET_NAME_FIRST_RE = re.compile(rf"^([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'.\- ]*?)\s+({_MIN})['.+]*$")
_BARE_MINUTE_RE = re.compile(rf"^({_MIN})['.+]*$")


def _minute_sort_key(m):
    return int(str(m).split("+")[0])
_PEN_ANNOTATION_RE = re.compile(r"\s*\(?pen\.?\)?\s*$", re.I)


def _is_squad_header(line: str) -> bool:
    if _NOT_A_SQUAD_RE.match(line):
        return False
    return bool(_SQUAD_HEADER_RE.match(line) or _SQUAD_HEADER_RE2.match(line))


def _squad_header_name(line: str) -> str:
    m = _SQUAD_HEADER_RE.match(line)
    if m:
        return m.group(1).strip()
    return line.split(":", 1)[0].strip()
_OG_RE = re.compile(r"\bog\b", re.I)
_STOPWORDS = {"de", "of", "the", "fc", "sc", "sk", "ac", "cf", "cd", "as"}


def _fold(s: str) -> str:
    """Accent-insensitive, lowercase compare key (Bayern München ~ Munich
    still won't match -- that's a translation, not an accent -- but Vinícius
    ~ Vinicius, Kögl ~ Kogl, etc. will)."""
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _initials(name: str) -> str:
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]+", name) if w.lower() not in _STOPWORDS]
    return "".join(w[0] for w in words).upper()


def _guess_side_by_initials(abbrev: str, winner: str, runner: str) -> str:
    def overlap(name):
        ini = _initials(name)
        return sum(1 for a, b in zip(abbrev.upper(), ini) if a == b)
    return "winner" if overlap(winner) >= overlap(runner) else "runner"


def extract_scorers(start_year: int, winner: str, runner: str, winner_goals: int, runner_goals: int):
    """Return [{'minute': int, 'name': str, 'side': 'winner'|'runner'|None}]
    for goals scored in normal play (shootout attempts excluded)."""
    html = fetch(season_slug(start_year))
    section = extract_cc_section(html)
    lines = section.split("\n")
    headers = find_round_headers(lines)
    if not headers:
        return []
    final_start = headers[-1][0]
    chunk = [clean_line(l) for l in lines[final_start: final_start + 80]]
    chunk = [l for l in chunk if l]
    if len(chunk) < 2:
        return []

    def _join_wrapped_brackets(lines):
        """RSSSF wraps a long scorer list across lines:
            [RAUL 8, ZIDANE 45;
            LUCIO 14]
        Neither half matches a [..] on one line, so all three of the
        2001-02, 2004-05 and 2013-14 finals silently lost every scorer.
        Glue an unclosed bracket to the lines that follow it."""
        out, buf = [], None
        for line in lines:
            if buf is not None:
                buf += " " + line
                if "]" in line:
                    out.append(buf)
                    buf = None
                continue
            if line.startswith("[") and "]" not in line:
                buf = line
                continue
            out.append(line)
        if buf is not None:
            out.append(buf)
        return out

    goal_lines, squad_lines, squad_start = [], [], None
    for i, line in enumerate(chunk[1:]):
        if _is_squad_header(line):
            squad_start = i
            break
        if _SKIP_RE.search(line):
            continue
        goal_lines.append(line)
    if squad_start is not None:
        squad_lines = chunk[1 + squad_start:]

    # --- squad blocks, for attributing bracket-style (no-abbrev) scorers
    squads = {}  # club header text -> lowercase roster text
    current = None
    for line in squad_lines:
        if _END_OF_SQUADS_RE.match(line):
            break
        if _COACH_LINE_RE.match(line):
            continue
        if _is_squad_header(line):
            current = _squad_header_name(line)
            tail = line.split(":", 1)[1] if ":" in line else ""
            # A club can head more than one block -- the XI, then again for
            # the penalty shootout. Overwriting left Liverpool's 2004-05
            # entry holding only its five shootout takers, so none of its
            # actual scorers could be matched. Accumulate instead.
            squads[current] = squads.get(current, "") + " " + tail
        elif current:
            squads[current] += " " + line
    header_side = {}
    for header in squads:
        h = _fold(ALIASES.get(header, header))
        if h in _fold(winner) or _fold(winner) in h:
            header_side[header] = "winner"
        elif h in _fold(runner) or _fold(runner) in h:
            header_side[header] = "runner"
    unresolved = [h for h in squads if h not in header_side]
    if len(unresolved) == 1 and len(header_side) == 1:
        header_side[unresolved[0]] = "runner" if "winner" in header_side.values() else "winner"

    folded_rosters = {header: _fold(roster) for header, roster in squads.items()}

    def side_by_squad(name):
        name = _PEN_ANNOTATION_RE.sub("", _OG_RE.sub("", name)).strip()
        base = _fold(name)
        words = base.split()
        if not words:
            return None
        # try, in order of specificity: full name, last two words, last word
        # (covers "Vinicius Junior" -> roster's "Vinicius Jr", "Carlos
        # Alberto" as a two-word surname, then plain single-surname cases)
        candidates = [base]
        if len(words) >= 2:
            candidates.append(" ".join(words[-2:]))
        candidates.append(words[-1])
        if words[-1] in ("junior", "jr"):  # e.g. "Vinicius Junior" vs roster's "Vinicius Jr"
            candidates.append(" ".join(words[:-1] + ["jr"]))
        for header, roster in folded_rosters.items():
            if any(c in roster for c in candidates):
                return header_side.get(header)
        # last resort: transliteration drift between the scorer bracket and
        # the roster (e.g. "Alenichev" vs "Alenitchev") -- match on a long
        # enough prefix of the surname against each roster word
        surname = words[-1]
        if len(surname) >= 5:
            prefix = surname[:5]  # "Alenichev" vs roster's "Alenitchev)," etc.
            for header, roster in folded_rosters.items():
                if any(w.startswith(prefix) for w in roster.split()):
                    return header_side.get(header)
        return None

    # --- parse goal lines
    raw = []  # (minute, name, abbrev|None)
    for line in _join_wrapped_brackets(goal_lines):
        m = _BRACKET_RE.match(line)
        if m:
            # RSSSF has (at least) three bracket sub-formats:
            #  - "59' Pedro"                          (minute-first, one goal)
            #  - "27' Mandzukic; 20' Ronaldo, 64' Ronaldo"  (";" sometimes hints
            #    at a team boundary, but not reliably -- team attribution
            #    really comes from side_by_squad() below, not this split)
            #  - "Filippo Inzaghi 45, 82"              (name-first, 1+ minutes)
            # Comma-separated items are ambiguous on their own: "Inzaghi 45,
            # 82" is one player with two minutes, but "Eto'o 76, Belletti
            # 81" is two different players -- and a bare minute can name its
            # player either *before* it ("Inzaghi 45, 82") or *after*
            # ("20., 63. Doué" = Doué scored at both 20' and 63'). A pending
            # buffer handles both directions: a bare minute with no name yet
            # queues up, and gets flushed onto whichever name comes next.
            # A bare minute's owner depends on which style came before it:
            # after a "Name 45" (name-first) entry, a bare minute is a
            # forward continuation ("Inzaghi 45, 82" -- both his goals).
            # After a "12. Name" (minute-first) entry, a bare minute is
            # NOT a continuation of that name -- it's waiting for whichever
            # name comes *next* ("20., 63. Doué" -- Doué scored both).
            last_name = None
            last_style = None
            pending_minutes = []
            for item in re.split(r"[,;]", m.group(1)):
                item = item.strip()
                if not item:
                    continue
                im = _BRACKET_ITEM_RE.match(item)  # "12. Name" (minute-first, own name)
                if im:
                    name = im.group(2).strip()
                    for pm in pending_minutes:
                        raw.append((pm, name, None))
                    pending_minutes = []
                    raw.append((im.group(1), name, None))
                    last_name, last_style = name, "minute_first"
                    continue
                bare = _BARE_MINUTE_RE.match(item)  # bare: "82" or "20."
                if bare:
                    if last_name and last_style == "name_first":
                        raw.append((bare.group(1), last_name, None))
                    else:
                        pending_minutes.append(bare.group(1))
                    continue
                nf = _BRACKET_NAME_FIRST_RE.match(item)  # "Name 45"
                if nf:
                    last_name = nf.group(1).strip()
                    for pm in pending_minutes:
                        raw.append((pm, last_name, None))
                    pending_minutes = []
                    raw.append((nf.group(2), last_name, None))
                    last_style = "name_first"
            continue
        m2 = _OLD_GOAL_RE.match(line)
        if m2:
            raw.append((int(m2.group(1)), m2.group(3).strip(), m2.group(2)))

    # --- resolve abbrev -> side (old-style)
    abbrevs = sorted({a for _, _, a in raw if a})
    abbrev_side = {}
    if len(abbrevs) == 2:
        a, b = abbrevs
        count_a = sum(1 for _, _, ab in raw if ab == a)
        count_b = sum(1 for _, _, ab in raw if ab == b)
        if {count_a, count_b} == {winner_goals, runner_goals} and count_a != count_b:
            abbrev_side[a] = "winner" if count_a == winner_goals else "runner"
            abbrev_side[b] = "runner" if abbrev_side[a] == "winner" else "winner"
        else:
            abbrev_side[a] = _guess_side_by_initials(a, winner, runner)
            abbrev_side[b] = "runner" if abbrev_side[a] == "winner" else "winner"
    elif len(abbrevs) == 1:
        abbrev_side[abbrevs[0]] = _guess_side_by_initials(abbrevs[0], winner, runner)

    results = []
    for minute, name, abbrev in raw:
        is_og = bool(_OG_RE.search(name))
        clean_name = _OG_RE.sub("(o.g.)", name).strip()
        clean_name = _PEN_ANNOTATION_RE.sub("", clean_name).strip()
        if re.search(r"\bpen\.?\)?$", name, re.I):
            clean_name += " (pen.)"
        if abbrev:
            # the abbrev already names the side that BENEFITED (it's the
            # side whose running score just went up), so no flip needed
            side = abbrev_side.get(abbrev)
        else:
            # side_by_squad finds the OG SCORER's own team via the roster,
            # so here we do need to flip to the side that benefited
            side = side_by_squad(name)
            if is_og and side:
                side = "runner" if side == "winner" else "winner"
        results.append({"minute": minute, "name": clean_name, "side": side})
    return results


def format_scorers(goals, label):
    """goals: list from extract_scorers(); label: 'winner' or 'runner'."""
    by_name, order = {}, []
    for g in goals:
        if g["side"] != label:
            continue
        if g["name"] not in by_name:
            by_name[g["name"]] = []
            order.append(g["name"])
        by_name[g["name"]].append(g["minute"])
    return " · ".join(
        f"{name} " + ", ".join(f"{m}'" for m in sorted(mins, key=_minute_sort_key))
        for name, mins in ((n, by_name[n]) for n in order)
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

    unattributed_finals = []
    for f in finals:
        winner, runner = f["winner"], f["runnerUp"]
        start_year = int(f["season"].split("–")[0])
        # a score string can carry extra context (e.g. "1–1, replay 4–0" for
        # 1973-74's replayed final) -- the LAST pair is always the deciding
        # scoreline, which is also what the scraped Final-round text shows
        pairs = re.findall(r"(\d+)[–-](\d+)", f["score"])
        winner_goals, runner_goals = (int(pairs[-1][0]), int(pairs[-1][1])) if pairs else (0, 0)
        try:
            goals = extract_scorers(start_year, winner, runner, winner_goals, runner_goals)
        except Exception:
            goals = []  # e.g. season not yet on RSSSF -- degrade gracefully

        # validate: attributed goals per side must equal the known score,
        # or we drop team labels for this final rather than risk a wrong one
        won = sum(1 for g in goals if g["side"] == "winner")
        run = sum(1 for g in goals if g["side"] == "runner")
        trustworthy = (won, run) == (winner_goals, runner_goals)
        if not trustworthy and goals:
            unattributed_finals.append((f["season"], (won, run), (winner_goals, runner_goals)))
            for g in goals:
                g["side"] = None

        winner_scorers = format_scorers(goals, "winner")
        runner_scorers = format_scorers(goals, "runner")
        unassigned = format_scorers(goals, None)

        clubs[winner]["titles"] += 1
        clubs[winner]["appearances"].append({
            "season": f["season"], "result": "Winner", "score": f["score"],
            "opponent": runner, "venue": f["venue"], "city": f["city"],
            "wikiUrl": wiki_url(f["season"]),
            "scorers": winner_scorers, "opponentScorers": runner_scorers,
            "unassignedScorers": unassigned,
        })
        clubs[runner]["runnerUps"] += 1
        clubs[runner]["appearances"].append({
            "season": f["season"], "result": "Runner-up", "score": f["score"],
            "opponent": winner, "venue": f["venue"], "city": f["city"],
            "wikiUrl": wiki_url(f["season"]),
            "scorers": runner_scorers, "opponentScorers": winner_scorers,
            "unassignedScorers": unassigned,
        })

    if unattributed_finals:
        print(f"NOTE: {len(unattributed_finals)} finals had scorer counts that didn't "
              f"match the scoreline, so team labels were dropped for those (shown as "
              f"unassignedScorers instead):")
        for season, got, want in unattributed_finals:
            print(f"   {season}: attributed {got}, expected {want}")

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
