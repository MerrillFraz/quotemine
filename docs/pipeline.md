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
event mapped to it. A pool may also carry an optional **7th field**, a
state-routing filter the engine passes through untouched for `package.py` to
use (see `tuning.md`) — how one event gets split into several context-specific
pools.

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
Cuts a generously padded preview (`PREVIEW_EDIT_PAD_S`) per top-N candidate
(N = `AUDITION_TOP_N`) from the 16 kHz WAV into `work/<project>/audition/`, and
emits `audition.html` — a keep/reject board grouped by pool (players,
localStorage, same pattern as the Stage 2 tagger).

### `serve`  *(CPU)*
Serve `work/<project>/` over HTTP with a **Range-capable** handler, so the board's
audio is seekable (the lead-in ▶ needs it). Use this — **not** `python -m
http.server`, which ignores Range and silently breaks the seek.

### Keep + tune (human)
Serve the board with `04_audition serve` (**not** `python -m http.server` — the
stdlib server ignores HTTP Range requests, which leaves audio non-seekable in the
browser and silently breaks the lead-in ▶ seek). Open the board, play lines,
**Keep** the ones you want. On a kept clip you can nudge its **lead-in / lead-out**
(in−/in+,
out−/out+, `NUDGE_STEP_S` per press; `▶` auditions just that window inside the
padded preview). Positive widens, negative tightens — the fix for a clip that
starts late or runs long, per clip, without disturbing the others. Export
`picks.json` (carries `head_s`/`tail_s`).

**Clip-uniqueness guard:** the same utterance can be a candidate in several pools
(a "Boom!" fits both `good_hit` and `first_kill`). The board tracks keeps by
utterance across pools and **soft-warns** on reuse — a clip kept in two pools is
flagged, one already used elsewhere shows a "used in `<pool>`" note before you
keep it, and the HUD shows a running unique-clip count. Nothing is blocked (you
*can* reuse deliberately), but you can't lose track of it. Useful when a pack
aims to never repeat a clip.

### `import <picks.json>`
Loads the kept `(pool, utterance)` pairs — with their `head_s`/`tail_s` deltas
(default 0) — into the `picks` table. Pre-tuning two-field files still load.

## Stage 5 — Clean (`05_clean.py`)  *(CPU)*

For every pick, re-cut the span from the **original source** video
(`episodes.path`) at full quality — **not** the 16 kHz ML WAV — then loudnorm to
`LOUDNORM_LUFS`, optional `BANDPASS_HZ`, and head/tail fades (`FADE_MS`). Keeps
the intentional `CLEAN_PAD_S` padding plus each pick's `head_s`/`tail_s` lead-in/
lead-out delta from Stage 4; does **not** silence-trim (that would
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

The `archer_wot` hook also emits everything the Wwise + `.wotmod` build needs:
`audio_mods.xml` (WoT's event-remap descriptor — every original `vo_*` event
redirected to that pool's `vo_qm_<pool>` mod event, generated straight from the
pool→events map), `meta.xml` (the `.wotmod` manifest), and `wwise_events.txt`
(the per-pool checklist for the Wwise GUI). See `projects/archer_wot/WWISE.md`
for the full build walkthrough.

## Assemble the mod (`build_wotmod.py`)  *(CPU, WoT-specific)*

WoT sound mods are **additive event-remaps**, not soundbank overwrites: the game
loads your `.bnk` alongside its own and redirects events. After you build the
soundbank in Wwise (the one GUI/Windows step — see `WWISE.md`),
`pipeline/build_wotmod.py --project <name> --banks <GeneratedSoundBanks/WinHighRes>`
bundles the generated `.bnk` + `audio_mods.xml` + `meta.xml` into an installable
`res/audioww/…` tree, zipped (STORED) as `<Mod>.wotmod`. It fails loudly if a
bank named in the descriptor is missing from `--banks`.

### The other packaging pattern: WoWs state-routed overrides (`archer_wows`)

A second worked example, `archer_wows`, shows a very different target. WoWs voice
mods are **not** an event-remap + compiled bank — they're a folder of loose
`.wem` files plus a `mod.xml` (`AudioModification.xml`) that **overrides the file
list of the game's own containers**. You inherit Wargaming's containers, so their
randomization, voice priority, and ducking come for free — you just supply the
audio. Key differences from the WoT pattern:

- **`package.py` clones a vendored `reference_mod.xml`** (the real event/state
  schema, extracted from a shipping pack) and, per pool, fills only the `<Path>`
  blocks whose states match that pool's 7th-field filter. Unmatched paths are
  **dropped**, so uncovered states fall back to the game's default voice — a
  partial pack is safe and silent-gap-free.
- **Multiple voices per event** is just multiple `<File>` entries in a
  `<FilesList>`; the game's container random-picks one. No Wwise container to
  author.
- **`build_wowsmod.py --project <name> --wems <dir>`** verifies every `.wem`
  named in `mod.xml` is present, then zips the `res_mods/banks/Mods/<Mod>/` tree.
- **The one external step is WAV→`.wem`** (Wwise Vorbis). Encode headlessly with a
  `WwiseConsole` wrapper (e.g. `sound2wem`); the encode preserves loudness, so do
  the loudness work in Stage 5 (`COMPRESS_VO`), not here.

The generic engine is identical for both — only `config.py` (pools, optional
state filters) and `package.py` (the deliverable layout) differ. That's the
portability claim, demonstrated twice.

---

## Variant packs over one corpus (`fork_corpus.py`, `phrases.py`)

Stages 1–2 produce something entirely project-agnostic: a speaker-attributed
line database. Stages 3–6 — which pools exist, what got picked, how it's
packaged — are the project-specific half, and they're cheap. So a *second*
project over the same corpus needs **no GPU at all**.

The worked example is `archer_wot_sterling`: the same Archer corpus and the same
17 WoT events as `archer_wot`, but every pool restricted to one character and
seeded with his catchphrases.

### `fork_corpus.py`  *(CPU, seconds)*

```
python pipeline/fork_corpus.py --from archer_wot --to archer_wot_sterling
```

Copies `corpus.db` and clears **only** the project layer (`pools`,
`pool_events`, `pool_candidates`, `picks`, `finals`), keeping the transcripts,
the human character tagging, and the `text_emb` cache — which is keyed by
`utterance_id` alone and therefore valid for any pool set.

Two things worth knowing:

- **Audio is shared, not copied.** `episodes.wav_path` is absolute and keeps
  pointing at the parent's `wav/`. A fork is ~80 MB against ~6 GB for a full
  workdir — but it *depends* on its parent, so deleting the parent's `work/`
  breaks every fork's Stage 4 previews. (Stage 5 re-cuts from `episodes.path`,
  the original video, so finals are unaffected.)
