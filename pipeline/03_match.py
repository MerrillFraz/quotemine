#!/usr/bin/env python3
"""
stage6.py — Match attributed utterances to World of Tanks crew events.

Two passes per event:
  keyword  — FTS5 over the transcript. Fast, catches the obvious hits.
  semantic — embed a DESCRIPTION of the event, rank every line by meaning.
             This is what surfaces the perfect line that shares no keywords.

Text embeddings are cached in the DB, so re-tuning an event description and
re-ranking costs nothing — no GPU re-run.

    python stage6.py events              # show / (re)load the event table
    python stage6.py match               # GPU once to embed texts, then rank
    python stage6.py list                # candidate counts per event
    python stage6.py show <event_id>     # top candidates for one event

IMPORTANT: the EVENTS below are my mapping to typical WoT crew callouts. The
authoritative event list is the Notes column on the Wwise project's Events tab
(the official WG sound-mod project). Reconcile these against that before you
build banks — names here are working labels, not verified game event strings.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

WORKDIR_DEFAULT = str(Path.home() / "archer-vp" / "work")
TEXT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Candidate window: usable callout lengths, attributed to a character.
CAND_MIN_S = 0.4
CAND_MAX_S = 2.2

TOP_SEMANTIC = 60      # keep this many semantic hits per event
KW_BONUS = 0.15        # keyword hits get their semantic score nudged up

# ---------------------------------------------------------------------------
# Event definitions.  (event_id, display, keywords, description, suggested_char)
# suggested_char is a HINT shown in the board, never a filter.
# ---------------------------------------------------------------------------

EVENTS = [
    ("battle_start",   "Battle start",        "start begin ready move go people",
     "rallying the crew as the battle begins, let's move out", "Archer"),
    ("enemy_spotted",  "Enemy spotted",       "there them enemy contact spotted see got",
     "alerting the crew that an enemy vehicle has just been spotted", "Archer"),
    ("enemy_destroyed","Enemy destroyed",     "got dead killed destroyed down boom",
     "triumphant callout that an enemy has just been destroyed", "Archer"),
    ("ally_destroyed", "Ally destroyed",      "down lost gone dead man",
     "grim resigned acknowledgment that a friendly tank was destroyed", "Malory"),
    ("reload_done",    "Reload complete",     "ready loaded set go good",
     "the gun is reloaded and ready to fire again", None),
    ("reloading",      "Still reloading",     "wait hold moment reloading second",
     "asking for a moment, the gun is still reloading", None),
    ("ammo_rack",      "Ammo rack hit",       "ammo explode blow boom rack",
     "panic, the ammunition is hit and about to explode", "Lana"),
    ("on_fire",        "On fire",             "fire burning flames hot smoke",
     "alarm, the tank is on fire, we are burning", "Cheryl"),
    ("tracks_damaged", "Tracks damaged",      "stuck track move immobile can't",
     "frustration, the tracks are blown and we cannot move", None),
    ("gun_damaged",    "Gun damaged",         "gun broken barrel shoot can't",
     "the main gun is damaged and cannot fire", "Cyril"),
    ("engine_damaged", "Engine damaged",      "engine dead stalled power",
     "the engine is damaged and losing power", "Krieger"),
    ("crew_injured",   "Crew injured",        "hurt hit injured down ow",
     "a crew member has been injured", None),
    ("low_hp",         "Critical health",     "dying bad hurt almost done",
     "badly damaged and desperate, we are barely holding on", "Archer"),
    ("ricochet",       "Ricochet",            "bounced off armor nothing",
     "the shot bounced harmlessly off the armor", "Archer"),
    ("penetrated",     "Penetration",         "through got them hit right",
     "we punched a shot clean through the enemy armor", None),
    ("we_got_hit",     "Took a hit",          "hit us damn ow hell",
     "we just took a hit from the enemy", None),
    ("base_capturing", "Capturing base",      "cap base point taking capture",
     "we are capturing the base", None),
    ("base_lost",      "Base under attack",   "base capping enemy point them",
     "urgent, the enemy is capturing our base", "Lana"),
    ("time_low",       "Time running out",    "time hurry running out fast",
     "the clock is running down, we need to hurry", "Malory"),
    ("victory",        "Victory",             "win won yes did great",
     "elated, we won the battle", "Cheryl"),
    ("defeat",         "Defeat",              "lost lose over damn done",
     "dejected, we lost the battle", "Malory"),
    ("affirmative",    "Affirmative",         "yes okay got roger copy right",
     "acknowledging an order, affirmative, on it", None),
    ("negative",       "Negative",            "no can't won't nope never",
     "refusing or negating, that is not going to happen", "Archer"),
    ("need_help",      "Requesting support",  "help need support cover backup",
     "calling for help or backup from allies", "Cyril"),
    ("taunt",          "Taunt enemy",         "idiot stupid loser pathetic amateur",
     "mocking and insulting an enemy", "Archer"),
    ("suppressing",    "Suppressing fire",    "fire keep shooting suppress cover",
     "laying down suppressing fire, keep shooting", None),
    ("retreat",        "Retreat",             "back run pull retreat go",
     "we need to fall back and retreat", "Cyril"),
    ("push",           "Push forward",        "go forward attack now push move",
     "push forward and press the attack", "Archer"),
    ("confusion",      "Confusion",           "what where huh who wait",
     "confused, unsure what is happening", "Cheryl"),
    ("annoyed",        "Annoyed",             "god damn seriously again ugh",
     "exasperated and annoyed at the situation", "Malory"),
    ("drinking",       "Need a drink",        "drink drunk bar scotch",
     "wishing for or referencing a drink", "Archer"),
    ("insult_ally",    "Ribbing an ally",     "idiot dummy shut moron",
     "casually insulting a teammate", "Archer"),
]


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY, display TEXT, keywords TEXT,
            description TEXT, suggested_char TEXT);
        CREATE TABLE IF NOT EXISTS text_emb (
            utterance_id INTEGER PRIMARY KEY, vector BLOB);
        CREATE TABLE IF NOT EXISTS event_candidates (
            event_id TEXT, utterance_id INTEGER, kw INTEGER,
            sem REAL, score REAL,
            PRIMARY KEY (event_id, utterance_id));
    """)
    return db


