# Quotemine

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
- Preview audio clips for auditioning, and **cleaned, packaged final clips**
  with a manifest mapping them to your target's events.

## The six stages

| Stage | Script | Does | Compute |
|-------|--------|------|---------|
| 1. Index | `pipeline/01_index.py` | demux audio → transcribe (WhisperX) → diarize (pyannote) → index to SQLite+FTS5 | GPU |
| 2. Identify | `pipeline/02_identify.py` | tag reference lines by hand → build neural voiceprint centroids → attribute every cluster to a name | GPU + ~1hr human |
| 3. Match | `pipeline/03_match.py` | keyword (FTS5) + semantic (MiniLM) matching of lines to your event list | GPU |
| 4. Audition | `pipeline/04_audition.py` | preview ranked candidates in a browser board → keep/reject → record picks | ffmpeg + human |
| 5. Clean | `pipeline/05_clean.py` | re-cut picks from original source, loudnorm + fades | ffmpeg |
| 6. Package | `pipeline/06_package.py` | assemble clips + manifest; optional project-specific layout | ffmpeg |

Stages 1–3 need the GPU; 4–6 need only ffmpeg. Diagnostics
(`pipeline/diag_*.py`, `pipeline/mfcc_coherence.py`) are optional tools for
validating speaker separation; see `docs/pipeline.md`.

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
pip install -r requirements.txt          # whisperx, transformers, pyannote, numpy
# optional, to run the test suite:
pip install -r requirements-dev.txt
export HF_TOKEN=hf_...        # your token; never commit this

# Every stage takes --project (default: archer_wot, the bundled example).
python pipeline/01_index.py --project archer_wot scan /path/to/videos
python pipeline/01_index.py --project archer_wot demux
python pipeline/01_index.py --project archer_wot transcribe
python pipeline/01_index.py --project archer_wot diarize
python pipeline/01_index.py --project archer_wot index

python pipeline/02_identify.py --project archer_wot sample
#   ... tag reference lines in the browser, export refs.json ...
python pipeline/02_identify.py --project archer_wot embed work/archer_wot/refs.json
python pipeline/02_identify.py --project archer_wot assign --dry-run
python pipeline/02_identify.py --project archer_wot assign --threshold 0.50

python pipeline/03_match.py --project archer_wot events
python pipeline/03_match.py --project archer_wot match
python pipeline/03_match.py --project archer_wot show <pool_id>

python pipeline/04_audition.py --project archer_wot sample
#   ... keep the lines you want in the browser, export picks.json ...
python pipeline/04_audition.py --project archer_wot import work/archer_wot/picks.json
python pipeline/05_clean.py --project archer_wot
python pipeline/06_package.py --project archer_wot
```

Stages 4–6 (audition → clean → package) need **ffmpeg**, not the GPU. Stage 5
re-cuts finals from the original source at full quality; Stage 6 writes a
manifest (and, for `archer_wot`, a Wwise-oriented layout via its `package.py`).

Each project's data lives under `work/<project>/` (e.g. `work/archer_wot/corpus.db`).

Full walkthrough in **`docs/pipeline.md`**. Read **`docs/gotchas.md`** before you
start — it will save you a day.

## Adapting it to your own source

The three stage scripts are generic; everything specific to *your* material
lives in one file. To point the pipeline at a different show, film, or game:

```bash
cp -r projects/_template projects/my_project
# edit projects/my_project/config.py, then run any stage with --project my_project
```

`projects/my_project/config.py` supplies five things (see the heavily-commented
`projects/_template/config.py` and the worked example in `projects/archer_wot/`):

- **`parse(filename)`** — how your filenames encode provenance, returning two
  orderable ints `(group_idx, item_idx)` (a TV show → season, episode; a film →
  disc, scene; a game may return `(None, None)`).
- **`label(group_idx, item_idx)`** — how to display that provenance.
- **`CHARACTERS`** — the roster you'll tag and attribute.
- **`BANDS`** — optional era-groupings for voice drift (set `None` if your
  source has no drift; the pipeline uses one implicit band).
- **`POOLS`** — your event definitions (keywords + description + target IDs).
- **`TUNING`** — optional per-corpus overrides (see `pipeline/project.py` for
  the full default set; `MAX_SPEAKERS` especially is worth setting per source).

## License / responsibility

The **code** is yours to use. The **content** you run it on is not: extracting
and redistributing audio from copyrighted material is your responsibility, not
this tool's. A private, local index is one thing; publishing a pack built from
someone else's IP is another. Know which you're doing.
