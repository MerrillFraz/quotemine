#!/usr/bin/env python3
"""
phrases.py — build catchphrase lists for a character-centric pack.

A single-character voicepack lives or dies on catchphrases, and fans have
already indexed them: episode wikis carry a per-episode "Running Gags /
Callbacks" section that is, structurally, a speaker-attributed catchphrase
list. This turns that into a `POOL_FILTERS` phrase block.

Three steps, because each answers a different question:

    scrape  what does the wiki say?    -> projects/_data/<corpus>_wiki.json
    mine    which gags are canon?      -> phrase -> {character: episodes_citing}
    probe   which survive our corpus?  -> a paste-ready POOL_FILTERS block

`mine` and `probe` measure different things and BOTH are needed. The number of
episodes citing a gag is the fan-canonicity signal: "phrasing" is cited across
21 episodes, "danger zone" 11, "sploosh" 6, against a long tail cited once.
Corpus frequency alone is actively misleading — "shut up" (206 hits), "hang on"
(97) and "hello" (53) all scrape as legitimate character gags, and at
PHRASE_BONUS they would swamp every pool with filler. So a phrase ships only if
it is cited by more than one episode AND appears in our corpus AND isn't a
generic filler on the stoplist.

Fan lists are a hypothesis; the transcript database is the evidence.

Usage:
    python pipeline/phrases.py scrape --project archer_wot
    python pipeline/phrases.py mine   --project archer_wot
    python pipeline/phrases.py probe  --project archer_wot --character Archer

Wiki text is CC BY-SA; the attribution is recorded in the scraped JSON header.
Only short phrases are ever extracted, and they are used as search keys against
our own transcripts — no wiki prose ends up in a pack.
"""

import argparse
import collections
import json
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from project import REPO_ROOT, load_project

DATA_DIR = REPO_ROOT / "projects" / "_data"
UA = "quotemine/0.1 (https://github.com/MerrillFraz/quotemine)"

# Generic conversational filler that a wiki will happily list as a "running
# gag" but which is far too common in ordinary dialogue to be a usable search
# key — these have hundreds of corpus hits and would crowd out real
# catchphrases at PHRASE_BONUS. Extend per corpus as you find more.
DEFAULT_STOPLIST = """
shut up
hang on
hello
yes
no
what
why
okay
oh god
oh my god
come on
sorry
wait
me too
seriously
""".split("\n")


# ---------------------------------------------------------------------------
# wikitext helpers
# ---------------------------------------------------------------------------

def strip_markup(s):
    """Flatten wikitext to plain words: piped/plain links, external links,
    bold/italic markers, refs, and templates. Pure; unit-tested."""
    s = re.sub(r"<ref[^>]*>.*?</ref>", " ", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", " ", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)   # [[target|shown]]
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)            # [[shown]]
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)  # [url shown]
    s = re.sub(r"\[https?://\S+\]", " ", s)
    s = re.sub(r"\{\{[^{}]*\}\}", " ", s)
    s = re.sub(r"</?[^>]+>", " ", s)
    s = s.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", s).strip()


def normalize_phrase(s):
    """Canonical key for a catchphrase, so casing, punctuation and elongation
    variants collapse to one row ("Phrasing!", "phrasing", "PHRASING" -> the
    same phrase). Also what gets handed to FTS5. Pure; unit-tested."""
    s = strip_markup(s).lower()
    s = re.sub(r"[^0-9a-z' ]+", " ", s)
    s = re.sub(r"'+", "'", s)
    return re.sub(r"\s+", " ", s).strip(" '")


HEADING_RE = re.compile(r"^==+(?P<title>[^=\n]+?)==+\s*$", re.M)


def section(wikitext, pattern):
    """Body of the first section whose heading matches `pattern`, up to the next
    heading. Headings are markup-stripped and lowercased before matching,
    because hand-edited wikis spell the same section many ways — the Archer wiki
    alone has 'Running Gags / Callbacks', 'Running Gags/Callbacks',
    '[[Running Gags]] / Callbacks', a double space, and 'Runnings Gags'. Match
    on a keyword, not an exact title. "" when absent. Pure; unit-tested."""
    rx = re.compile(pattern, re.I)
    for m in HEADING_RE.finditer(wikitext):
        if not rx.search(strip_markup(m.group("title")).lower()):
            continue
        start = m.end()
        nxt = HEADING_RE.search(wikitext, start)
        return wikitext[start:nxt.start()] if nxt else wikitext[start:]
    return ""


# Section-heading keywords, tolerant of the spelling drift above.
GAGS_SECTION = r"gag|callback"
QUOTES_SECTION = r"^quotes$"


