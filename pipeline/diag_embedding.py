#!/usr/bin/env python3
"""
verify_embedding.py — before committing a GPU pass, prove the neural embedding
model (a) loads under this pyannote build and (b) separates acoustically-close
voices (Krieger/Cyril/Pam) where MFCC-mean fingerprints failed.

Runs on ONE cluster. If the intruder lines separate here, the full purity pass
is worth it. If they don't, Option A is no better than Option B and we stop.

    python pipeline/diag_embedding.py --group 6 --item 3 --speaker SPEAKER_04
"""

import argparse
import os
import sqlite3
import wave
from pathlib import Path

import numpy as np

from project import load_project


CANDIDATES = [
    "pyannote/wespeaker-voxceleb-resnet34-LM",
    "pyannote/embedding",
    "speechbrain/spkrec-ecapa-voxceleb",   # fallback, different loader
]


def load_span(wav, s, e):
    with wave.open(str(wav), "rb") as w:
        fr = w.getframerate()
        w.setpos(int(s * fr))
        raw = w.readframes(int((e - s) * fr))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, fr


def try_load_pyannote(name, tok, device):
    from pyannote.audio import Model, Inference
    import torch
    model = Model.from_pretrained(name, use_auth_token=tok)
    model.to(torch.device(device)).eval()
    inf = Inference(model, window="whole", device=torch.device(device))
    return ("pyannote", inf)


def embed_pyannote(inf, sig, sr):
    from pyannote.core import SlidingWindowFeature
    import torch
    wf = torch.from_numpy(sig).float().unsqueeze(0)   # (1, samples)
    out = inf({"waveform": wf, "sample_rate": sr})
    v = np.asarray(out, dtype=np.float32).reshape(-1)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="archer_wot")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--group", type=int, required=True, help="group_idx (e.g. season)")
    ap.add_argument("--item", type=int, required=True, help="item_idx (e.g. episode)")
    ap.add_argument("--speaker", required=True)
    ap.add_argument("--min-s", type=float, default=0.7)
    ap.add_argument("--max-s", type=float, default=2.5)
    a = ap.parse_args()

    tok = os.environ.get("HF_TOKEN")
    if not tok:
        raise SystemExit("HF_TOKEN not set.")

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device: {device}\n")

    # --- find a model that loads ---
    loader = None
    for name in CANDIDATES:
        try:
            print(f"trying model: {name} ...", end=" ", flush=True)
            if name.startswith("speechbrain"):
                print("skip (needs speechbrain pkg; try only if others fail)")
                continue
            loader = try_load_pyannote(name, tok, device)
            print("LOADED")
            model_used = name
            break
        except Exception as e:
            print(f"failed: {type(e).__name__}: {str(e)[:80]}")
    if loader is None:
        raise SystemExit("\nNo embedding model loaded. Paste this output and we'll fix the string.")

    kind, inf = loader

    # --- pull the cluster's lines ---
    proj = load_project(a.project, a.workdir)
    db = sqlite3.connect(proj.workdir / "corpus.db")
    db.row_factory = sqlite3.Row
    ep = db.execute("SELECT id, wav_path FROM episodes WHERE group_idx=? AND item_idx=?",
                    (a.group, a.item)).fetchone()
    rows = db.execute(
        "SELECT start_s, end_s, text FROM utterances "
        "WHERE episode_id=? AND speaker=? AND duration_s BETWEEN ? AND ? ORDER BY start_s",
        (ep["id"], a.speaker, a.min_s, a.max_s)).fetchall()
    print(f"\nmodel: {model_used}")
    print(f"{proj.label(a.group, a.item)} {a.speaker} — {len(rows)} lines\n")

    vecs, texts = [], []
    for r in rows:
        sig, sr = load_span(ep["wav_path"], r["start_s"], r["end_s"])
        vecs.append(embed_pyannote(inf, sig, sr))
        texts.append(r["text"])

    V = np.stack(vecs)
    S = V @ V.T

    print("pairwise cosine (neural embedding):")
    print("     " + " ".join(f"{i:>5}" for i in range(len(vecs))))
    for i in range(len(vecs)):
        print(f"{i:>2}   " + " ".join(f"{S[i,j]:>5.2f}" for j in range(len(vecs))) + f"   {texts[i][:42]}")

    # crude split: how many pairs disagree below 0.5? MFCC saw NONE here.
    off = (S < 0.5).sum() - 0   # includes diagonal? no, diagonal is 1.0
    print(f"\npairs below 0.50 cosine: {int(off/2)}  (MFCC found 0 — it saw one blended voice)")
    print("If distinct sub-groups appear here, the neural model separates what MFCC couldn't.")


if __name__ == "__main__":
    main()
