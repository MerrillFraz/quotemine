# Gotchas

Every one of these cost real time to discover. Read before you start.

---

## Install & environment

### Install torch FIRST, from the PyTorch index
```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install whisperx transformers      # only after torch is in
```
If you install whisperx first, pip resolves torch as a transitive dependency and
may pull the **CPU wheel**. Everything imports and runs — at 1/50th the speed.
After installing whisperx, re-check: `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`.
You want a `+cuXXX` suffix and `True`. A bare version with no CUDA suffix means
you got the CPU build.

### whisperx will replace your torch — that's usually fine
Installing whisperx may uninstall your torch and install its own (e.g.
`2.6.0+cu124` → `2.8.0+cu128`). Fine **as long as the `+cuXXX` suffix survives**.
The CUDA version riding along changes with it; that's expected.

### cuDNN 9, CUDA 12
Current CTranslate2 (WhisperX's ASR backend) wants CUDA 12 + cuDNN 9. Installing
torch-first from the cu124 index gets this right automatically. The classic
failure is an error mentioning `libcudnn_ops_infer.so.8` — that's a cuDNN version
mismatch; fix by aligning versions, not downgrading torch.

### Verify CUDA actually works, not just that it's "available"
`torch.cuda.is_available()` returning True is weak. Do a real kernel launch and
check the *other* runtime:
```python
import torch, ctranslate2
print(bool((torch.randn(2048,2048,device="cuda") @ torch.randn(2048,2048,device="cuda")).sum().isfinite()))
print(ctranslate2.get_cuda_device_count())   # must be >= 1
```
CTranslate2 finds CUDA independently of torch. If it prints 0, ASR silently runs
on CPU.

### WSL: driver on Windows, NOT inside the distro
Install the NVIDIA driver on **Windows**. Do **not** install a Linux NVIDIA
driver inside WSL — that's the #1 way to break CUDA passthrough. The pip torch
wheels ship their own CUDA runtime; driver + wheels is the whole story.

### Keep working data on ext4, not /mnt/
On WSL, put `work/` (the DB, WAVs, previews) on the ext4 filesystem inside the
distro. The SQLite DB especially: file locking over the `/mnt/` drvfs layer is
slow and occasionally flaky, and you'll have a resumable job queue hammering it.
Reading *source video* from `/mnt/` is fine — big sequential reads amortize
against GPU time.

---

## The pyannote license silent-failure trap

pyannote's diarization models are **gated** on HuggingFace. You must (a) have an
`HF_TOKEN` and (b) accept the model's user conditions on its HF page.

The trap: WhisperX may use a **different** pyannote model than the tutorials
assume (e.g. `speaker-diarization-community-1` vs `3.1`). If you accept the
license for the wrong one, diarization **runs to completion, exits clean, and
produces ZERO speaker labels** — silently. No error. You discover it hours later
when every row is unattributed.

Mitigation: the diarize step counts labeled words per episode and screams if it
gets zero. Watch the first two episodes before walking away. If you see zero
labels, find which model your whisperx build actually pulls, accept *that*
license, re-run.

---

## MAX_SPEAKERS overflow (the big one)

pyannote's clustering takes a `max_speakers` bound. Two things about it:

1. **Setting it too high wastes enormous time.** The clustering *searches* the
   allowed range. Give it max=12 on an episode that truly has 10 speakers and it
   burns 6x the wall-time exploring 11 and 12 before settling on 10 — same
   answer, far slower. Symptom: some episodes take 6–15x longer than others for
   no visible reason. Set the bound close to the real ceiling.

2. **Setting it too low silently merges people.** If an episode has more real
   speakers than the cap, pyannote doesn't drop anyone — it **crushes the overflow
   into the nearest existing cluster.** You get a "cluster" that's mostly
   character A with a few of character B's lines spliced in. Its per-cluster
   voiceprint is then a contaminated blend.

Diagnostic: `SELECT season, COUNT(DISTINCT speaker) FROM ...` — if *every*
episode reports exactly `max_speakers` clusters, you're capping and merging.
Natural speaker counts vary; a flat line pinned at the cap is the fingerprint.

There's no free fix — raising the cap reintroduces problem #1 and the VRAM cost.
The practical answer is to accept some merged clusters and handle them
downstream (see next two entries).

---

## Tag individual utterances, not clips

Early versions bundled several utterances into one preview "clip" to give the
tagger more audio per click. **This is a mistake** once you know clusters can be
merged: one misfiled line in a 4-line clip forces you to reject all four,
throwing away good references.

Tag **individual utterances**. A misfiled line costs one skip, not a cluster. You
become the filter at the granularity where human judgment actually works — you
can hear "that's a different person" instantly, which no automated check does
reliably for *acoustically similar* voices.

---

## Automated purity filtering doesn't work at utterance length

Tempting idea: auto-detect merged clusters by embedding each line and checking
agreement. Two approaches, both fail:

- **MFCC-mean fingerprints** catch *gross* mismatches (very different voices) but
  cannot separate acoustically-close ones (e.g. two similar male voices). Worse,
  they confidently rate a genuine 3-person merge as "clean."
- **Neural speaker embeddings** (wespeaker/ECAPA) *can* separate close voices in
  principle, but they're trained on multi-second utterances and return near-noise
  on the sub-second clips a callout corpus is full of.

Conclusion: don't build an auto-purity filter for short lines. Let the human tag
per-utterance (above), and let assignment route merges to NULL (below). The
`diag_*.py` scripts exist to *prove* this to yourself on your own audio before
you trust any filter.

---

## Assignment routes merges to NULL — lean on it

Stage 2 `assign` builds each cluster's vector by averaging its isolated lines.
A merged cluster averages to a blend that's close to *neither* centroid, so it
falls below the similarity threshold and gets NULL instead of a wrong label.
This is why you can tolerate the overflow merges: they self-select into the
reject pile at assignment. Pick your threshold from the dry-run histogram to sit
in the valley between the "real match" mountain and the "matches nothing" lump.

---

## FTS5 syntax errors on apostrophes

Building an FTS5 `MATCH` query by joining raw keywords breaks on any term with an
apostrophe (`can't` → `fts5: syntax error near "'"`). Wrap each term in double
quotes so FTS5 treats it as a literal token: `'"' + word + '"'`. Applies to any
punctuation FTS5 treats as an operator.

---

## Two classes of event, by design

When matching lines to events, expect a split:
- **Personality events** (taunt, annoyed, victory, confusion) overflow with
  great candidates — they match how a character actually talks.
- **Mechanical events** (reload, tracks-damaged, enemy-spotted) are sparse —
  scripted dialogue has no literal equivalent. Match on *energy/tone*, not
  vocabulary, and accept looser semantic scores.

This isn't a bug; it's the source material telling you the truth. A drama doesn't
contain literal game callouts.

---

## The audition board must be served Range-capable

The Stage-4 board tunes each clip's in/out point by seeking a hidden `<audio>`
element (`currentTime = winStart`). Browsers **refuse to seek** media whose server
doesn't answer HTTP **Range** requests with `206 Partial Content` — `seekable`
stays empty and every seek clamps to 0. Python's stdlib `http.server` ignores
`Range` and returns `200` + the whole file, so the lead-**in** nudge *silently
does nothing* while the lead-**out** (a pause, no seek) still works — which masks
the cause completely. Serve with `pipeline/04_audition.py --project <name> serve`
(a Range-capable handler), never `python -m http.server`.

Related board-audio lesson: stop windowed playback with a `requestAnimationFrame`
poll of `currentTime`, **not** the `timeupdate` event — `timeupdate` fires only
~every 250 ms, so 0.05 s out-cut nudges appear to do nothing until they cross a
tick, then drop a whole quarter-second, and the stop point jitters run-to-run.

---

## Game-voice loudness is not broadcast loudness

Finals default to `loudnorm` at −16 LUFS — a **broadcast** target. Dropped into a
game, that voice is **inaudible under the mix** (gunfire, engines, music). Game
voice is mastered *hot*: peaks slammed to ~0 dBFS, RMS around −10 dB. A −16 LUFS
/ −1.5 dBTP clip is 5–7 dB quieter and leaves headroom the game's own voice
doesn't. Symptom: "the pack fires but I can barely hear it," and turning the
in-game Voice slider up doesn't rescue it (it raises *everything* on that bus).

Two traps hide inside this:

1. **`loudnorm`'s integrated LUFS is meaningless on short clips.** EBU R128
   integrated measurement gates and needs ~3 s of audio; a 0.4 s "Nailed it."
   reads as −28 LUFS *as an artifact*, so `loudnorm` under-processes it and
   leaves it **peak-shy** (max −1 to −2 dBFS, not slammed to 0). A callout corpus
   is mostly sub-3 s clips, so this hits nearly everything. `loudnorm` is the
   wrong tool here.

2. **Measuring by integrated LUFS misleads you the same way.** Compare clips with
   a length-independent metric — `ffmpeg -af volumedetect` (mean = RMS, max =
   peak) — not `loudnorm=print_format`.

**The fix** is the `COMPRESS_VO` tuning knob (Stage 5): it swaps `loudnorm` for a
maximizer — `highpass` → `speechnorm` (lifts the whole clip toward full scale) →
`acompressor` → `alimiter` — that slams every clip, short or long, to ~0 dBFS
peak with high RMS, exactly like game voice. Turn it on for any in-game VO
target; leave it off for broadcast/soundboard output.

**How to calibrate against a real target:** decode a *known-good* pack's audio
and match its numbers. WoWs/WoT ship Wwise Vorbis `.wem` (`fmt 0xFFFF`), which
`ffmpeg` can't read — decode with `ww2ogg` (build from source; the repo bundles
the codebooks) then `volumedetect`. That A/B is what revealed the −16 LUFS target
was 5–7 dB too quiet, and that the reference pack peaked at 0.0 dB on every clip.

