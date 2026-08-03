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

#### Segregating one event by state (optional 7th field)

A pool tuple may carry a **7th element**: a state-routing filter, or `None`.
The engine treats it as **opaque** (Stage 3 ignores it); only a project's
`package.py` interprets it, to route a pool to a *subset* of a target event's
states. This lets several pools share one event, each firing in a different
context. WoWs, for instance, splits one torpedo-alarm event by bearing:

```python
("torpedo_left",  "...", "Lana", "left port", "...",
 ["Play_VO_Ship_Alarms_Torpedo_Danger"], {"VO_Torpedo_Location": ["Torpedo_Left"]}),
("torpedo_right", "...", "Lana", "right starboard", "...",
 ["Play_VO_Ship_Alarms_Torpedo_Danger"], {"VO_Torpedo_Location": ["Torpedo_Right"]}),
```

`{state_var: [allowed_values]}` — a target state-path matches when every listed
variable holds an allowed value. `None` (or a 6-tuple) fills the whole event with
no segregation (the default). Because it's opaque to the engine, the state
*names* live entirely in the project layer — the pipeline stays game-agnostic.
See `projects/archer_wows/` for the worked example (65 pools; `package.py` does
the path matching). Matching, auditioning, and the generic manifest all work
unchanged whether a pool carries the field or not.

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

### `POOL_CAND_WINDOWS` (default `{}`) — per-pool duration override
`{pool_id: (min_s, max_s)}` overrides the global `CAND_MIN_S`/`CAND_MAX_S` for
pools whose lines *aren't* terse callouts. The global window is tuned for short
barks, so a longer beat gets filtered out before matching ever sees it — a
battle-start rally line runs 3–6 s and can never surface under a 2.2 s cap.
```python
"POOL_CAND_WINDOWS": {"battle_start": (3.0, 7.5)},
```
Pools not listed keep the global window. `match` embeds the **union** of all
windows once (cached) and then filters each pool to its own, so adding one wide
pool doesn't disturb the candidates (or your existing picks) for any other pool.
Re-run cost: one-time embed of the newly-eligible longer lines, then instant.

### `POOL_KW_BONUS` (default `{}`) — per-pool keyword-bonus override
`{pool_id: bonus}` overrides the global `KW_BONUS` for pools where the **literal
words are the signal**. Some beats are carried by terse trash-talk ("Nailed it",
"Boom", "Rampage!") that scores *low* on semantic similarity — so a good keyword
hit stays buried under mediocre semantic lines. A large per-pool bonus floats the
keyword hits to the top of the board instead:
```python
"POOL_KW_BONUS": {"you_penetrated": 0.6},   # vs global KW_BONUS ~0.03
```
Pair it with a **tight, distinctive** keyword list (a floated bonus surfaces
*every* keyword hit, so noisy terms show too — the FTS tokenizer also stems, e.g.
`hurt` matches `hurtful`). Pools not listed use the global `KW_BONUS`. Re-run
cost: instant (embeddings cached) — but remember to run `events` before `match`
whenever you change a pool's keywords.

---

## Stages 4–6 — Downstream

All downstream knobs are TUNING keys too. Re-run cost is cheap (ffmpeg, no GPU).

### `AUDITION_TOP_N` (default 25)
Candidates per pool put on the audition board (`04_audition sample`). Raise to
review deeper into the ranking, lower for a tighter board.

### `PREVIEW_EDIT_PAD_S` (default 1.0)
Padding on audition preview clips (cut from the 16 kHz WAV). Deliberately
generous: it's the headroom the board expands into when you nudge a clip's
lead-in/lead-out, so you hear the adjusted window without re-cutting. Doesn't
affect finals; the previewed **window** base is `CLEAN_PAD_S`, not this value.

### `NUDGE_STEP_S` (default 0.05)
How much one lead-in/lead-out button press moves a clip's boundary on the board.