def load_events(db):
    db.execute("DELETE FROM events")
    db.executemany("INSERT INTO events VALUES (?,?,?,?,?)", EVENTS)
    db.commit()


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def cmd_events(args, db):
    load_events(db)
    print(f"{len(EVENTS)} events loaded.\n")
    print(f"{'event_id':<16}{'suggested':<10}display")
    for e in db.execute("SELECT * FROM events"):
        print(f"{e['event_id']:<16}{(e['suggested_char'] or '-'):<10}{e['display']}")
    print("\nEdit the EVENTS list in stage6.py to tune. Then re-run `events`, then `match`.")


# ---------------------------------------------------------------------------
# text embedding (GPU, cached)
# ---------------------------------------------------------------------------

def _mean_pool(last_hidden, mask):
    m = mask.unsqueeze(-1).float()
    return (last_hidden * m).sum(1) / m.sum(1).clamp(min=1e-9)


def make_text_embedder():
    import torch
    from transformers import AutoTokenizer, AutoModel
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(TEXT_MODEL)
    mdl = AutoModel.from_pretrained(TEXT_MODEL).to(dev).eval()
    print(f"[text] {TEXT_MODEL} on {dev}")

    @torch.no_grad()
    def embed(texts):
        out = []
        for i in range(0, len(texts), 256):
            batch = texts[i:i + 256]
            enc = tok(batch, padding=True, truncation=True, max_length=64, return_tensors="pt").to(dev)
            h = mdl(**enc).last_hidden_state
            v = _mean_pool(h, enc["attention_mask"])
            v = torch.nn.functional.normalize(v, dim=1)
            out.append(v.cpu().numpy().astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, 384), np.float32)
    return embed


def ensure_text_embeddings(db, embed):
    """Embed any candidate utterance not already cached."""
    cand = db.execute(
        "SELECT id, text FROM utterances "
        "WHERE character IS NOT NULL AND duration_s BETWEEN ? AND ? "
        "AND text IS NOT NULL AND length(text) > 0",
        (CAND_MIN_S, CAND_MAX_S)).fetchall()
    have = {r["utterance_id"] for r in db.execute("SELECT utterance_id FROM text_emb")}
    todo = [(r["id"], r["text"]) for r in cand if r["id"] not in have]
    print(f"[text] {len(cand)} candidates, {len(todo)} need embedding")
    if todo:
        vecs = embed([t for _, t in todo])
        db.executemany("INSERT OR REPLACE INTO text_emb VALUES (?,?)",
                       [(uid, v.tobytes()) for (uid, _), v in zip(todo, vecs)])
        db.commit()
    return [r["id"] for r in cand]