# Bulleted gag: *'''Get some!''' - Pam        (dash optional, nesting allowed)
GAG_RE = re.compile(r"^\*+\s*'''(?P<phrase>.+?)'''\s*[-–—:]?\s*(?P<who>.*)$", re.M)

# Quote lines, both formats seen in the wild:
#   :'''Archer''': text            (Wikiquote)
#   : '''Krieger: '''"text"        (Fandom — colon inside the bold)
#   :'''Archer''' ''(beat)'': text (stage direction between)
QUOTE_RE = re.compile(
    r"^[:*]+\s*'''\s*(?P<who>[^':]{1,40}?)\s*:?\s*'''\s*"
    r"(?P<mid>(?:''\([^)]*\)''|\([^)]*\))?)\s*:?\s*(?P<line>.+)$", re.M)

# |season = 10 / |season=Season 3 — tolerant, since episode infoboxes vary.
SEASON_RE = re.compile(r"\|\s*season\s*=\s*\D{0,12}?(\d{1,2})", re.I)
EPISODE_RE = re.compile(r"\|\s*episode\s*=\s*\D{0,60}?(\d{1,3})", re.I)


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

def api_get(api, **params):
    params["format"] = "json"
    url = api + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def cmd_scrape(args):
    """Walk a wiki category and cache every episode page's raw wikitext.

    Batched through the `generator=categorymembers` + `prop=revisions` combo so
    all ~150 pages arrive in a handful of requests. Deliberately NOT WebFetch or
    a page-at-a-time HTML scrape: Fandom answers 402 to generic fetchers, while
    api.php is unauthenticated and well-mannered. Cached to disk so `mine` and
    `probe` iterate offline — re-run with --refresh when the wiki changes.
    """
    out = DATA_DIR / f"{args.name}.json"
    if out.exists() and not args.refresh:
        print(f"[scrape] {out} exists; pass --refresh to re-fetch.")
        return

    pages, cont, reqs = {}, None, 0
    while True:
        p = dict(action="query", generator="categorymembers",
                 gcmtitle=args.category, gcmlimit="50", gcmnamespace="0",
                 prop="revisions", rvprop="content", rvslots="main")
        if cont:
            p["gcmcontinue"] = cont
        d = api_get(args.api, **p)
        reqs += 1
        for pg in d.get("query", {}).get("pages", {}).values():
            try:
                pages[pg["title"]] = pg["revisions"][0]["slots"]["main"]["*"]
            except (KeyError, IndexError):
                continue
        print(f"\r[scrape] {len(pages)} pages ({reqs} requests)", end="", flush=True)
        cont = d.get("continue", {}).get("gcmcontinue")
        if not cont:
            break
        time.sleep(args.delay)
    print()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "_source": args.api,
        "_category": args.category,
        "_license": "CC BY-SA — see the source wiki for full attribution.",
        "_note": ("Cached wikitext, used only to derive short catchphrase search "
                  "keys for matching against our own transcripts."),
        "_fetched": time.strftime("%Y-%m-%d"),
        "pages": pages,
    }, indent=1))

    seasons = collections.Counter()
    gags = quotes = 0
    for w in pages.values():
        m = SEASON_RE.search(w)
        seasons[int(m.group(1)) if m else 0] += 1
        if section(w, GAGS_SECTION):
            gags += 1
        if section(w, QUOTES_SECTION):
            quotes += 1
    print(f"[scrape] {len(pages)} pages -> {out}")
    print(f"[scrape] {gags} with running gags, {quotes} with quotes")
    print(f"[scrape] seasons: {dict(sorted(seasons.items()))}"
          + ("   (0 = no season in infobox)" if seasons.get(0) else ""))


# ---------------------------------------------------------------------------
# mine
# ---------------------------------------------------------------------------

def mine_gags(pages, characters):
    """{normalized phrase: {character: episodes citing it}} from the running-gag
    sections. A gag counts for a character when the attribution names them.
    Pure; unit-tested."""
    out = collections.defaultdict(collections.Counter)
    for title, w in pages.items():
        body = section(w, GAGS_SECTION)
        if not body:
            continue
        seen = set()
        for m in GAG_RE.finditer(body):
            phrase = normalize_phrase(m.group("phrase"))
            who = strip_markup(m.group("who"))
            if not phrase or len(phrase) > 40:
                continue
            for c in characters:
                # One credit per episode per (phrase, character), so a gag
                # listed twice on one page doesn't inflate its canon score.
                if re.search(rf"\b{re.escape(c)}\b", who, re.I) and (phrase, c) not in seen:
                    out[phrase][c] += 1
                    seen.add((phrase, c))
    return out


