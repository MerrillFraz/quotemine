# -*- coding: utf-8 -*-
"""
archer_wows/package.py — target-specific packaging for the WoWs voice pack.

Optional Stage 6 hook (see pipeline/06_package.py): when present, Stage 6 calls
package(ctx) instead of its generic default. Composes pipeline/downstream.py
helpers rather than reinventing them.

World of Warships loads a voice mod as a folder of loose `.wem` files plus a
`mod.xml` (the `<AudioModification.xml>` schema) that redirects game voiceover
events to those files. Unlike WoT there is no `.wotmod`, no `audio_mods.xml`
event-remap, and no compiled `.bnk` — the mod ships its own audio.

Layout produced into work/archer_wows/package/:
  - mod/                 the installable mod folder (-> res_mods/banks/Mods/Archer)
      <clips>.wem        one file per picked clip, named <pool>__<uid>.wem.
                         NOTE: these are still WAV bytes here; the WAV->.wem
                         encode is an external Wwise step (see convert_wem.txt).
      mod.xml            the AudioModification manifest (see below)
  - manifest.json        the generic pool -> game-events -> clips map (internal)
  - convert_wem.txt      the WAV->.wem encode checklist
  - README.txt           pack description + install note

mod.xml is built by cloning the exact per-event skeleton from the vendored
reference_mod.xml (real WoWs event/state structure extracted from a shipping
voicepack) and filling every one of an event's <FilesList> slots with the
matching pool's clips — so a random Archer line fires for that event in any
game state. Events with no clips (empty pools) are dropped, since a filesless
container would play nothing.

Downstream, pipeline/build_wowsmod.py takes the Wwise-encoded `.wem` files plus
this mod.xml and assembles the zipped, Aslain-ready mod folder.
"""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

# --- pack identity / conventions -------------------------------------------
MOD_FOLDER = "Archer"          # -> res_mods/banks/Mods/Archer
MOD_NAME = "Quotemine — Archer Commander Voices"
MOD_VERSION = "1.0.0"
MOD_DESCRIPTION = (
    "Commander voiceover pack: lines from the animated series Archer, matched "
    "to World of Warships battle events. Built with Quotemine."
)

REFERENCE_MOD_XML = Path(__file__).with_name("reference_mod.xml")


def _clip_base(pool_id, utterance_id):
    """A unique, filesystem-safe basename for a clip (no extension). We stage the
    source as <base>.wav and mod.xml references the encoded <base>.wem."""
    uid = re.sub(r"[^A-Za-z0-9]+", "_", str(utterance_id)).strip("_")
    return f"{pool_id}__{uid}"


def _path_matches(path, filt):
    """A reference <Path> matches a pool's routing filter when every
    (state_var -> allowed_values) pair in the filter is satisfied by the path's
    <StateList>. A None/empty filter matches every path — i.e. blanket-fill the
    whole event (no state segregation). The matcher is generic: it knows nothing
    about which state variables exist; those names live entirely in the project's
    POOLS filters (e.g. VO_Torpedo_Location, VO_Plane_Status)."""
    if not filt:
        return True
    states = {s.findtext("Name"): s.findtext("Value") for s in path.findall(".//State")}
    return all(states.get(var) in allowed for var, allowed in filt.items())


def _reference_event_names():
    """Every <ExternalEvent> name the reference schema knows about."""
    ref_am = ET.parse(REFERENCE_MOD_XML).getroot().find("AudioModification")
    return {ev.findtext("Name", "").strip() for ev in ref_am.findall("ExternalEvent")}


def _why_stranded(pool_id, pool_events, known_events):
    """Best explanation for a pool whose clips reached no <Path>. Pure;
    unit-tested. The three causes need different fixes, so name which one."""
    evs = pool_events.get(pool_id) or []
    if not evs:
        return "no game_event wired in POOLS"
    missing = [e for e in evs if e not in known_events]
    if missing:
        return "game_event not in the reference schema: " + ", ".join(sorted(missing))
    return "state filter matched no <Path> of " + ", ".join(evs)