---

## Character packs: three ways to silently ship the wrong thing

All three were hit while building `archer_wot_sterling`, and each fails quietly —
you get a pack, it just isn't the one you wanted.

### The default candidate window throws catchphrases away
`CAND_MAX_S` is 2.2 s, tuned for terse battle callouts. But catchphrases are
routinely buried mid-utterance — *"...well, just keep at it. You're not my
supervisor."* — so that window discards **53–78 %** of them (`shitsnacks` 2 of 9
survive, `sploosh` 6 of 16). This is why phrase hits get their own
`PHRASE_WINDOW` (0.4–8.0 s) instead of the pool's, and why you then trim them by
hand on the board. If a catchphrase you know exists isn't showing up, check the
window before you suspect the transcript.

### Character-filter BEFORE the top-N cut, never after
`window_positions()` takes the character filter for a reason. Rank first and
filter after, and a lead character swamps the ranking — Archer is 42 % of all
attributed lines, so the global top 60 is nearly all him and a Pam pack comes
back almost empty. There's a test pinning this (`test_window_positions_*`).

### Neither the wiki nor raw corpus counts tell you who says a line
Wiki attributions frequently name the person being spoken *to* (the Archer wiki
credits "sploosh" to Lana). The obvious fix — believe the corpus — is worse:
counting raw hits credits Archer with "get some", "idiot", "burn" and "chet",
because he simply talks more than everyone else. Attribute by **rate**, hits
divided by that character's share of the corpus; that recovers Pam, Malory,
Cheryl and Cyril. `phrases.py probe` does this and prints "said more by X" when
its answer disagrees with the wiki's, so the disagreements stay visible.

Related: a scraped "running gag" is often a *thematic* gag ("Coconut butter",
"Punny names") rather than a quotable line. Those harmlessly score zero corpus
hits and drop out — but generic filler ("shut up", 206 hits) does **not**, and
needs the stoplist.

---

## Misc

- **torchaudio's alignment model ignores `HF_HOME`** — it caches to
  `~/.cache/torch/hub/`. Just know it's there (~360 MB).
- **Set `HF_HOME` before the first run.** Model weights are 10–15 GB; you want
  them on the drive you chose, not wherever the default lands. Moving them after
  is a chore.
- **Filenames drive provenance.** No parseable `SxxExx` → NULL season/episode →
  you find a perfect line and can't tell which episode it's from. Rename first.
- **A commit is forever.** Never let `env.sh` or any token into git history.
  `.gitignore` it before `git init`, and `git check-ignore env.sh` to confirm.
