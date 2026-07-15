#!/usr/bin/env python3
"""
05_clean.py — Stage 5: cut and clean the final clips for your picks.  [CPU]

For every row in `picks`, re-cut the span from the ORIGINAL source video (full
quality — NOT the 16 kHz working WAV) and run it through loudnorm + optional
bandpass + trim + fades. Outputs land in work/<project>/final/<pool>/ and are
recorded in the `finals` table for Stage 6.

Tuning: CLEAN_PAD_S, LOUDNORM_LUFS, BANDPASS_HZ, FADE_MS (see project config).
Requires ffmpeg. Run Stage 4 `import` first so `picks` is populated.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from project import load_project
import downstream


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS finals (
        pool_id TEXT, utterance_id INTEGER, path TEXT,
        PRIMARY KEY (pool_id, utterance_id))""")
    return db


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", (s or "unknown")).strip("_") or "unknown"


def main():
    p = argparse.ArgumentParser(description="Cut + clean final clips (Stage 5).")
    p.add_argument("--project", default="archer_wot")
    p.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    args = p.parse_args()

    proj = load_project(args.project, args.workdir)
    proj.workdir.mkdir(parents=True, exist_ok=True)
    t = proj.TUNING
    db = connect(proj.workdir)

    picks = db.execute(
        """SELECT pk.pool_id, pk.utterance_id, u.start_s, u.end_s,
                  u.character, e.path AS src
           FROM picks pk
           JOIN utterances u ON u.id = pk.utterance_id
           JOIN episodes e   ON e.id = u.episode_id
           ORDER BY pk.pool_id""").fetchall()
    if not picks:
        sys.exit("No picks. Run Stage 4 `import <picks.json>` first.")

    final_root = proj.workdir / "final"
    db.execute("DELETE FROM finals")
    done = missing = 0
    for r in picks:
        if not r["src"] or not Path(r["src"]).exists():
            print(f"  [skip] source missing for utt {r['utterance_id']}: {r['src']}")
            missing += 1
            continue
        pool_dir = final_root / r["pool_id"]
        pool_dir.mkdir(parents=True, exist_ok=True)
        out = pool_dir / f"{r['utterance_id']}_{_slug(r['character'])}.wav"
        tmp = pool_dir / f".raw_{r['utterance_id']}.wav"
        try:
            downstream.cut_from_source(r["src"], r["start_s"], r["end_s"], tmp,
                                       pad=t["CLEAN_PAD_S"])
            downstream.clean_audio(tmp, out, lufs=t["LOUDNORM_LUFS"],
                                   bandpass_hz=t["BANDPASS_HZ"], fade_ms=t["FADE_MS"])
        finally:
            if tmp.exists():
                tmp.unlink()
        db.execute("INSERT OR REPLACE INTO finals VALUES (?,?,?)",
                   (r["pool_id"], r["utterance_id"], str(out.relative_to(proj.workdir))))
        done += 1
    db.commit()
    print(f"\n[clean] {done} clips cleaned into {final_root}"
          + (f"  ({missing} skipped: source missing)" if missing else ""))
    db.close()


if __name__ == "__main__":
    main()