### Per-clip lead-in / lead-out (a human adjustment, not a re-run knob)
On the audition board, a kept clip carries a signed **head** and **tail** delta
(dialed with the in−/in+ and out−/out+ buttons, `▶` to audition just that
window). They export in `picks.json` (`head_s`/`tail_s`) and land on the `picks`
row, then Stage 5 adds them on top of `CLEAN_PAD_S`: positive widens (more
lead-in / lead-out), negative tightens. This is how you fix a single clip that
starts late or runs long **without** moving every other clip — unlike the global
pads below. A clip left at 0/0 cuts exactly as before.

### `CLEAN_PAD_S` (default 0.10)
Padding kept on the **final** cut from the original source, and the base of the
board's play-window. This head/tail is intentional — it keeps clips from
sounding hard-clipped. Stage 5 does **not** silence-trim, so this padding
survives. Per-clip head/tail deltas (above) stack on top of this per clip.

### `LOUDNORM_LUFS` (default -16.0)
Integrated-loudness target for finals (ffmpeg `loudnorm`), used **only when
`COMPRESS_VO` is off**. A −16 LUFS broadcast target suits a soundboard or web
export. It is the **wrong target for in-game voice** — and worse, EBU R128
integrated measurement is invalid below ~3 s, so on the short callouts a game
pack is full of, `loudnorm` under-processes and leaves clips peak-shy and quiet
(see `gotchas.md`). For a game target, ignore this knob and use `COMPRESS_VO`.

### `COMPRESS_VO` (default False)
Swaps the Stage-5 `loudnorm` for a **voice-over maximizer** — `highpass` →
`speechnorm` → `acompressor` → `alimiter` — that slams every clip (short *or*
long) to ~0 dBFS peak with high RMS, matching how game voice is mastered.
- **On** for in-game VO that must cut through a loud mix (gunfire, engines).
  Broadcast loudness is inaudible in a game; measured against a shipping pack,
  game voice sits ~−10 dB RMS with peaks at 0.0 (see `gotchas.md`).
- **Off** for broadcast/soundboard output, where `LOUDNORM_LUFS` governs instead.

It **replaces** `loudnorm` rather than running before it, so `LOUDNORM_LUFS` has
no effect at all while this is on — don't retune it expecting a change.

Re-run cost: cheap (ffmpeg re-cut of finals). Calibrate by decoding a known-good
pack and matching its `volumedetect` numbers, not integrated LUFS.

### `VO_SPEECHNORM_E` / `VO_MAKEUP` / `VO_PEAK_FLOOR_DBFS`
The gain available to the `COMPRESS_VO` chain, and the floor for reporting when
it wasn't enough.

The chain has a **finite ceiling**: `speechnorm` contributes at most
`20·log10(VO_SPEECHNORM_E)` dB and the compressor's makeup another
`20·log10(VO_MAKEUP)` — about **21.9 + 9.5 = 31.4 dB** at the defaults. A clip
whose source peaks below roughly −31 dBFS therefore *cannot* reach the ~0 dBFS
target however hard the chain tries, and ffmpeg reports success either way.

Stage 5 closes that loop: it measures every compressed clip with
`volumedetect`, prints the median and worst peak, and names any clip peaking
below `VO_PEAK_FLOOR_DBFS` (default −2.0). When that fires, the fix is usually
per-clip — re-cut with more lead-in/lead-out so the chain has more signal to
work with, or drop the line — before reaching for the gain knobs, which buy
level at the cost of noise floor.

### `BANDPASS_HZ` (default None)
`(low, high)` to band-limit the final (e.g. `(300, 3400)` for a radio/telephone
character), or `None` to leave full-band.

### `FADE_MS` (default 15)
Head/tail fade on finals, to avoid clicks. Applied both ends.

### Packaging
Layout is code, not a knob: the generic default writes `package/<pool>/` +
`manifest.json`. For a target-specific layout, add `projects/<name>/package.py`
with a `package(ctx)` function (see `projects/archer_wot/package.py`).