def mine_quotes(pages, characters):
    """{character: [line, ...]} from the Quotes sections — verbatim dialogue,
    a secondary source of phrase candidates. Pure; unit-tested."""
    out = collections.defaultdict(list)
    for w in pages.values():
        for m in QUOTE_RE.finditer(section(w, QUOTES_SECTION)):
            who = strip_markup(m.group("who")).strip()
            line = strip_markup(m.group("line")).strip(' "')
            if not line:
                continue
            for c in characters:
                if who.lower() == c.lower():
                    out[c].append(line)
    return out


def load_pages(name):
    path = DATA_DIR / f"{name}.json"
    if not path.is_file():
        sys.exit(f"No scrape at {path}. Run `phrases.py scrape` first.")
    return json.loads(path.read_text())["pages"]


def cmd_mine(args):
    proj = args.proj
    pages = load_pages(args.name)
    gags = mine_gags(pages, proj.CHARACTERS)
    quotes = mine_quotes(pages, proj.CHARACTERS)

    print(f"[mine] {len(pages)} pages -> {len(gags)} attributed gag phrases")
    print(f"[mine] quotes per character: "
          + ", ".join(f"{c} {len(v)}" for c, v in sorted(quotes.items())))

    rows = []
    for phrase, who in gags.items():
        char, eps = who.most_common(1)[0]
        rows.append((eps, char, phrase))
    rows.sort(reverse=True)

    chars = [args.character] if args.character else None
    print(f"\n{'eps':>4}  {'character':<10} phrase")
    shown = 0
    for eps, char, phrase in rows:
        if chars and char not in chars:
            continue
        print(f"{eps:>4}  {char:<10} {phrase}")
        shown += 1
        if shown >= args.limit:
            break


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def load_stoplist():
    path = DATA_DIR / "phrase_stoplist.txt"
    if path.is_file():
        words = [l.strip() for l in path.read_text().splitlines()]
    else:
        words = DEFAULT_STOPLIST
    return {normalize_phrase(w) for w in words if w.strip() and not w.startswith("#")}


def character_shares(db):
    """{character: fraction of all attributed utterances}. The base rate a
    phrase's hits have to be judged against."""
    tot = {r[0]: r[1] for r in db.execute(
        "SELECT character, COUNT(*) FROM utterances "
        "WHERE character IS NOT NULL AND character <> '' GROUP BY character")}
    return tot, sum(tot.values())


def attribute(hits, totals):
    """Who actually says a phrase, by rate rather than raw count.

    Raw argmax is useless on an ensemble with a lead: Archer has 42% of all
    attributed lines, so he wins the count for almost any common word — raw
    counts credit him with "get some", "idiot", "burn" and "chet", all of which
    belong to someone else. Dividing by each character's share of the corpus
    recovers the right answer in every one of those cases. Pure; unit-tested."""
    if not hits:
        return None
    return max(hits, key=lambda c: hits[c] / max(totals.get(c, 1), 1))


def probe_phrase(db, phrase, window):
    """Corpus evidence for one phrase, per character.

    Returns (hits_by_character, wide_by_character, unattributed) where `hits` is
    inside PHRASE_WINDOW and `wide` is the extra reachable by widening it.

    The wiki's attribution is only a hint and is often wrong — the Archer wiki
    credits "sploosh" to Lana because she is who Pam says it at. The corpus
    knows who actually says the line, so let it decide and keep the wiki
    attribution for comparison.
    """
    like = f"%{phrase}%"
    lo, hi = window
    hits, wide = collections.Counter(), collections.Counter()
    for r in db.execute(
            "SELECT character, duration_s FROM utterances "
            "WHERE character IS NOT NULL AND character <> '' AND lower(text) LIKE ?",
            (like,)):
        (hits if lo <= (r[1] or 0) <= hi else wide)[r[0]] += 1
    unattr = db.execute(
        "SELECT COUNT(*) FROM utterances WHERE (character IS NULL OR character='') "
        "AND lower(text) LIKE ?", (like,)).fetchone()[0]
    return hits, wide, unattr