def _build_mod_xml(event_routes, out_path):
    """Clone the reference skeleton; for each covered event keep only the <Path>
    blocks some route matches, filling each with that route's clips. Paths no
    route matches are dropped, so the game falls back to its default voice there.

    event_routes: {game_event: [(pool_id, state_filter, [wem_basename, ...]), ...]}
    Returns (events_written, filelist_slots_filled, routed_pool_ids).

    Routes are tried MOST SPECIFIC FIRST (by number of constrained state
    variables), not in the order they were built. Route order otherwise comes
    from Stage 6's row order, so an unfiltered pool — which matches every path —
    would claim a whole event purely because one of its clips happened to be
    inserted first. `_template/config.py` advertises exactly that blanket-pool
    pattern next to the state-split one, so the collision is reachable.
    """
    ref_am = ET.parse(REFERENCE_MOD_XML).getroot().find("AudioModification")

    root = ET.Element("AudioModification.xml")
    am = ET.SubElement(root, "AudioModification")
    ET.SubElement(am, "Name").text = MOD_NAME

    slots = 0
    written = 0
    routed = set()
    for ev in ref_am.findall("ExternalEvent"):
        name = ev.findtext("Name", "").strip()
        routes = event_routes.get(name)
        if not routes:
            continue  # event not covered by any pool that produced clips
        # Specific beats blanket; ties keep their original relative order.
        routes = sorted(routes, key=lambda r: -len(r[1] or {}))
        container = ev.find("Container")
        kept = 0
        for path in container.findall("Path"):   # snapshot list — safe to remove
            fl = path.find("FilesList")
            match = None
            if fl is not None:
                match = next(((pid, wems) for (pid, filt, wems) in routes
                              if _path_matches(path, filt)), None)
            if not match:
                container.remove(path)   # unmatched -> omit -> in-game default voice
                continue
            pid, wems = match
            for child in list(fl):
                fl.remove(child)
            fl.text = None
            for w in wems:
                f = ET.SubElement(fl, "File")
                ET.SubElement(f, "Name").text = w
            routed.add(pid)
            slots += 1
            kept += 1
        if kept:
            am.append(ev)
            written += 1

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return written, slots, routed


def _write_convert_txt(clip_count, out_path):
    # NOTE the parenthesised "=" * 54: adjacent string literals concatenate at
    # parse time and bind TIGHTER than *, so writing `"title\n" "=" * 54` makes
    # ("title\n=") * 54 — 54 copies of the title. That shipped.
    Path(out_path).write_text(
        "WAV -> .wem encode checklist — Quotemine / archer_wows\n"
        + ("=" * 54) + "\n\n"
        f"mod/ holds {clip_count} real .wav clips. WoWs needs Wwise-Vorbis .wem with\n"
        "the SAME basenames (mod.xml matches files by name). Encode headlessly with\n"
        "WwiseConsole via sound2wem (github.com/EternalLeo/sound2wem) — it makes its\n"
        "own Wwise project, batches a folder, and keeps filenames. From its dir:\n\n"
        '  zSound2wem.cmd --out:"<enc>" --channels:1 --audioformats:wav \\\n'
        '      "--conversion:Vorbis Quality High" "<...>\\work\\archer_wows\\package\\mod"\n\n'
        "-> <enc> now has <base>.wem for every <base>.wav. Then:\n\n"
        "  python pipeline/build_wowsmod.py --project archer_wows --wems <enc>\n\n"
        "which verifies every .wem named in mod.xml is present and zips the mod.\n"
    )


