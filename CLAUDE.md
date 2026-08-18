# CLAUDE.md — Quotemine

Generic pipeline: video archive → speaker-attributed searchable line
database → event-matched candidate lists → auditioned, cleaned, packaged
deliverable. Six stages (three GPU) + human tagging + human audition.

## Read first
- `docs/gotchas.md` — hard-won failure modes. Read before changing anything
  in the pipeline. Especially: MAX_SPEAKERS overflow, pyannote license silent
  failure, torch-first install, per-utterance (not clip) tagging.
- `docs/pipeline.md` — stage walkthrough + schema.
- `docs/tuning.md` — every knob and its re-run cost.
- `docs/backlog.md` — open problems and forward-looking work. Read before
  touching WoWs packaging: the unresolved in-game loudness bug is logged there
  with what's already been ruled out, so it doesn't get re-derived.

## Plan Mode

When writing a plan, always specify:
- The exact file paths that will be touched
- The order in which they'll be modified
- Which changes are independent vs. which depend on a prior step completing

Do not proceed to execution until the plan lists files and sequence explicitly.

## Stage scripts (run in order)
Stages are `pipeline/0N_*.py`, run in numeric order; see the stage table in
`README.md` and `pipeline/project.py` for the `--project` contract.

Stages 4–6 share `pipeline/downstream.py` (clip cutting, cleaning, board,
manifest helpers). They need ffmpeg, not the GPU.

## Variant packs over an already-mined corpus (no GPU)
- `pipeline/fork_corpus.py --from A --to B` — copy `corpus.db`, clear only the
  project layer (`pools`, `pool_events`, `pool_candidates`, `picks`, `finals`).
  Keeps transcripts, human tagging, and the `text_emb` cache. Audio is *shared*
  (absolute `episodes.wav_path`), so a fork is ~80 MB, not ~6 GB — and it
  depends on its parent's `work/` for Stage 4 previews.
- `pipeline/phrases.py scrape|mine|probe` — build catchphrase lists from an
  episode wiki's "Running Gags" sections. Always `probe` before shipping a list:
  fan lists are a hypothesis, the transcript DB is the evidence.
- `projects/archer_wot_sterling/` — worked example: same corpus, same 17 WoT
  events as `archer_wot`, restricted to one character via `POOL_FILTERS`.

## Project vs. engine (portability)
- The stages are generic. Everything specific to a corpus — filename parsing,
  character roster, era-bands, event pools, and per-corpus tuning — lives in
  `projects/<name>/config.py`. `projects/_template/` is the starting point.
  Two worked examples, the same Archer corpus to two very different targets:
  `projects/archer_wot/` (→ World of Tanks, the simple case) and
  `projects/archer_wows/` (→ World of Warships, the advanced case — 65
  state-segregated pools, in-game VO loudness).
- Provenance is neutral: `group_idx`/`item_idx` are orderable ints (a TV show
  maps them to season/episode; a film/game may leave them null). Nothing in
  `pipeline/` assumes television.
- A POOLS entry may carry an **optional 7th field** — an opaque state-routing
  filter the engine passes through untouched, for a project's `package.py` to
  map one event onto several context-specific pools. Routing specifics (state
  names) stay in the project layer; the engine never learns them.
- **Projects may carry code, not just config.** An optional
  `projects/<name>/package.py` with a `package(ctx)` function overrides Stage 6's
  generic packaging for a target-specific layout. `archer_wot/package.py` emits
  Wwise `RC_` containers + a `.wotmod` event-remap (via `pipeline/build_wotmod.py`);
  `archer_wows/package.py` clones a reference `mod.xml` and state-routes each pool
  (via `pipeline/build_wowsmod.py`). Both compose `pipeline/downstream.py` helpers
  rather than reinventing them.
- **Shared target layout lives in the engine, identity lives in the project.**
  `pipeline/build_wotpack.py` holds the whole WoT emitter; a project's
  `package.py` is an `Identity` (mod id, bank name, display name) plus a
  `build()` call. Adding a WoT pack should never mean copying that emitter.
- **A project may restrict candidates by character** via the `POOL_FILTERS`
  tuning knob (`{"*": {"chars": [...]}, "<pool>": {"phrases": [...]}}`). Note
  `suggested_char` (POOLS field 3) is display metadata and is *never* a filter —
  that contract is unchanged and `archer_wows` depends on it.

## Conventions
- Each project reads/writes one SQLite DB: `work/<project>/corpus.db`.
- Stages are idempotent and resumable via the `jobs` table.
- Never commit: `env.sh`, tokens, `work/`, audio, `.db`. (See `.gitignore`.)
- Per-corpus tuning lives in a project's `TUNING` dict (see `pipeline/project.py`
  for `DEFAULT_TUNING`). Only hardware/model knobs (batch size, model names)
  stay at the top of the stage scripts.

## Git conventions
- Conventional commits: feat:, fix:, docs:, refactor:
- Subject lines under 72 chars.

## Never
- Reintroduce clip-based tagging (see gotchas: per-utterance is deliberate).
- Install whisperx before torch (pulls CPU wheel).
- Build an auto-purity filter for short utterances (proven not to work).
- Silence-trim final clips (Stage 5): the intentional CLEAN_PAD_S head/tail is
  the point, and aggressive trimming guts quieter clips. Level (loudnorm, or the
  `COMPRESS_VO` game-VO maximizer) + fades only — never silence-trim.
- Master in-game voice to a broadcast target. Game voice is mastered hot (~0 dBFS
  peaks); `-16 LUFS` is inaudible under the mix. Use `COMPRESS_VO` (see
  gotchas/tuning) and measure short clips by RMS, not integrated LUFS.
- Cut finals from the 16 kHz working WAV. Stage 5 re-cuts from the original
  source (`episodes.path`) for full quality; the 16 kHz WAV is ML-only.
- Serve the audition board with `python -m http.server`. The stdlib server
  ignores HTTP Range, so the browser can't seek and the board's lead-in ▶
  silently does nothing (the lead-out still works, which hides it). Use
  `pipeline/04_audition.py --project <name> serve` (Range-capable).
