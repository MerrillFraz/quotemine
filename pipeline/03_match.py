#!/usr/bin/env python3
"""
03_match.py — Match attributed utterances to (pooled) game events.

Pools come from the active project's config (projects/<name>/config.py), grouped
by emotional beat. Each pool -> one downstream container (e.g. a Wwise Random
Container), wired to fire on all of that pool's game events.

Two passes per pool:
  keyword  — FTS5 over the transcript. Fast, catches obvious hits.
  semantic — embed the pool DESCRIPTION, rank every line by meaning.

Text embeddings are cached in the DB; re-tuning a description and re-ranking
costs nothing.

    python 03_match.py events            # (re)load pool table, show it
    python 03_match.py match             # GPU once to embed texts, then rank
    python 03_match.py list              # candidate counts per pool
    python 03_match.py show <pool_id>    # top candidates for one pool
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

from project import load_project

# Model default (environment). POOLS and the candidate/scoring knobs come from
# the project (pipeline/project.py).
TEXT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS pools (
            pool_id TEXT PRIMARY KEY, display TEXT, suggested_char TEXT,
            keywords TEXT, description TEXT);
        CREATE TABLE IF NOT EXISTS pool_events (
            pool_id TEXT, game_event TEXT,
            PRIMARY KEY (pool_id, game_event));
        CREATE TABLE IF NOT EXISTS text_emb (
            utterance_id INTEGER PRIMARY KEY, vector BLOB);
        CREATE TABLE IF NOT EXISTS pool_candidates (
            pool_id TEXT, utterance_id INTEGER, kw INTEGER,
            sem REAL, score REAL,
            PRIMARY KEY (pool_id, utterance_id));
    """)
    return db


def load_pools(db, pools):
    db.execute("DELETE FROM pools")
    db.execute("DELETE FROM pool_events")
    for p in pools:
        # A pool is (pool_id, display, char, keywords, description, [events]) and
        # MAY carry extra trailing fields (e.g. a package-time routing filter) the
        # engine passes through untouched. Matching only needs the first six.
        pid, disp, char, kw, desc, events = p[:6]
        db.execute("INSERT INTO pools VALUES (?,?,?,?,?)", (pid, disp, char, kw, desc))
        db.executemany("INSERT INTO pool_events VALUES (?,?)",
                       [(pid, ev) for ev in events])
    db.commit()


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def cmd_events(args, db):
    pools = args.proj.POOLS
    load_pools(db, pools)
    print(f"{len(pools)} pools loaded, "
          f"{sum(len(p[5]) for p in pools)} game events mapped.\n")
    print(f"{'pool_id':<16}{'char':<9}{'evts':>5}  display")
    for p in db.execute("SELECT * FROM pools"):
        n = db.execute("SELECT COUNT(*) c FROM pool_events WHERE pool_id=?",
                       (p["pool_id"],)).fetchone()["c"]
        print(f"{p['pool_id']:<16}{(p['suggested_char'] or '-'):<9}{n:>5}  {p['display']}")
    print(f"\nEdit projects/{args.project}/config.py to tune pools/descriptions. "
          "Then re-run events, then match.")


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
            enc = tok(batch, padding=True, truncation=True, max_length=64,
                      return_tensors="pt").to(dev)
            h = mdl(**enc).last_hidden_state
            v = _mean_pool(h, enc["attention_mask"])
            v = torch.nn.functional.normalize(v, dim=1)
            out.append(v.cpu().numpy().astype(np.float32))
        return np.concatenate(out) if out else np.zeros((0, 384), np.float32)
    return embed


def ensure_text_embeddings(db, embed, cand_min_s, cand_max_s):
    cand = db.execute(
        "SELECT id, text FROM utterances "
        "WHERE character IS NOT NULL AND duration_s BETWEEN ? AND ? "
        "AND text IS NOT NULL AND length(text) > 0",
        (cand_min_s, cand_max_s)).fetchall()
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

def embedding_window(cand_min_s, cand_max_s, pool_windows):
    """The union of the global candidate window and every per-pool override —
    the duration range to embed once so a pool with a wider window has its
    longer lines available. Pure; unit-tested."""
    return (min([cand_min_s] + [w[0] for w in pool_windows.values()]),
            max([cand_max_s] + [w[1] for w in pool_windows.values()]))


def window_positions(ids, durs, pmin, pmax):
    """Indices into `ids` whose utterance duration falls in [pmin, pmax], used
    to restrict the ranked set to one pool's window. Pure; unit-tested."""
    return [k for k, uid in enumerate(ids) if pmin <= durs[uid] <= pmax]


def combined_score(sem, is_kw, bonus):
    """A candidate's board-ranking score: semantic similarity plus the (possibly
    per-pool) keyword bonus when it's a keyword hit. A large per-pool bonus floats
    terse keyword gold above higher-semantic non-keyword lines. Pure; unit-tested."""
    return sem + (bonus if is_kw else 0.0)