def load_text_matrix(db, ids):
    rows = db.execute("SELECT utterance_id, vector FROM text_emb").fetchall()
    lut = {r["utterance_id"]: np.frombuffer(r["vector"], dtype=np.float32) for r in rows}
    keep = [i for i in ids if i in lut]
    M = np.stack([lut[i] for i in keep]) if keep else np.zeros((0, 384), np.float32)
    return keep, M


# ---------------------------------------------------------------------------
# match
# ---------------------------------------------------------------------------

def cmd_match(args, db):
    if not db.execute("SELECT 1 FROM events LIMIT 1").fetchone():
        load_events(db)

    embed = make_text_embedder()
    cand_ids = ensure_text_embeddings(db, embed)
    ids, M = load_text_matrix(db, cand_ids)
    if not ids:
        sys.exit("No candidate embeddings. Did Stage 5 attribute characters?")
    id_pos = {uid: k for k, uid in enumerate(ids)}

    events = db.execute("SELECT * FROM events").fetchall()
    ev_vecs = embed([e["description"] for e in events])

    db.execute("DELETE FROM event_candidates")
    for e, ev in zip(events, ev_vecs):
        sims = M @ ev                      # cosine; both normalized
        order = np.argsort(-sims)[:TOP_SEMANTIC]
        cand = {ids[k]: float(sims[k]) for k in order}

        # keyword pass: FTS match, character-agnostic, joined to candidate window
        kw_ids = set()
        terms = " OR ".join(f'"{w}"' for w in e["keywords"].split())
        for r in db.execute(
            "SELECT u.id FROM utterances u "
            "WHERE u.character IS NOT NULL AND u.duration_s BETWEEN ? AND ? "
            "AND u.id IN (SELECT rowid FROM utterances_fts WHERE utterances_fts MATCH ?)",
            (CAND_MIN_S, CAND_MAX_S, terms)):
            kw_ids.add(r["id"])

        rows = []
        allids = set(cand) | kw_ids
        for uid in allids:
            sem = cand.get(uid)
            if sem is None and uid in id_pos:
                sem = float(M[id_pos[uid]] @ ev)
            sem = sem if sem is not None else 0.0
            kw = 1 if uid in kw_ids else 0
            score = sem + (KW_BONUS if kw else 0.0)
            rows.append((e["event_id"], uid, kw, sem, score))
        db.executemany("INSERT OR REPLACE INTO event_candidates VALUES (?,?,?,?,?)", rows)
    db.commit()
    print("\n[match] done.")
    _print_counts(db)


def _print_counts(db):
    print(f"\n{'event_id':<16}{'cands':>6}{'kw':>5}{'sem':>5}")
    for e in db.execute("SELECT * FROM events"):
        c = db.execute("SELECT COUNT(*) n, SUM(kw) k FROM event_candidates WHERE event_id=?",
                       (e["event_id"],)).fetchone()
        n = c["n"] or 0
        k = c["k"] or 0
        print(f"{e['event_id']:<16}{n:>6}{k:>5}{n-k:>5}")


def cmd_list(args, db):
    _print_counts(db)


def cmd_show(args, db):
    e = db.execute("SELECT * FROM events WHERE event_id=?", (args.event_id,)).fetchone()
    if not e:
        sys.exit(f"unknown event: {args.event_id}")
    print(f"\n{e['display']}  (suggested: {e['suggested_char'] or '-'})")
    print(f"desc: {e['description']}\n")
    print(f"{'char':<9}{'dur':>5}{'kw':>4}{'sem':>6}  text")
    for r in db.execute("""
        SELECT ec.kw, ec.sem, u.character, u.duration_s, u.text
        FROM event_candidates ec JOIN utterances u ON u.id=ec.utterance_id
        WHERE ec.event_id=? ORDER BY ec.score DESC LIMIT ?""",
        (args.event_id, args.n)):
        star = "*" if r["kw"] else " "
        print(f"{r['character']:<9}{r['duration_s']:>5.1f}{star:>4}{r['sem']:>6.2f}  {r['text'][:70]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", default=WORKDIR_DEFAULT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("events")
    sub.add_parser("match")
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("event_id"); s.add_argument("-n", type=int, default=25)
    args = p.parse_args()

    db = connect(args.workdir)
    {"events": cmd_events, "match": cmd_match, "list": cmd_list, "show": cmd_show}[args.cmd](args, db)
    db.close()


if __name__ == "__main__":
    main()
