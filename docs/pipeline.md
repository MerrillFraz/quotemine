# Pipeline walkthrough

Three stages, run in order. Each stage's steps are idempotent and resumable —
a crash or reboot mid-run costs you the current item, not the whole stage,
because progress is tracked in a `jobs` table in SQLite.

Each project reads and writes one database, `work/<project>/corpus.db` (e.g.
`work/archer_wot/corpus.db`). Every stage takes `--project <name>`, resolved
against `projects/<name>/config.py` (see `pipeline/project.py`). Keep `work/` on
a fast local filesystem (on WSL, use ext4 inside the distro, **not** a `/mnt/`
drive — see `gotchas.md`).

---

## Stage 1 — Index (`01_index.py`)

Builds the searchable corpus. Four sub-steps, all sharing one job queue.

### `scan <video_dir>`
Discovers video files, extracts provenance from each filename via the project's
`parse()` (for `archer_wot`, `SxxExx` → season/episode), populates the
`episodes` table with `group_idx`/`item_idx`. **Filenames must parse** or
provenance is lost — rename before scanning; the command warns on any file
`parse()` returns `(None, None)` for. Provenance is just two orderable ints;
what they mean is the project's business.

### `demux`  *(CPU)*
ffmpeg extracts a 16 kHz mono WAV per episode. ~10 s/episode. This is the only
copy used for the ML steps; final audio cuts should come from the original
source, not this downsample.

### `transcribe`  *(GPU)*
WhisperX (large-v3) ASR **plus wav2vec2 forced alignment**. The alignment is the
point — it produces genuine **word-level timestamps**, not Whisper's vague
segment-level ones. You need word-level to cut clips on tight boundaries later.
Output: one JSON per episode. Loads models once, processes all episodes in a
single pass.

### `diarize`  *(GPU)*
pyannote clusters voices *within each item* → every word tagged
`SPEAKER_00`, `SPEAKER_01`, etc. **These labels are item-local** — `SPEAKER_00`
in one episode is unrelated to `SPEAKER_00` in another. Stage 2 turns them into
global names. Also captures pyannote's per-cluster embeddings for free (see
`gotchas.md` — this is a deliberate optimization).

### `index`  *(CPU)*
Reads the JSONs, splits the word stream into utterance-sized units (on speaker
change, word-gap, and a max-length ceiling), loads the `utterances` table and an
FTS5 full-text index. **Re-runnable any time without touching the GPU** — retune
the segmentation constants at the top of the file and re-run in seconds. The
`character` column starts NULL; Stage 2 fills it.

**Check:** `status` shows per-stage progress. After `index`, you should see a
count of utterances and how many fall in a usable callout window (0.4–2.0 s).

---

## Stage 2 — Identify (`02_identify.py`)

Turns episode-local `SPEAKER_xx` labels into global character names via
**nearest-centroid on neural voiceprints**. No model training — it's hand-labeled
references, averaged into a centroid per character, then cosine-matched.

### `sample`  *(CPU)*
Cuts one preview clip **per candidate utterance** (not per cluster — see
`gotchas.md` for why this matters), filtered for cleanliness (SNR floor) and
isolation (no overlapping speaker). Emits `tagger_utt.html`, grouped by cluster
so same-voice lines are adjacent.

### Tag (human, ~1 hr)
Serve `work/` over HTTP and open the tagger. Play a line, click the character.
**Approve clean lines, skip only misfiled ones.** Because you tag individual
utterances, a merged cluster (two people under one label) costs you one skip per
bad line instead of the whole cluster. Aim for **12+ approved lines per
character, spread across your project's bands** (voices drift over a long-running
show; a centroid built on one era underperforms on others). A project with no
`BANDS` uses one implicit band, so this reduces to "12+ lines per character."
Export `refs.json`.

### `embed <refs.json>`  *(GPU)*
Neural-embeds every approved line, averages per character into 8 (or N)
centroids. Prints a `tight` score per character — mean agreement of each
character's references with their own centroid. **>0.5 = coherent. <0.45 =
LOOSE**, meaning a mis-tag or too-noisy references; go re-tag before proceeding.

### `assign [--dry-run] [--threshold T]`  *(GPU)*
For every cluster in the corpus, embeds up to N isolated lines and averages them
into a cluster vector, then cosine-matches to the nearest centroid. Merged
clusters average to a blend that matches nothing and fall below threshold →
NULL, rather than mislabeling.

