# Backlog

## Open: the WoWs pack is too quiet in-game (unresolved)
The `archer_wows` pack was built end-to-end and installed, and the lines are too
quiet to be usable under the game mix. **Not yet root-caused.** What's already
been ruled in or out, so it doesn't get re-derived:

- **Stage 5 output is NOT the problem.** Sampled 20 of the 282 shipped finals:
  every clip peaks at **0.0 dBFS**, RMS −9.8 to −14.5 (**median −11.8**). The
  game-VO target from `docs/gotchas.md` is peak ~0 / RMS ~−10. So the WAVs are
  on target within ~2 dB, and the level is lost *downstream of Stage 5*.
- **The encode CLI isn't attenuating.** WoWs used the headless
  `sound2wem`/WwiseConsole path (**not** the Wwise GUI used for WoT):
  `zSound2wem.cmd --channels:1 --audioformats:wav --conversion:"Vorbis Quality High"`,
  with `--volume` and `--extra` both blank — no gain change, no `loudnorm`.
- **`COMPRESS_VO`'s finite gain ceiling is real but is a different bug.** It
  can't lift a source peaking below ~−31 dBFS to target, and used to fail
  silently; Stage 5 now measures every compressed clip and warns
  (`VO_PEAK_FLOOR_DBFS`). None of the shipped clips actually hit that ceiling.

Prime suspect: **`sound2wem` embeds its own Wwise project**
(`sound2wem/wavtowemscript/`), and Wwise applies that project's Conversion
Settings + Actor-Mixer properties on every encode. Wwise's per-object *Loudness
Normalization* targets −23 LUFS, which would gut short callouts exactly this
way. Check `Conversion Settings/` and `Actor-Mixer Hierarchy/Default Work
Unit.wwu` for `EnableLoudnessNormalization`, make-up gain, and volume offsets.

Decisive test: build `ww2ogg` (+`revorb`), decode one shipped `.wem` from
`work/archer_wows/dist/Archer.zip`, and measure it against its source
`final/**/<uid>_*.wav`. Equal ⇒ the encode is innocent and it's a game-side
bus/priority issue; quieter ⇒ it's the Wwise project settings.

## Character packs — Sterling built, awaiting its Wwise bank
`archer_wot_sterling` is the pilot single-character pack and the proof that the
variant-pack path works end to end. Built 2026-08-17 through Stage 6:

- **110 clips across 16 of 17 pools.** `base_captured` was deliberately left
  unpicked — nothing in Archer's lines fit it — so `build_wotpack` omits the
  container and its one event remap, and the game keeps its stock crew line.
  That is the designed graceful outcome, not a gap to fill.
- **Level is at parity with the shipped ensemble pack** (+0.10 dB median peak,
  −0.10 dB median RMS; 110 clips vs the ensemble's 115 — measure from the
  `finals` table, not `final/**/*.wav`, since Stage 5 never deletes the clips of
  picks you dropped and the parent has 7 such strays on disk), both at
  `LOUDNORM_LUFS=-16` with
  `COMPRESS_VO` off. Deliberately *not* switched to `COMPRESS_VO` for this
  build: the ensemble pack shipped and works at these settings, and changing
  them here would have confounded this pack with the open WoWs loudness bug
  above. If Sterling also turns out quiet in-game, that is a second data point
  for that investigation — WoT (Wwise GUI) and WoWs (`sound2wem`) would then
  share a symptom across two different encode paths, which would exonerate
  `sound2wem` and point at Stage 5 after all.
- **The refactored emitter passed its first non-default identity.** This was the
  real risk in moving the WoT emitter into `pipeline/build_wotpack.py`: that
  `archer_wot`'s strings were baked in and only *looked* generic, since the
  byte-identical re-emit test could only ever re-run the original identity.
  Verified in the output: `quotemine_sterling.bnk`, 47 remaps all `vo_qms_`,
  zero un-prefixed `vo_qm_` leaks.

**Remaining:** the Wwise GUI pass (bank named `quotemine_sterling`, events
`vo_qms_*` — see `projects/archer_wot/WWISE.md`), then `build_wotmod.py`, then
an in-game check. The two WoT packs are **alternatives, not co-installable** —
both remap the same `vo_*` crew events, so whichever bank loads last wins.

**Next characters:** a sibling pack is `config.py` with `LEAD` changed and its
own `_PHRASES` (~15 lines), plus a 3-line `package.py` identity. Pam, Malory,
Cheryl and Cyril are the ones `probe`'s rate-based attribution actually
recovers. None has been built, so the filter-before-top-N fix is still untested
against a non-lead character — Archer is 42 % of the corpus and is the easy
case. Expect thinner pools and more semantic-fallback events for anyone else.

**Worth doing before the next four:** `phrases.py mine|probe` print to stdout
only. Sterling's `_PHRASES` was hand-transcribed from a terminal scroll that no
longer exists, so the evidence behind each phrase-to-pool assignment is gone.
Persisting probe output per project would make that reproducible.

## Forward-looking, not committed — distribution/UX polish, its own branch:
- **Unified `quotemine` CLI + `pyproject.toml`** — console entry points so stages
  run as `quotemine audition sample …` instead of `python pipeline/04_…`.
  Source/editable install with documented torch-first setup; NOT a PyPI
  `pip install` (the GPU/torch/pyannote deps won't resolve cleanly) and NOT a
  frozen binary (wrong for a CUDA ML pipeline). Highest-leverage win.
- **README quickstart** — six-stage end-to-end walkthrough that surfaces the top
  gotchas up front (torch-first, pyannote token, MAX_SPEAKERS).
- **`quotemine new <name>` scaffolder** — copy `projects/_template/` to start a
  new corpus.
- **Orchestration helper** (`quotemine run --project X`) — run the automated
  stages in order, stopping with a clear prompt at the human audition step.
