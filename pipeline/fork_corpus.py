#!/usr/bin/env python3
"""
fork_corpus.py — start a new project from an already-mined corpus.

Stages 1-2 are the expensive half of the pipeline (demux, transcribe, diarize,
embed, human tagging) and they produce something entirely project-agnostic: a
speaker-attributed line database. Stages 3-6 — which pools exist, what got
picked, how it's packaged — are the project-specific half, and they're cheap.

A second project over the SAME corpus therefore needs no GPU at all. This copies
the corpus tables and clears the project ones:

    python pipeline/fork_corpus.py --from archer_wot --to archer_wot_sterling

KEPT (corpus, project-agnostic)
    episodes, utterances, utterances_fts, jobs   Stage 1
    utt_pool, refs_utt, centroids, clusters      Stage 2 (incl. human tagging)
    text_emb                                     Stage 3 embedding cache, keyed
                                                 by utterance_id alone, so it is
                                                 valid for any pool set

CLEARED (project layer)
    pools, pool_events, pool_candidates          Stage 3
    picks                                        Stage 4
    finals                                       Stage 5

Only corpus.db is copied — roughly 80 MB against ~6 GB for a full workdir. The
audio is NOT duplicated: `episodes.wav_path` / `json_path` are absolute and keep
pointing at the source project's `wav/` and `json/`, which is exactly what we
want. The corollary is that a fork DEPENDS on its parent — deleting the parent's
work/ breaks every fork's Stage 4 previews. Stage 5 reads `episodes.path` (the
original video), so finals are unaffected either way.

Clearing `pools` is load-bearing, not tidiness: 03_match only loads POOLS when
the table is empty, so a fork would otherwise silently keep its parent's pools
forever.
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

from project import REPO_ROOT, load_project

# Wiped in the fork so the new project starts from its own POOLS. Order matters
# only for readability — there are no cross-table FKs here.
PROJECT_TABLES = ["finals", "picks", "pool_candidates", "pool_events", "pools"]

# Sanity-checked after the copy: a fork with no utterances means the source was
# never mined, and every later stage would fail with a less obvious message.
CORPUS_TABLES = ["episodes", "utterances", "clusters", "centroids", "text_emb"]


def _count(db, table):
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return None          # table absent in an older DB; not an error


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True,
                    help="Source project whose corpus.db is already mined.")
    ap.add_argument("--to", dest="dst", required=True,
                    help="New project name; work/<name>/ must not exist yet.")
    ap.add_argument("--src-workdir", default=None,
                    help="Override the source workdir (default: work/<from>/).")
    ap.add_argument("--dst-workdir", default=None,
                    help="Override the destination workdir (default: work/<to>/).")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing destination corpus.db.")
    args = ap.parse_args()

    src_wd = Path(args.src_workdir) if args.src_workdir else REPO_ROOT / "work" / args.src
    dst_wd = Path(args.dst_workdir) if args.dst_workdir else REPO_ROOT / "work" / args.dst
    src_db, dst_db = src_wd / "corpus.db", dst_wd / "corpus.db"

    if not src_db.is_file():
        sys.exit(f"No corpus at {src_db}. Run Stages 1-2 for '{args.src}' first.")
    if dst_db.exists() and not args.force:
        sys.exit(f"{dst_db} already exists. Delete it or pass --force.")

    # The destination config need not exist yet (you may be forking before
    # writing it), but warn if it doesn't — every later stage will need it.
    try:
        load_project(args.dst, workdir=dst_wd)
    except SystemExit:
        print(f"[fork] note: projects/{args.dst}/config.py doesn't exist yet — "
              f"create it before running Stage 3.")

    dst_wd.mkdir(parents=True, exist_ok=True)

    # Copy through sqlite's backup API rather than cp: it takes a consistent
    # snapshot even if something else holds the source open, and it leaves the
    # WAL behind instead of copying a -wal/-shm pair that won't match.
    print(f"[fork] {src_db}\n    -> {dst_db}")
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_db)
    with dst:
        src.backup(dst)
    src.close()

    kept = {t: _count(dst, t) for t in CORPUS_TABLES}
    if not kept.get("utterances"):
        dst.close()
        dst_db.unlink(missing_ok=True)
        sys.exit(f"Source corpus has no utterances — nothing to fork. "
                 f"Has Stage 1 run for '{args.src}'?")
    if not kept.get("clusters"):
        print("[fork] WARNING: no clusters — Stage 2 may not have run, so "
              "utterances.character is probably empty and Stage 3 will find "
              "nothing to match.")

    cleared = {}
    for t in PROJECT_TABLES:
        n = _count(dst, t)
        if n is None:
            continue
        dst.execute(f"DELETE FROM {t}")
        cleared[t] = n
    dst.commit()
    dst.execute("VACUUM")
    dst.close()

    print("\n[fork] kept (corpus):")
    for t, n in kept.items():
        if n is not None:
            print(f"         {t:<16}{n:>9,}")
    print("[fork] cleared (project layer):")
    for t, n in cleared.items():
        print(f"         {t:<16}{n:>9,}")

    size_mb = dst_db.stat().st_size / 1e6
    src_wd_mb = sum(f.stat().st_size for f in src_wd.rglob("*") if f.is_file()) / 1e6
    print(f"\n[fork] {dst_db.name} is {size_mb:,.0f} MB; the source workdir is "
          f"{src_wd_mb:,.0f} MB.")
    print(f"[fork] audio is shared, not copied — this fork reads {args.src}'s "
          f"wav/ via absolute paths in `episodes`.")
    print(f"\nNext: write projects/{args.dst}/config.py, then\n"
          f"  python pipeline/03_match.py --project {args.dst} events\n"
          f"  python pipeline/03_match.py --project {args.dst} match")


if __name__ == "__main__":
    main()