**Always `--dry-run` first.** It prints the similarity histogram. Look for a
bimodal shape — a high-similarity mountain (real matches) and a low lump (guests,
merges). The threshold goes in the valley between them. Then run for real to
write the `character` column onto every utterance.

---

## Stage 3 — Match (`03_match.py`)

Ranks attributed lines against a list of pools, each wired to one or more
real target game events (see the project's `POOLS`, e.g.
`projects/archer_wot/config.py`).

### `events`
Loads/prints the pool definitions (edit the `POOLS` list in
`projects/<name>/config.py`, not `03_match.py` itself). Each pool has: keywords (for FTS), a
natural-language description (for semantic search), an optional suggested
character (a hint, not a filter), and a list of real target game event IDs
that pool is wired to. A pool maps to *multiple* game events on purpose —
each pool becomes one Random Container downstream, wired to fire on every
event mapped to it.

### `match`  *(GPU once, then cached)*
Two passes per pool:
- **Keyword** — FTS5 over the transcript. Catches obvious hits.
- **Semantic** — embeds the pool *description* (MiniLM) and ranks every line by
  meaning. Surfaces the perfect line that shares no keywords — the real value.

Text embeddings are cached in the DB, so re-tuning descriptions and re-running is
instant.

### `list` / `show <pool_id>`
Inspect candidate counts and top-ranked lines per pool. A `*` marks keyword
hits; high-`sem` rows *without* a star are pure semantic finds.

**Expect two classes of event:** "personality" events (taunt, confusion, victory)
overflow with great lines because they match how a character actually talks;
"mechanical" events (reload, tracks-damaged) are sparse because scripted dialogue
has no literal equivalent — you match on *energy*, looser.

---

## Stage 4 — Audition (`04_audition.py`)

Turn ranked candidates into your actual picks. Shares `downstream.py` helpers;
needs ffmpeg, not the GPU.

### `sample`  *(CPU)*
Cuts a padded preview per top-N candidate (N = `AUDITION_TOP_N`) from the 16 kHz
WAV into `work/<project>/audition/`, and emits `audition.html` — a keep/reject
board grouped by pool (players, localStorage, same pattern as the Stage 2 tagger).

### Keep (human)
Serve `work/<project>/` over HTTP, open the board, play lines, **Keep** the ones
you want. Export `picks.json`.

### `import <picks.json>`
Loads the kept `(pool, utterance)` pairs into the `picks` table.

## Stage 5 — Clean (`05_clean.py`)  *(CPU)*

For every pick, re-cut the span from the **original source** video
(`episodes.path`) at full quality — **not** the 16 kHz ML WAV — then loudnorm to
`LOUDNORM_LUFS`, optional `BANDPASS_HZ`, and head/tail fades (`FADE_MS`). Keeps
the intentional `CLEAN_PAD_S` padding; does **not** silence-trim (that would
strip the padding and gut quiet clips). Outputs to `work/<project>/final/<pool>/`
and records them in the `finals` table.

## Stage 6 — Package (`06_package.py`)  *(CPU)*

Assembles the finals into a deliverable. **Generic default:** copy clips into
`package/<pool>/` + write `manifest.json` (each pool → its wired game events →
its clips). **Project override:** if `projects/<name>/package.py` defines
`package(ctx)`, that runs instead for a target-specific layout — e.g.
`archer_wot` emits Wwise `RC_<pool>/` Random Container folders plus a
`wwise_import.csv`. This is the "projects may carry code" pattern; the hook
composes the shared `downstream.py` helpers.

## Schema (the important tables)

- `episodes` — one row per source file; `group_idx`/`item_idx` (ordered
  provenance, e.g. season/episode), paths, duration.
- `utterances` — the corpus. Transcript, timestamps, duration, `speaker`
  (item-local), `character` (global, from Stage 2), word count.
- `utterances_fts` — FTS5 mirror of `utterances.text`.
- `centroids` — per-character voiceprint vectors.
- `clusters` — per-cluster assignment + similarity.
- `pools`, `pool_events`, `pool_candidates` — pool definitions, their mapped
  game events, and ranked line matches.
- `text_emb` — cached MiniLM text embeddings for utterances (Stage 3).
- `picks` — kept `(pool, utterance)` pairs from the audition (Stage 4).
- `finals` — cleaned final clips per pick, with output path (Stage 5).
- `jobs` — per-episode, per-stage progress (the resumability backbone).