def cmd_probe(args):
    proj = args.proj
    db = sqlite3.connect(Path(proj.workdir) / "corpus.db")
    pages = load_pages(args.name)
    gags = mine_gags(pages, proj.CHARACTERS)
    stop = load_stoplist()
    totals, _ = character_shares(db)
    window = tuple(proj.TUNING.get("PHRASE_WINDOW") or (0.4, 8.0))

    rows, dropped = [], collections.Counter()
    for phrase, who in gags.items():
        wiki_char, eps = who.most_common(1)[0]
        if phrase in stop:
            dropped["stoplist"] += 1
            continue
        if len(phrase) < args.min_len:
            dropped["too short"] += 1
            continue
        if eps < args.min_episodes:
            dropped["under-cited"] += 1
            continue
        hits, wide, unattr = probe_phrase(db, phrase, window)
        if not hits:
            dropped["no corpus hits"] += 1
            continue
        # The corpus decides the speaker, by rate not raw count; the wiki only
        # nominated one. When building a specific pack we care whether THAT
        # character says it enough, even if someone else says it more.
        best = attribute(hits, totals)
        char = args.character or best
        n = hits.get(char, 0)
        if n < args.min_hits:
            dropped["too few hits"] += 1
            continue
        rows.append((eps, n, wide.get(char, 0), unattr, char,
                     "" if char == wiki_char else wiki_char,
                     "" if char == best else best, phrase))

    rows.sort(key=lambda r: (-r[1], -r[0]))
    print(f"[probe] window {window[0]}-{window[1]}s   stoplist {len(stop)} entries")
    print(f"[probe] gates: >={args.min_episodes} episode(s), >={args.min_hits} corpus hit(s)")
    print(f"[probe] {len(rows)} phrases survived; dropped: "
          + ", ".join(f"{k} {v}" for k, v in dropped.most_common()))
    print(f"\n{'hits':>5}{'eps':>5}{'wide':>6}{'unatt':>6}  {'character':<10} phrase")
    for eps, n, wide_n, unattr, char, wiki_char, best, phrase in rows:
        note = ""
        if best:
            note = f"   (said more by {best})"
        elif wiki_char:
            note = f"   (wiki said {wiki_char})"
        print(f"{n:>5}{eps:>5}{wide_n:>6}{unattr:>6}  {char:<10} {phrase}{note}")
    print("\n  hits  = matching utterances for that character inside PHRASE_WINDOW")
    print("  eps   = episodes citing the gag (fan-canon signal)")
    print("  wide  = further hits outside the window (widen it to reach them)")
    print("  unatt = hits on utterances Stage 2 left unattributed (unreachable)")

    if args.emit:
        by_char = collections.defaultdict(list)
        for eps, n, wide_n, unattr, char, wiki_char, best, phrase in rows:
            by_char[char].append(phrase)
        print("\n# --- paste into a project's TUNING, then assign phrases to pools ---")
        for char, phrases in sorted(by_char.items()):
            print(f'\n# {char} — {len(phrases)} surviving phrases')
            print('"POOL_FILTERS": {')
            print(f'    "*": {{"chars": ["{char}"]}},')
            print('    # distribute these across the pools they suit:')
            for p in phrases:
                print(f'    #   "{p}"')
            print('},')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="archer_wot")
    ap.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    ap.add_argument("--name", default=None,
                    help="Scrape cache basename (default: <project>_wiki).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="Fetch and cache episode wikitext.")
    s.add_argument("--api", default="https://archer.fandom.com/api.php")
    s.add_argument("--category", default="Category:Episodes")
    s.add_argument("--delay", type=float, default=0.2)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(fn=cmd_scrape)

    m = sub.add_parser("mine", help="Rank gag phrases by episodes citing them.")
    m.add_argument("--character", default=None)
    m.add_argument("--limit", type=int, default=60)
    m.set_defaults(fn=cmd_mine)

    p = sub.add_parser("probe", help="Score candidates against the corpus.")
    p.add_argument("--character", default=None)
    # A single citation is NOT disqualifying: "sploosh", "get some" and
    # "shitsnacks" are each listed by one episode yet are unmistakably canon.
    # The stoplist plus a real corpus-hit floor does the discriminating; the
    # citation count is better used for ranking than for gating.
    p.add_argument("--min-episodes", type=int, default=1,
                   help="Drop gags cited by fewer episodes (default: 1).")
    p.add_argument("--min-hits", type=int, default=2,
                   help="Drop phrases with fewer corpus hits (default: 2).")
    p.add_argument("--min-len", type=int, default=4)
    p.add_argument("--emit", action="store_true",
                   help="Print a paste-ready POOL_FILTERS skeleton.")
    p.set_defaults(fn=cmd_probe)

    args = ap.parse_args()
    args.proj = load_project(args.project, args.workdir)
    args.name = args.name or f"{args.project}_wiki"
    args.fn(args)


if __name__ == "__main__":
    main()