def _write_readme(clip_count, event_count, out_path):
    Path(out_path).write_text(
        f"{MOD_NAME}\n"
        f"{'=' * len(MOD_NAME)}\n\n"
        f"{MOD_DESCRIPTION}\n\n"
        f"{clip_count} clips across {event_count} battle events.\n\n"
        "Install: copy the 'Archer' folder into\n"
        "  World_of_Warships/bin/<build>/res_mods/banks/Mods/\n"
        "and pick it in the game's audio settings. Delete the folder to uninstall.\n"
    )


def package(ctx):
    pkg = ctx["package_dir"]
    workdir = ctx["workdir"]
    entries = ctx["entries"]
    pools = ctx["pools"]
    pool_events = ctx["pool_events"]

    if pkg.exists():
        shutil.rmtree(pkg)
    mod_dir = pkg / "mod"
    mod_dir.mkdir(parents=True)

    manifest_entries = []
    wems_by_pool = {}
    for e in entries:
        pid = e["pool_id"]
        base = _clip_base(pid, e["utterance_id"])
        wav, wem = f"{base}.wav", f"{base}.wem"
        src = workdir / e["file"]
        if src.exists():
            shutil.copy2(src, mod_dir / wav)   # real WAV, staged for the .wem encode
        wems_by_pool.setdefault(pid, []).append(wem)   # mod.xml names the encoded .wem
        manifest_entries.append({
            "pool_id": pid, "file": f"mod/{wav}", "character": e["character"],
            "text": e["text"], "source": e["source"],
        })

    # Each pool may carry an optional routing filter (the opaque 7th POOLS field)
    # that restricts it to specific state-paths of its event; None -> whole event.
    pool_filter = {p[0]: (p[6] if len(p) > 6 else None) for p in ctx["proj"].POOLS}

    # For each game event, the routes contributed by every pool that wires to it
    # and produced clips: (pool_id, its state filter, its wems). At build time each
    # of the event's <Path> blocks is matched to the first route whose filter fits.
    event_routes = {}
    for pid, wems in wems_by_pool.items():
        for game_event in pool_events.get(pid, []):
            event_routes.setdefault(game_event, []).append((pid, pool_filter.get(pid), wems))

    n_events, n_slots, routed = _build_mod_xml(event_routes, mod_dir / "mod.xml")

    ctx["downstream"].write_manifest(manifest_entries, pools, pool_events,
                                     pkg / "manifest.json")
    _write_convert_txt(len(manifest_entries), pkg / "convert_wem.txt")
    _write_readme(len(manifest_entries), n_events, pkg / "README.txt")

    print(f"[package:archer_wows] {len(manifest_entries)} clips -> mod/ "
          f"({len(wems_by_pool)} pools)")
    print(f"  mod.xml: {n_events} events wired, {n_slots} <FilesList> slots filled")

    # A pool whose clips route nowhere — a typo'd game_event, or a state value
    # absent from the reference — is otherwise dropped in silence: its WAVs are
    # still staged and still get encoded, and build_wowsmod only checks
    # mod.xml -> wem, never wem -> mod.xml. In-game it looks identical to an
    # event you deliberately left uncovered, so say it out loud here.
    known = _reference_event_names()
    stranded = sorted(set(wems_by_pool) - routed)
    if stranded:
        print(f"  WARNING: {len(stranded)} pool(s) produced clips that route to no "
              f"<Path> and will NOT be heard:")
        for pid in stranded:
            print(f"           {pid}: {_why_stranded(pid, pool_events, known)}")
    unknown = sorted({e for evs in pool_events.values() for e in evs} - known)
    if unknown:
        print(f"  WARNING: {len(unknown)} wired game_event(s) absent from "
              f"{REFERENCE_MOD_XML.name}: {', '.join(unknown)}")
    print(f"  emitted: mod/mod.xml, manifest.json, convert_wem.txt, README.txt")
    print(f"  next: encode mod/*.wav -> .wem (see convert_wem.txt), then: "
          f"python pipeline/build_wowsmod.py --project {ctx['proj'].name} "
          f"--wems <encoded_dir>")
