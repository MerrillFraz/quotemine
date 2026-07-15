#!/usr/bin/env python3
"""
04_audition.py — Stage 4: audition ranked candidates and record your picks.

    sample            Cut a padded preview per top candidate, emit audition.html
                      (a keep/reject board grouped by pool).            [CPU]
    --- YOU KEEP THE LINES YOU WANT IN THE BROWSER, EXPORT picks.json ---
    import <picks>    Load kept (pool, utterance) pairs into the picks table.

Previews are cut from the 16 kHz working WAV (fast, fine for review). The final
clips are cut later from the ORIGINAL source in Stage 5. Reads pool_candidates
from Stage 3, so run `03_match.py match` first.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from project import load_project
import downstream


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS picks (
        pool_id TEXT, utterance_id INTEGER, PRIMARY KEY (pool_id, utterance_id))""")
    return db


def cmd_sample(args, db):
    proj = args.proj
    t = proj.TUNING
    meta = {p[0]: {"display": p[1], "suggested_char": p[2]} for p in proj.POOLS}

    outdir = Path(proj.workdir) / "audition"
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    total = 0
    for p in proj.POOLS:
        pid = p[0]
        cands = db.execute(
            """SELECT pc.utterance_id, pc.kw, pc.sem, u.start_s, u.end_s,
                      u.duration_s, u.character, u.text, e.wav_path
               FROM pool_candidates pc
               JOIN utterances u ON u.id = pc.utterance_id
               JOIN episodes e   ON e.id = u.episode_id
               WHERE pc.pool_id = ? AND e.wav_path IS NOT NULL
               ORDER BY pc.score DESC LIMIT ?""",
            (pid, t["AUDITION_TOP_N"])).fetchall()
        for c in cands:
            name = f"{pid}_{c['utterance_id']}.wav"
            if not downstream.preview_clip(c["wav_path"], c["start_s"], c["end_s"],
                                           outdir / name, pad=t["PREVIEW_PAD_S"]):
                continue
            rows.append({
                "pool_id": pid, "display": meta[pid]["display"],
                "utterance_id": c["utterance_id"], "character": c["character"],
                "duration_s": c["duration_s"], "text": c["text"],
                "kw": c["kw"], "sem": c["sem"], "preview": f"audition/{name}",
            })
            total += 1
        print(f"[audition] {pid}: {len(cands)} candidates")

    (Path(proj.workdir) / "audition.html").write_text(downstream.build_audition_board(rows))
    print(f"\n[audition] {total} previews across {len(proj.POOLS)} pools")
    print(f"  cd {proj.workdir} && python -m http.server 8000")
    print("  open http://localhost:8000/audition.html")
    print("  ... keep the lines you want, export picks.json, then:")
    print(f"  python pipeline/04_audition.py --project {args.project} import picks.json")


def cmd_import(args, db):
    picks = json.loads(Path(args.picksfile).read_text())
    if not isinstance(picks, list):
        sys.exit("picks.json must be a list of {pool_id, utterance_id}.")
    db.execute("DELETE FROM picks")
    db.executemany("INSERT OR IGNORE INTO picks(pool_id, utterance_id) VALUES (?,?)",
                   [(p["pool_id"], p["utterance_id"]) for p in picks])
    db.commit()
    print(f"[audition] imported {len(picks)} picks.")
    for r in db.execute("SELECT pool_id, COUNT(*) n FROM picks GROUP BY pool_id ORDER BY pool_id"):
        print(f"  {r['pool_id']:<18}{r['n']:>4}")


def main():
    p = argparse.ArgumentParser(description="Audition candidates, record picks (Stage 4).")
    p.add_argument("--project", default="archer_wot")
    p.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    im = sub.add_parser("import"); im.add_argument("picksfile")
    args = p.parse_args()

    args.proj = load_project(args.project, args.workdir)
    args.proj.workdir.mkdir(parents=True, exist_ok=True)
    db = connect(args.proj.workdir)
    {"sample": cmd_sample, "import": cmd_import}[args.cmd](args, db)
    db.close()


if __name__ == "__main__":
    main()
