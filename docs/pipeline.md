# Pipeline walkthrough

Three stages, run in order. Each stage's steps are idempotent and resumable —
a crash or reboot mid-run costs you the current item, not the whole stage,
because progress is tracked in a `jobs` table in SQLite.

Everything reads and writes one database, `work/corpus.db`. Keep `work/` on a
fast local filesystem (on WSL, use ext4 inside the distro, **not** a `/mnt/`
drive — see `gotchas.md`).

---

## Stage 1 — Index (`01_index.py`)

Builds the searchable corpus. Four sub-steps, all sharing one job queue.

### `scan <video_dir>`
Discovers video files, parses `SxxExx` season/episode from filenames, populates
the `episodes` table. **Filenames must contain a parseable `SxxExx`** (or `NxNN`)
or provenance is lost. Rename before scanning; the command warns on any file it
can't parse.

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
pyannote clusters voices *within each episode* → every word tagged
`SPEAKER_00`, `SPEAKER_01`, etc. **These labels are episode-local** — `SPEAKER_00`
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
character across 3+ era-bands** (voices drift over a long-running show; a centroid
built on one era underperforms on others). Export `refs.json`.

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

Ranks attributed lines against a list of events.

### `events`
Loads/prints the event definitions (edit the `EVENTS` list in the script). Each
event has: keywords (for FTS), a natural-language description (for semantic
search), and an optional suggested character (a hint, not a filter).

### `match`  *(GPU once, then cached)*
Two passes per event:
- **Keyword** — FTS5 over the transcript. Catches obvious hits.
- **Semantic** — embeds the event *description* (MiniLM) and ranks every line by
  meaning. Surfaces the perfect line that shares no keywords — the real value.

Text embeddings are cached in the DB, so re-tuning descriptions and re-running is
instant.

### `list` / `show <event_id>`
Inspect candidate counts and top-ranked lines per event. A `*` marks keyword
hits; high-`sem` rows *without* a star are pure semantic finds.

**Expect two classes of event:** "personality" events (taunt, confusion, victory)
overflow with great lines because they match how a character actually talks;
"mechanical" events (reload, tracks-damaged) are sparse because scripted dialogue
has no literal equivalent — you match on *energy*, looser.

---

## Downstream (not included)

- **Audition** — cut every candidate to a padded preview, review in a board with
  players + keep flags. (Build to taste.)
- **Clean** — loudnorm to a target level, bandpass for character, trim, fades.
  Cut finals from **original** source, not the 16 kHz WAV.
- **Package** — assemble into whatever your target wants (game soundbank, JSON
  manifest, sampler, etc.).

## Schema (the important tables)

- `episodes` — one row per source file; season/episode, paths, duration.
- `utterances` — the corpus. Transcript, timestamps, duration, `speaker`
  (episode-local), `character` (global, from Stage 2), word count.
- `utterances_fts` — FTS5 mirror of `utterances.text`.
- `centroids` — per-character voiceprint vectors.
- `clusters` — per-cluster assignment + similarity.
- `events`, `event_candidates` — event definitions and ranked matches.
- `jobs` — per-episode, per-stage progress (the resumability backbone).
