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
        """SELECT pk.pool_id, pk.utterance_id, pk.head_s, pk.tail_s,
                  u.start_s, u.end_s, u.character, e.path AS src
           FROM picks pk
           JOIN utterances u ON u.id = pk.utterance_id
           JOIN episodes e   ON e.id = u.episode_id
           ORDER BY pk.pool_id""").fetchall()
    if not picks:
        sys.exit("No picks. Run Stage 4 `import <picks.json>` first.")

    final_root = proj.workdir / "final"
    db.execute("DELETE FROM finals")
    done = missing = empty = 0
    compress = t.get("COMPRESS_VO", False)
    floor_db = t.get("VO_PEAK_FLOOR_DBFS", -2.0)
    ceiling_db = downstream.compress_gain_ceiling_db(
        t.get("VO_SPEECHNORM_E", 12.5), t.get("VO_MAKEUP", 3.0))
    levels = []
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
            # per-clip lead-in/lead-out from Stage 4 rides on top of CLEAN_PAD_S.
            # An over-tightened delta can drive the span <= 0; cut returns False
            # without writing tmp, so skip rather than hand clean_audio a missing
            # file (an ffmpeg error there would abort the whole batch pre-commit).
            if not downstream.cut_from_source(
                    r["src"], r["start_s"], r["end_s"], tmp,
                    pad_head=t["CLEAN_PAD_S"] + (r["head_s"] or 0.0),
                    pad_tail=t["CLEAN_PAD_S"] + (r["tail_s"] or 0.0)):
                print(f"  [skip] empty span for utt {r['utterance_id']} "
                      f"(head_s/tail_s too tight)")
                empty += 1
                continue
            downstream.clean_audio(tmp, out, lufs=t["LOUDNORM_LUFS"],
                                   bandpass_hz=t["BANDPASS_HZ"], fade_ms=t["FADE_MS"],
                                   compress=compress,
                                   speechnorm_e=t.get("VO_SPEECHNORM_E", 12.5),
                                   makeup=t.get("VO_MAKEUP", 3.0))
        finally:
            if tmp.exists():
                tmp.unlink()
        # The COMPRESS_VO chain is open-loop: it has a finite gain ceiling and
        # reports success whether the clip reached ~0 dBFS or landed 10 dB
        # short. Measure so a too-quiet pack is visible here, not in-game.
        if compress:
            peak, mean = downstream.measure_level(out)
            if peak is not None:
                levels.append((peak, mean, r["pool_id"], r["utterance_id"]))
        db.execute("INSERT OR REPLACE INTO finals VALUES (?,?,?)",
                   (r["pool_id"], r["utterance_id"], str(out.relative_to(proj.workdir))))
        done += 1
    db.commit()
    print(f"\n[clean] {done} clips cleaned into {final_root}"
          + (f"  ({missing} skipped: source missing)" if missing else "")
          + (f"  ({empty} skipped: empty span)" if empty else ""))
    _report_levels(levels, floor_db, ceiling_db)
    db.close()


def _report_levels(levels, floor_db, ceiling_db):
    """Summarize measured output levels and name the clips that fell short."""
    if not levels:
        return
    peaks = sorted(p for p, _, _, _ in levels)
    means = sorted(m for _, m, _, _ in levels if m is not None)
    med = lambda xs: xs[len(xs) // 2] if xs else float("nan")
    print(f"[clean] measured peak: median {med(peaks):.1f} dB, "
          f"worst {peaks[0]:.1f} dB   (RMS median {med(means):.1f} dB)")
    print(f"[clean] COMPRESS_VO ceiling is ~{ceiling_db:.1f} dB of lift; a source "
          f"peaking below ~{-ceiling_db:.0f} dBFS cannot reach the target.")
    short = sorted((l for l in levels if l[0] < floor_db))
    if not short:
        return
    print(f"\n[clean] WARNING: {len(short)} of {len(levels)} clips peak below "
          f"{floor_db:.1f} dB — they will sit under the game mix:")
    for peak, mean, pool_id, uid in short[:12]:
        rms = f", RMS {mean:.1f}" if mean is not None else ""
        print(f"           {peak:6.1f} dB{rms}   {pool_id}/{uid}")
    if len(short) > 12:
        print(f"           ... and {len(short) - 12} more")
    print("         Re-cut these with more lead-in/lead-out, drop them, or raise "
          "VO_SPEECHNORM_E / VO_MAKEUP (see docs/tuning.md).")


if __name__ == "__main__":
    main()
