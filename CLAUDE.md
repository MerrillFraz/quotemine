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
- `pipeline/01_index.py`  — demux, transcribe, diarize, index  [GPU]
- `pipeline/02_identify.py` — tag → centroids → assign          [GPU + human]
- `pipeline/03_match.py`  — keyword + semantic event matching   [GPU]

## Conventions
- Everything reads/writes one SQLite DB: `work/corpus.db`.
- Stages are idempotent and resumable via the `jobs` table.
- Never commit: `env.sh`, tokens, `work/`, audio, `.db`. (See `.gitignore`.)
- Tuning constants live at the top of each stage script.

## Git conventions
- Conventional commits: feat:, fix:, docs:, refactor:
- Subject lines under 72 chars.

## Never
- Reintroduce clip-based tagging (see gotchas: per-utterance is deliberate).
- Install whisperx before torch (pulls CPU wheel).
- Build an auto-purity filter for short utterances (proven not to work).
- Build out audition/clean/package stages as if they're missing — they're
  intentionally left to the user downstream of `03_match.py`. See docs/pipeline.md.