- **Clearing `pools` is load-bearing.** `03_match` only loads POOLS when that
  table is empty, so a fork that kept it would silently reuse its parent's pools
  forever.

### `phrases.py`  *(CPU + network)*

Builds the catchphrase lists that `POOL_FILTERS` consumes. Episode wikis carry a
per-episode "Running Gags / Callbacks" section that is, structurally, a
speaker-attributed catchphrase index — this turns it into config.

```
python pipeline/phrases.py --project archer_wot scrape          # cache wikitext
python pipeline/phrases.py --project archer_wot mine            # rank by canon
python pipeline/phrases.py --project archer_wot probe --character Archer
```

- **`scrape`** walks a wiki category through `api.php` (~4 requests for 137
  pages) and caches the raw wikitext to `projects/_data/`. Use the API, not a
  page fetcher: Fandom answers **HTTP 402** to generic fetchers.
- **`mine`** aggregates the gag bullets into `{phrase: {character: episodes}}`.
  The episode count is the **fan-canonicity signal** — "phrasing" is cited by 19
  episodes, "danger zone" 8, against a long tail cited once.
- **`probe`** is the reality check, and the step that makes the rest safe. It
  scores every candidate against the actual corpus and applies a stoplist. Two
  independent failure modes it exists to catch:
  - *Generic filler.* "shut up" (206 corpus hits), "hang on" (97) and "hello"
    (53) all scrape as legitimate running gags. At `PHRASE_BONUS` they would
    swamp every pool. `projects/_data/phrase_stoplist.txt` holds them.
  - *Wrong speaker.* Wiki attributions are often the person being spoken *to*,
    and raw corpus counts are worse — Archer has 42 % of all lines, so counting
    alone credits him with "get some", "idiot", "burn" and "chet". `probe`
    attributes by **rate** (hits ÷ that character's share of the corpus), which
    recovers Pam, Malory, Cheryl and Cyril respectively.

Fan lists are a hypothesis; the transcript database is the evidence. Expect
roughly a quarter of scraped gags to survive.

`mine` and `probe` print to **stdout only** — nothing is persisted. The
surviving phrases get hand-transcribed into the project's `_PHRASES` dict (see
`projects/archer_wot_sterling/config.py`), and the assignment of a phrase to a
*pool* is an editorial judgement no tool makes for you. `--emit` prints a
paste-ready `POOL_FILTERS` skeleton to shorten the copy.

### Then the normal downstream stages

Stages 3–6 run unchanged. `_print_counts` warns loudly about any pool that ends
up with **no** candidates — for a single-character pack that means the game
falls back to its stock line for that event, which is a decision to make
deliberately, not discover in-game.

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
- `picks` — kept `(pool, utterance)` pairs from the audition (Stage 4), each with
  `head_s`/`tail_s` lead-in/lead-out deltas (seconds, default 0).
- `finals` — cleaned final clips per pick, with output path (Stage 5).
- `jobs` — per-episode, per-stage progress (the resumability backbone).