def cmd_match(args, db):
    t = args.proj.TUNING
    cand_min_s, cand_max_s = t["CAND_MIN_S"], t["CAND_MAX_S"]
    top_semantic, kw_bonus = t["TOP_SEMANTIC"], t["KW_BONUS"]
    # Per-pool duration windows override the global one for pools whose lines
    # aren't terse callouts (e.g. a 3-6s battle-start rally). Pools not listed
    # use the global window. See docs/tuning.md.
    pool_windows = t.get("POOL_CAND_WINDOWS", {}) or {}
    # Per-pool keyword bonus: float literal hits for pools where the words are
    # the signal (terse trash-talk). Pools not listed use the global KW_BONUS.
    pool_kw_bonus = t.get("POOL_KW_BONUS", {}) or {}
    if not db.execute("SELECT 1 FROM pools LIMIT 1").fetchone():
        load_pools(db, args.proj.POOLS)

    # Embed the UNION of every window in use, so a pool with a wider window has
    # its longer lines available; each pool then filters M to its own window.
    emb_min, emb_max = embedding_window(cand_min_s, cand_max_s, pool_windows)

    embed = make_text_embedder()
    cand_ids = ensure_text_embeddings(db, embed, emb_min, emb_max)
    ids, M = load_text_matrix(db, cand_ids)
    if not ids:
        sys.exit("No candidate embeddings. Did Stage 2 attribute characters?")
    id_pos = {uid: k for k, uid in enumerate(ids)}
    durs = {r["id"]: r["duration_s"]
            for r in db.execute("SELECT id, duration_s FROM utterances")}

    pools = db.execute("SELECT * FROM pools").fetchall()
    pool_vecs = embed([p["description"] for p in pools])

    db.execute("DELETE FROM pool_candidates")
    for p, pv in zip(pools, pool_vecs):
        pmin, pmax = pool_windows.get(p["pool_id"], (cand_min_s, cand_max_s))
        # Restrict the ranked set to this pool's duration window.
        win_pos = window_positions(ids, durs, pmin, pmax)
        if win_pos:
            sims = M[win_pos] @ pv
            order = np.argsort(-sims)[:top_semantic]
            cand = {ids[win_pos[k]]: float(sims[k]) for k in order}
        else:
            cand = {}

        kw_ids = set()
        # A pool may legitimately carry no keywords — e.g. a mechanical event
        # with no literal dialogue, matched on the semantic "vibe" alone. An
        # empty FTS5 MATCH is a syntax error, so skip the keyword pass entirely.
        terms = " OR ".join(f'"{w}"' for w in p["keywords"].split())
        if terms:
            for r in db.execute(
                "SELECT u.id FROM utterances u "
                "WHERE u.character IS NOT NULL AND u.duration_s BETWEEN ? AND ? "
                "AND u.id IN (SELECT rowid FROM utterances_fts WHERE utterances_fts MATCH ?)",
                (pmin, pmax, terms)):
                kw_ids.add(r["id"])

        bonus = pool_kw_bonus.get(p["pool_id"], kw_bonus)
        rows = []
        for uid in (set(cand) | kw_ids):
            sem = cand.get(uid)
            if sem is None and uid in id_pos:
                sem = float(M[id_pos[uid]] @ pv)
            sem = sem if sem is not None else 0.0
            kw = 1 if uid in kw_ids else 0
            rows.append((p["pool_id"], uid, kw, sem, combined_score(sem, kw, bonus)))
        db.executemany("INSERT OR REPLACE INTO pool_candidates VALUES (?,?,?,?,?)", rows)
    db.commit()
    print("\n[match] done.")
    _print_counts(db)


def _print_counts(db):
    print(f"\n{'pool_id':<16}{'cands':>6}{'kw':>5}{'sem':>5}")
    for p in db.execute("SELECT * FROM pools"):
        c = db.execute("SELECT COUNT(*) n, SUM(kw) k FROM pool_candidates WHERE pool_id=?",
                       (p["pool_id"],)).fetchone()
        n, k = c["n"] or 0, c["k"] or 0
        print(f"{p['pool_id']:<16}{n:>6}{k:>5}{n-k:>5}")


def cmd_list(args, db):
    _print_counts(db)


def cmd_show(args, db):
    p = db.execute("SELECT * FROM pools WHERE pool_id=?", (args.pool_id,)).fetchone()
    if not p:
        sys.exit(f"unknown pool: {args.pool_id}")
    evs = [r["game_event"] for r in db.execute(
        "SELECT game_event FROM pool_events WHERE pool_id=?", (args.pool_id,))]
    print(f"\n{p['display']}  (suggested: {p['suggested_char'] or '-'})")
    print(f"desc: {p['description']}")
    print(f"wires to {len(evs)} game events: {', '.join(evs)}\n")
    print(f"{'char':<9}{'dur':>5}{'kw':>4}{'sem':>6}  text")
    for r in db.execute("""
        SELECT pc.kw, pc.sem, u.character, u.duration_s, u.text
        FROM pool_candidates pc JOIN utterances u ON u.id=pc.utterance_id
        WHERE pc.pool_id=? ORDER BY pc.score DESC LIMIT ?""",
        (args.pool_id, args.n)):
        star = "*" if r["kw"] else " "
        print(f"{r['character']:<9}{r['duration_s']:>5.1f}{star:>4}{r['sem']:>6.2f}  {r['text'][:70]}")


def main():
    p = argparse.ArgumentParser(description="Match lines to event pools (Stage 3).")
    p.add_argument("--project", default="archer_wot",
                   help="Project name under projects/ (see projects/_template/).")
    p.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("events")
    sub.add_parser("match")
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("pool_id"); s.add_argument("-n", type=int, default=25)
    args = p.parse_args()

    args.proj = load_project(args.project, args.workdir)
    args.proj.workdir.mkdir(parents=True, exist_ok=True)
    db = connect(args.proj.workdir)
    {"events": cmd_events, "match": cmd_match, "list": cmd_list, "show": cmd_show}[args.cmd](args, db)
    db.close()


if __name__ == "__main__":
    main()
