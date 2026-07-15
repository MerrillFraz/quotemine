#!/usr/bin/env python3
"""
mfcc_coherence.py — numpy-only acoustic fingerprinting, no librosa, no GPU.

Purpose: catch the "clip of 4 lines where 1 is a completely different character"
problem. pyannote's MAX_SPEAKERS=10 overflow misfiles a lead's line into a
minor character's bucket. Each line is individually clean; the CLUSTER is
contaminated. We can't fix the diarization, but before stitching a preview we
can fingerprint each candidate line and keep only the subset that agrees.

This catches GROSS mismatches (Cheryl vs Archer). It will NOT reliably split
acoustically-close voices (Cyril vs Krieger) — those stay a job for your ears.

Public API:
    fp = mfcc_fingerprint(samples, sr)          # -> 1D np.float32 vector
    keep_idx = coherent_subset(fingerprints)    # -> indices of the agreeing majority
"""

import numpy as np

# ---- MFCC parameters (speech-tuned, 16kHz) --------------------------------
N_FFT = 512
HOP = 160          # 10ms at 16kHz
WIN = 400          # 25ms
N_MELS = 40
N_MFCC = 13
FMIN, FMAX = 80.0, 7600.0

# ---- coherence parameters -------------------------------------------------
COHERENCE_COS = 0.55   # two lines "agree" if cosine >= this (on MFCC-mean fp)
MIN_SUBSET = 2         # need at least this many agreeing lines to trust a clip


def _hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def _mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _mel_filterbank(sr):
    lo, hi = _hz_to_mel(FMIN), _hz_to_mel(FMAX)
    pts = _mel_to_hz(np.linspace(lo, hi, N_MELS + 2))
    bins = np.floor((N_FFT + 1) * pts / sr).astype(int)
    fb = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float32)
    for m in range(1, N_MELS + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        if c == l:
            c += 1
        if r == c:
            r += 1
        for k in range(l, c):
            if 0 <= k < fb.shape[1]:
                fb[m - 1, k] = (k - l) / max(1, (c - l))
        for k in range(c, r):
            if 0 <= k < fb.shape[1]:
                fb[m - 1, k] = (r - k) / max(1, (r - c))
    return fb


def _dct_matrix():
    n = np.arange(N_MELS)
    k = np.arange(N_MFCC).reshape(-1, 1)
    D = np.cos(np.pi * k * (2 * n + 1) / (2 * N_MELS)).astype(np.float32)
    D *= np.sqrt(2.0 / N_MELS)
    D[0] *= 1.0 / np.sqrt(2.0)
    return D


_HANN = np.hanning(WIN).astype(np.float32)
_FB_CACHE = {}
_DCT = _dct_matrix()


def mfcc_fingerprint(samples, sr):
    """
    One fixed-length fingerprint per utterance: MFCC mean + std concatenated.
    Robust to length; captures timbre, not content.
    """
    if sr not in _FB_CACHE:
        _FB_CACHE[sr] = _mel_filterbank(sr)
    fb = _FB_CACHE[sr]

    x = np.asarray(samples, dtype=np.float32)
    if x.size < WIN:
        x = np.pad(x, (0, WIN - x.size))

    frames = []
    for start in range(0, x.size - WIN + 1, HOP):
        frames.append(x[start:start + WIN] * _HANN)
    if not frames:
        frames = [x[:WIN] * _HANN]
    F = np.stack(frames)

    spec = np.abs(np.fft.rfft(F, n=N_FFT, axis=1)) ** 2
    mel = spec @ fb.T
    logmel = np.log(mel + 1e-8)
    mfcc = logmel @ _DCT.T              # (frames, N_MFCC)

    # liftering-free; mean+std over time is enough for a timbre fingerprint
    fp = np.concatenate([mfcc.mean(axis=0), mfcc.std(axis=0)]).astype(np.float32)
    n = np.linalg.norm(fp)
    return fp / n if n > 0 else fp


def coherent_subset(fingerprints):
    """
    Given per-line fingerprints, return indices of the largest mutually-agreeing
    group. This is the "drop the misfiled line" logic:

      1. medoid = the line most similar to all others
      2. keep every line within COHERENCE_COS of the medoid
      3. if fewer than MIN_SUBSET survive, the clip has no coherent core -> []

    A misfiled Archer line in a Cheryl cluster sits far from the 3 real Cheryl
    lines' medoid and gets dropped. A true 50/50 merge has no majority and
    returns [] (caller should discard the whole cluster).
    """
    F = np.stack(fingerprints)
    if len(F) == 1:
        return [0]
    S = F @ F.T
    medoid = int(np.argmax(S.sum(axis=1)))
    sims = F @ F[medoid]
    keep = [i for i in range(len(F)) if sims[i] >= COHERENCE_COS]
    return keep if len(keep) >= MIN_SUBSET else []


# ---- self-test ------------------------------------------------------------
if __name__ == "__main__":
    sr = 16000
    rng = np.random.default_rng(0)

    def tone(f, dur=1.0, noise=0.02):
        t = np.arange(int(sr * dur)) / sr
        sig = 0.3 * np.sin(2 * np.pi * f * t)
        sig += 0.15 * np.sin(2 * np.pi * 2 * f * t)   # a harmonic, for timbre
        return sig + noise * rng.standard_normal(t.size)

    # three "voice A" lines around 200Hz, one "voice B" intruder at 500Hz
    lines = [tone(200), tone(205), tone(198), tone(500)]
    fps = [mfcc_fingerprint(x, sr) for x in lines]
    keep = coherent_subset(fps)
    print("fingerprint dim:", fps[0].shape[0])
    print("kept indices   :", keep, "(expect [0,1,2], intruder 3 dropped)")
    assert 3 not in keep, "intruder not dropped"
    assert set(keep) == {0, 1, 2}, "wrong subset"
    print("PASS")
