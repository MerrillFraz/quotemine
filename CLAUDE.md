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

## Stage scripts (run in order)
Every stage takes `--project <name>` (default `archer_wot`), resolved against
`projects/<name>/config.py`. See `pipeline/project.py` for the contract.
- `pipeline/01_index.py`  — demux, transcribe, diarize, index  [GPU]
- `pipeline/02_identify.py` — tag → centroids → assign          [GPU + human]
- `pipeline/03_match.py`  — keyword + semantic event matching   [GPU]
- `pipeline/04_audition.py` — preview candidates → keep/reject → picks [human]
- `pipeline/05_clean.py`  — re-cut finals from source, loudnorm/fades [CPU]
- `pipeline/06_package.py` — assemble clips + manifest (project hook)  [CPU]

Stages 4–6 share `pipeline/downstream.py` (clip cutting, cleaning, board,
manifest helpers). They need ffmpeg, not the GPU.

## Project vs. engine (portability)
- The stages are generic. Everything specific to a corpus — filename parsing,
  character roster, era-bands, event pools, and per-corpus tuning — lives in
  `projects/<name>/config.py`. `projects/_template/` is the starting point;
  `projects/archer_wot/` is the worked example (Archer → World of Tanks).
- Provenance is neutral: `group_idx`/`item_idx` are orderable ints (a TV show
  maps them to season/episode; a film/game may leave them null). Nothing in
  `pipeline/` assumes television.
- **Projects may carry code, not just config.** An optional
  `projects/<name>/package.py` with a `package(ctx)` function overrides Stage 6's
  generic packaging for a target-specific layout (see `archer_wot/package.py`,
  which emits Wwise `RC_` containers). It composes `pipeline/downstream.py`
  helpers rather than reinventing them.

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
  the point, and aggressive trimming guts quieter clips. loudnorm + fades only.
- Cut finals from the 16 kHz working WAV. Stage 5 re-cuts from the original
  source (`episodes.path`) for full quality; the 16 kHz WAV is ML-only.
