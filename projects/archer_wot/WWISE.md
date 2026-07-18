# archer_wot — Wwise → `.wotmod` build

The generic pipeline stops at `work/archer_wot/package/`. This doc covers the
WoT-specific tail: turning that package into a soundbank in Wwise and assembling
an installable `.wotmod`. It's a worked example of the "projects carry code"
seam — the same steps apply to any WoT voiceover project.

## How a WoT sound mod actually works (the key mental model)
WoT sound mods are **additive event-remaps, not soundbank overwrites**. You ship
your own `.bnk` plus an `audio_mods.xml` descriptor that tells the game: *load
this bank, and redirect original event `vo_X` → my event `vo_qm_X`*. So you
never touch WG's banks. Stage 6 generates `audio_mods.xml` for you from the
pool→events map, so every original `vo_*` event in each pool is already wired to
that pool's `vo_qm_<pool>` mod event. Your only job in Wwise is to create those
`vo_qm_<pool>` events and bake them into one bank.

## Prerequisites
- The WG modder kit (the `wot.wwise` GitHub project) cloned locally, and Wwise
  installed at **the version the kit pins** — opening the kit's `.wproj` forces
  it. A `.bnk` is version-stamped; a bank built by the wrong Wwise version won't
  load in-game. Don't second-guess the version the kit put you on.
- `work/archer_wot/package/` populated (run Stages 4–6). Open
  `package/wwise_events.txt` next to Wwise — it's the authoritative per-pool
  checklist (container name, event name, source events).

## Steps

### 1. Open the WG project
Open the kit's `wwise_project/<lang>/*.wproj`. Accept the project migration
prompt if it appears (expected when your Wwise is newer than the authored file).

### 2. Make your own writable Work Unit  ⚠️
The WG project ships parts of its **Actor-Mixer Hierarchy as read-only work
units** — if you build your containers there, the Property Editor is greyed out
and you can't edit playlist settings. **Create your own:** right-click the
**Actor-Mixer Hierarchy** root → **New Child → Work Unit**, name it `Quotemine`.
Build everything under it. (Symptom of the trap: a plain sound is editable but a
container is fully greyed — the container landed in a read-only work unit.)

