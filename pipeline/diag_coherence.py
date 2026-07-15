#!/usr/bin/env python3
"""
probe_coherence.py — prove (or disprove) the MFCC filter on YOUR audio.

Pick a cluster you had to skip because one line was a different character.
This shows the per-line fingerprint agreement and which lines the filter
would keep vs drop — so you can confirm it works on real voices before it
goes anywhere near the sampler.

    python pipeline/diag_coherence.py --group 6 --item 3 --speaker SPEAKER_04
"""

import argparse
import sqlite3
import wave
from pathlib import Path

import numpy as np
from mfcc_coherence import mfcc_fingerprint, coherent_subset, COHERENCE_COS
from project import load_project


def load_span(wav, s, e):
    with wave.open(str(wav), "rb") as w:
        fr = w.getframerate()
        w.setpos(int(s * fr))
        raw = w.readframes(int((e - s) * fr))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, fr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="archer_wot")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--group", type=int, required=True, help="group_idx (e.g. season)")
    ap.add_argument("--item", type=int, required=True, help="item_idx (e.g. episode)")
    ap.add_argument("--speaker", required=True)
    ap.add_argument("--min-s", type=float, default=0.7)
    ap.add_argument("--max-s", type=float, default=2.2)
    a = ap.parse_args()

    proj = load_project(a.project, a.workdir)
    db = sqlite3.connect(proj.workdir / "corpus.db")
    db.row_factory = sqlite3.Row
    ep = db.execute("SELECT id, wav_path FROM episodes WHERE group_idx=? AND item_idx=?",
                    (a.group, a.item)).fetchone()
    if not ep:
        raise SystemExit("item not found")

    rows = db.execute(
        "SELECT start_s, end_s, text FROM utterances "
        "WHERE episode_id=? AND speaker=? AND duration_s BETWEEN ? AND ? "
        "ORDER BY start_s",
        (ep["id"], a.speaker, a.min_s, a.max_s)).fetchall()
    if len(rows) < 2:
        raise SystemExit(f"only {len(rows)} candidate lines; need >=2")

    fps, texts = [], []
    for r in rows:
        sig, sr = load_span(ep["wav_path"], r["start_s"], r["end_s"])
        fps.append(mfcc_fingerprint(sig, sr))
        texts.append(r["text"])

    F = np.stack(fps)
    S = F @ F.T
    keep = set(coherent_subset(fps))

    print(f"\n{proj.label(a.group, a.item)} {a.speaker} — {len(rows)} candidate lines")
    print(f"coherence threshold: {COHERENCE_COS}\n")
    medoid = int(S.sum(axis=1).argmax())
    print(f"{'#':>2} {'keep':>5} {'vs medoid':>10}  text")
    for i, t in enumerate(texts):
        mark = "KEEP" if i in keep else "drop"
        print(f"{i:>2} {mark:>5} {S[i, medoid]:>10.3f}  {t[:60]}")

    print("\npairwise cosine matrix:")
    print("    " + " ".join(f"{i:>5}" for i in range(len(fps))))
    for i in range(len(fps)):
        print(f"{i:>2}  " + " ".join(f"{S[i,j]:>5.2f}" for j in range(len(fps))))

    if not keep:
        print("\n=> NO coherent majority. This is a true merge; whole cluster would be discarded.")
    elif len(keep) < len(rows):
        print(f"\n=> would build clip from {len(keep)}/{len(rows)} lines, dropping the outlier(s).")
    else:
        print("\n=> all lines agree; clean cluster.")


if __name__ == "__main__":
    main()
