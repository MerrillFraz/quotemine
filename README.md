# voiceover-pipeline

Turn an archive of video (a TV series, a film set, a game's cutscenes — anything
with recurring speakers) into a **searchable, speaker-attributed,
word-timestamped line database**, then match lines to a set of events to build a
voice pack, soundboard, or annotated corpus.

It answers questions like *"give me every line character X says, under two
seconds, that reads as an angry taunt"* — in milliseconds, across dozens of hours
of source — so a human only has to audition a short ranked list instead of
scrubbing the whole archive by ear.

The original use case was a game voice pack (replacing crew callouts with lines
from a TV show), but nothing here is specific to that. The output is a plain
SQLite database plus ranked candidate lists; what you do with them is up to you.

---

## What it produces

- A **SQLite database** (`corpus.db`) of every spoken utterance across your
  archive, each with: transcript, word-level start/end timestamps, source
  episode, duration, and an attributed **character/speaker name**.
- A **full-text search index** (FTS5) over every line.
- Per-event **ranked candidate lists** combining keyword and semantic search.
- Preview audio clips for auditioning.

## The three stages

| Stage | Script | Does | Compute |
|-------|--------|------|---------|
| 1. Index | `pipeline/01_index.py` | demux audio → transcribe (WhisperX) → diarize (pyannote) → index to SQLite+FTS5 | GPU |
| 2. Identify | `pipeline/02_identify.py` | tag reference lines by hand → build neural voiceprint centroids → attribute every cluster to a name | GPU + ~1hr human |
| 3. Match | `pipeline/03_match.py` | keyword (FTS5) + semantic (MiniLM) matching of lines to your event list | GPU |

Diagnostics (`pipeline/diag_*.py`, `pipeline/mfcc_coherence.py`) are optional
tools for validating speaker separation; see `docs/pipeline.md`.

## Requirements

- **NVIDIA GPU** (developed on a 12 GB RTX 3080; smaller works with tuning).
- **Linux / WSL2.** Native Windows is possible but every dependency here is
  Linux-first; do yourself a favor.
- **Python 3.10–3.13**, in a dedicated venv.
- **ffmpeg** on PATH.
- A **HuggingFace account + token**, and acceptance of the pyannote model
  license (see `docs/gotchas.md` — this has a silent failure mode).

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
# torch FIRST, from the pytorch index (see docs/gotchas.md for why)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install whisperx transformers
export HF_TOKEN=hf_...        # your token; never commit this

python pipeline/01_index.py scan /path/to/videos
python pipeline/01_index.py demux
python pipeline/01_index.py transcribe
python pipeline/01_index.py diarize
python pipeline/01_index.py index

python pipeline/02_identify.py sample
#   ... tag reference lines in the browser, export refs.json ...
python pipeline/02_identify.py embed work/refs.json
python pipeline/02_identify.py assign --dry-run
python pipeline/02_identify.py assign --threshold 0.50

python pipeline/03_match.py events
python pipeline/03_match.py match
python pipeline/03_match.py show <event_id>
```

Full walkthrough in **`docs/pipeline.md`**. Read **`docs/gotchas.md`** before you
start — it will save you a day.

## License / responsibility

The **code** is yours to use. The **content** you run it on is not: extracting
and redistributing audio from copyrighted material is your responsibility, not
this tool's. A private, local index is one thing; publishing a pack built from
someone else's IP is another. Know which you're doing.