### 3. Import the clips
`Project → Import Audio Files` (**Shift+I**) → add the 17 `RC_*` folders from
`package/` (reach WSL files at `\\wsl$\<distro>\home\…\work\archer_wot\package`).
Leave **Import as: Sound SFX** — *not* Sound Voice. Sound Voice is Wwise's
localization type; its media generates into per-language banks that only load
when the player's voiceover language matches. SFX is language-agnostic and always
plays — correct for a single English voice set. (The event remap is object-type
agnostic, so SFX doesn't compromise the hookup.)

### 4. One Random Container per pool
Select a folder's sounds → right-click → **New Parent → Random Container**, name
it `RC_<pool>`. **Multi-select all 17 containers** and set these once (Wwise
multi-edits the selection):
- **Play Type: Random** (locked on a dedicated Random Container — fine)
- **Random Type: Shuffle** — plays every line once before repeating (best variety
  for a small pool). With **Scope: Game object** (default), the shuffle bag
  persists across triggers per tank, so callouts keep drawing new lines.
- **Play Mode: Step** — one clip per event trigger.
- **Avoid repeating last N** = 3 (optional; at each shuffle refill it excludes the
  last N).
- **"Always reset playlist"** is a *Sequence*-container control — greyed/locked
  here and irrelevant to Random+Shuffle. Ignore it.

### 5. One Event per pool
Right-click each `RC_<pool>` → **New Event → Play**, then rename the event to
exactly the `vo_qm_<pool>` name in `wwise_events.txt`. The name **must** match —
it's the string `audio_mods.xml` redirects the game's events to.

### 6. Build & generate the SoundBank
SoundBank layout (**F7**) → new SoundBank named exactly **`quotemine`** (→
`quotemine.bnk`, matching what `audio_mods.xml` loads). Drag all 17 `vo_qm_*`
events in (Events + Structures + Media checked). Select the **WinHighRes**
platform and **Generate**.
- Output lands in `wwise_project/<lang>/GeneratedSoundBanks/WinHighRes/`.
- **Generation errors are expected and safe to ignore** as long as
  `quotemine.bnk` is written: the `MissingPlugin` errors (Wwise Convolution
  Reverb, Resonance Audio) all belong to WG's **`Init` bank**, which you don't
  ship — your clips route to Master with no effects. `PostBuild.cmd not found`
  is a WG automation hook you don't have. An Academic/Non-Commercial license is
  fine for a free mod.

### 7. Assemble the `.wotmod`
Back in the repo:
```
python pipeline/build_wotmod.py --project archer_wot \
  --banks "/mnt/c/…/wot.wwise/wwise_project/<lang>/GeneratedSoundBanks/WinHighRes"
```
→ `work/archer_wot/dist/Quotemine_Archer_Crew_Voices.wotmod` (only
`quotemine.bnk` + `audio_mods.xml` + `meta.xml`; **not** WG's Init bank).

### 8. Install & test
Copy the `.wotmod` into `<WoT install>/mods/<current_client_version>/` — the
version folder that matches `version.xml`, confirmed by `paths.xml`'s
`./mods/<ver>` line (a *previous* patch's folder won't load). Enable crew
voiceover in Settings and use a **standard** national crew voice (a special
voiceover can override the base `vo_*` events). Quickest confirmation: the
`battle_start` line fires at the countdown. Then hits/kills in a Co-op battle.

Note: only one mod can own the `vo_*` crew events at a time — don't run another
crew-voice mod (e.g. an Aslain voiceover option) alongside this one.

### 9. Submit to Aslain
Use `package/README.txt` as the description; submit the `.wotmod` + a
screenshot/clip via Aslain's site/forum. For immediate personal use, his
`Aslain_Modpack/Custom_mods/` folder is a local-include dropzone.

## Updating clips (re-tune or drop) — the structure is already built

Re-running Stages 4–6 (after tuning lead-in/out or dropping a clip) rebuilds
`package/`, but your `.wproj` already has the work unit, `RC_*` containers,
events, and the `quotemine` bank — you are **not** rebuilding those. Two things
Wwise will NOT do for you:

**1. Refresh changed audio — Add FILES into the container, never Add Folders.**
The clips live *inside* Random Containers. `Import Audio Files` (Shift+I) with
**Add Folders** always lands audio at the *folder* level: it drops the new files
as loose sounds **beside** the container (so the container keeps the OLD audio and
nothing actually updates), or — if the container sits at the top level — creates a
duplicate `RC_<pool>_01`. "Replace on collision" does not save you; it only
matches when the path lines up, which folder-import breaks. Instead: select the
target **Random Container** (the dice icon) as the destination, use **Add Files**,
pick that pool's WAVs from `package/RC_<pool>/`, import as **Sound SFX**.
Same-named children get their sources replaced in place — no loose sounds, no
`_01`. One pass per changed pool; verify each container's child count against
`wwise_events.txt`.

**2. Remove dropped clips — Wwise won't.** A clip you un-kept in Stage 4 is gone
from `package/`, but its Sound SFX object still sits in its container from the
first build and **will still ship** unless you delete it (Project Explorer search
box → type the `<utt>_<char>` name → Delete). A Random/Shuffle container just
plays its remaining members afterward — no other edit needed. (Watch out: if the
folder you import from still has the dropped file on disk, Add Files will re-add
it — delete it again.)

Then **Generate** as in step 6 and confirm the bank's sound count equals your
pick total; a wrong count means an orphan survived or a re-import didn't land.

## Optional polish
The `RC_` containers inherit **Output Bus = Master Audio Bus**, which plays at
full volume ignoring the in-game voice slider. If the lines come in too hot,
route them to WG's voiceover bus instead so they respect the slider and duck
like normal crew chatter.
