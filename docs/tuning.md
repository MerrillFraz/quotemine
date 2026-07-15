# Tuning

Per-corpus knobs live in your project's `TUNING` dict
(`projects/<name>/config.py`); the defaults they fall back to are in
`pipeline/project.py` (`DEFAULT_TUNING`). Only hardware/model knobs (batch size,
compute type, model names) stay at the top of the stage scripts. The names below
are the `TUNING` keys. Defaults were tuned on a 14-season animated series on a
12 GB RTX 3080; your corpus and card will differ. Re-run cost is noted per knob.

---

## Stage 1 — Index

### `MAX_SPEAKERS` (diarize)
The single most consequential knob. Set it **close to the real per-item
speaker ceiling** for your source.
- Too high → 6–15x slower diarization for identical results (wasted search).
- Too low → silent speaker merges (see `gotchas.md`).
Check with a histogram of `COUNT(DISTINCT speaker)` per episode after a trial
run. Re-run cost: full diarize pass (but it's often the fast stage — minutes,
not hours).

### `COMPUTE_TYPE`, `BATCH_SIZE` (transcribe)
`float16` + `batch_size 16` suits a 12 GB Ampere card. Drop batch to 8 on OOM;
drop to `int8` only if truly VRAM-starved (quality cost).

### Utterance segmentation (index)
`MAX_WORD_GAP_S`, `MIN_UTTERANCE_S`, `MAX_UTTERANCE_S` control how the word
stream splits into utterances. **Cheap to retune** — the `index` step reads
cached JSON, no GPU. Re-run in seconds and inspect the callout-window count.

---

## Stage 2 — Identify

### Sampling filters (`sample`)
- `MIN_SNR_DB` (default 8) — per-line cleanliness floor. Raise to hear only
  pristine lines; lower if the pool is starved. Watch the reported drop count.
- `TALK_FLOOR_S` (default 60) — skip clusters below this much total talk-time.
  Raise to exclude bit-players (where merges concentrate); lower if you're
  missing a genuinely minor character.
- `UTT_MIN_S` / `UTT_MAX_S` — candidate length window. Shorter lines are purer
  (less chance of spanning a speaker change) but carry less identity signal.
  `ISOLATION_PAD_S` (default 0.5) — how much silence must flank a line for it to
  count as isolated (no overlapping speaker).
Re-run cost: re-cut previews (CPU, ~1–2 min). Existing browser tags survive
(keyed by cluster identity).

### `embed` — read the `tight` scores
Not a knob, a gauge. Per-character mean self-similarity.
- **> 0.5** — coherent centroid, proceed.
- **< 0.45 (LOOSE)** — references disagree; a mis-tag slipped in or the lines are
  too noisy. Re-tag before assigning.

### `assign --threshold`
The decision knob. Run `--dry-run` and read the similarity histogram:
- Ideal shape is **bimodal** — a high mountain (real matches) and a low lump
  (guests + merges). Put the threshold in the valley.
- **Lean permissive** (lower threshold) if a downstream human audition step will
  catch false positives anyway — recall matters more than precision when you have
  a large candidate surplus and a human in the loop.
- **Lean strict** (higher) if the attributions feed something automated with no
  review.
Re-run cost: the dry-run already did the GPU embedding; picking a new threshold
and running for real is fast.

`CLUSTER_EMBED_UTTS` (default 12) — how many isolated lines to average per
cluster for its assignment vector. More = steadier vector, slower pass. 12 is a
good balance; averaging is what rescues short-clip embedding noise.

---

## Stage 3 — Match

### The `POOLS` list
The main thing you'll edit — lives in your project's config
(`projects/<name>/config.py`), alongside `TUNING`. Each pool = keywords +
description + suggested character + a list of
real target game event IDs it's wired to. A pool maps to *multiple* game
events on purpose: downstream, each pool becomes one Random Container wired
to fire on every event mapped to it. **Reconcile the game event IDs against
your actual target** (for a game soundbank, the real event strings from the
sound project). The pool names/IDs themselves are just search labels; they
don't have to match your target.

- **Keywords** drive the FTS pass — literal terms that might appear in a matching
  line. Quote-safe (apostrophes handled).
- **Description** drives the semantic pass — a natural-language sentence of what
  the pool *means*. This is where quality lives: "urgent, the enemy is capturing
  our base" ranks far better than "base capture". Sharpen these using your
  target's own event descriptions if it has them.

#### Match the vibe, not the verb
Your source splits pools into two kinds (see `gotchas.md`):
- **Personality** pools (taunt, victory, confusion, annoyance) — the source
  overflows with these because they match how a character actually *talks*.
  Description tuning pays off richly here.
- **Mechanical** pools (reload, module-damaged, base-captured) — scripted
  dialogue has no literal equivalent, so literal keywords rarely hit and even a
  perfect description only reorders a thin set. **Don't chase a specific action
  that isn't in your source.** Describe the emotional *vibe* — the tone a line
  would carry — and accept looser scores: "sudden alarm, something just went
  badly wrong" finds more usable lines than "engine destroyed".

Tuning only ever *reorders the lines your source already contains* — it can't
conjure a line that was never spoken (no re-indexing happens; the corpus is
fixed and line embeddings are cached). So aim description effort at pools where
the material actually exists.

A mechanical pool with no useful literal terms may leave `keywords` empty
(`""`); the keyword pass is skipped and the pool matches on description alone.

**Re-tune loop (cheap):** edit the pool, then run `events` (reloads the pool
table from your config) and `match`. Forgetting `events` re-ranks against the
*old* descriptions — a common gotcha.

### `TOP_SEMANTIC` (default 60)
How many semantic hits to keep per pool. Raise for more candidates to audition,
lower for a tighter list. Re-run cost: instant (embeddings cached).

### `KW_BONUS` (default 0.08)
How much a keyword hit boosts a line's combined score above its raw semantic
score. Raise to favor literal matches, lower to trust semantics more. Keep it
small relative to the semantic score range (~0.3–0.6) so a keyword hit on a
filler word can't outrank a genuinely better semantic match.

### Candidate window (`CAND_MIN_S` / `CAND_MAX_S`)
Duration filter for what's eligible as a candidate. Match this to your output's
needs — game callouts want short (0.4–2.0 s); a general soundboard might want
wider.
