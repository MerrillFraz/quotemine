# CLAUDE.md — voiceover-pipeline

Generic pipeline: video archive → speaker-attributed searchable line
database → event-matched candidate lists. Three GPU stages + human tagging.

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

## Project vs. engine (portability)
- The three stages are generic. Everything specific to a corpus — filename
  parsing, character roster, era-bands, event pools, and per-corpus tuning —
  lives in `projects/<name>/config.py`. `projects/_template/` is the starting
  point; `projects/archer_wot/` is the worked example (Archer → World of Tanks).
- Provenance is neutral: `group_idx`/`item_idx` are orderable ints (a TV show
  maps them to season/episode; a film/game may leave them null). Nothing in
  `pipeline/` assumes television.

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
- Build out audition/clean/package stages as if they're missing — they're
  intentionally left to the user downstream of `03_match.py`. See docs/pipeline.md.
