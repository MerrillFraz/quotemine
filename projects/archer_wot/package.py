# -*- coding: utf-8 -*-
"""
archer_wot/package.py — target-specific packaging for the WoT voice pack.

This is the optional project code hook (see pipeline/06_package.py). When
present, Stage 6 calls package(ctx) instead of its generic default. It composes
the shared helpers in pipeline/downstream.py rather than reinventing them.

Layout produced (Wwise-oriented): each pool becomes one Random Container folder
`RC_<pool_id>/` holding its cleaned clips, plus everything the Wwise + .wotmod
build needs:
  - manifest.json      — the generic pool -> game-events -> clips map
  - wwise_import.csv    — one row per clip: container, source event(s), file
  - wwise_events.txt    — the human checklist for the Wwise step: for each pool,
                          the Random Container, the mod Event to create, and the
                          source vo_* events it stands in for
  - audio_mods.xml      — WoT's event-remap descriptor: load our bank, and
                          redirect every original vo_* event to our mod event.
                          Generated straight from pool_events (no hand-editing).
  - meta.xml            — the .wotmod manifest (id/version/name/description)
  - README.txt          — short pack description + install note (for submission)

WoT sound mods are ADDITIVE: the game loads our bank alongside WG's and remaps
events (see docs/audio_mods.xml in the WG modder kit). So each pool needs one
new Wwise Event named `vo_qm_<pool_id>` that plays its `RC_<pool_id>` container;
audio_mods.xml points every one of that pool's original vo_* events at it.

Downstream, pipeline/build_wotmod.py takes the Wwise-generated `.bnk` plus the
audio_mods.xml + meta.xml emitted here and assembles the installable .wotmod.
"""

import csv
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

# --- pack identity / conventions -------------------------------------------
BANK_NAME = "quotemine"                 # -> quotemine.bnk (name the SoundBank this in Wwise)
MOD_EVENT_PREFIX = "vo_qm_"             # each pool's new Wwise event: vo_qm_<pool_id>
MOD_ID = "com.merrillfraz.quotemine.archer"
MOD_VERSION = "1.1.0"          # 1.1: retuned lead-in/out on ~60 lines, dropped 2
MOD_NAME = "Quotemine — Archer Crew Voices"
MOD_DESCRIPTION = (
    "Crew voiceover pack: lines from the animated series Archer, matched to "
    "World of Tanks standard-battle events. Built with Quotemine."
)


def _mod_event(pool_id):
    return f"{MOD_EVENT_PREFIX}{pool_id}"


def _indent(elem, level=0):
    """Pretty-print ElementTree in place (stdlib has no indent pre-3.9)."""
    pad = "\n" + "    " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "    "
        for child in elem:
            _indent(child, level + 1)
            if not (child.tail or "").strip():
                child.tail = pad + "    "
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = pad
    elif level and not (elem.tail or "").strip():
        elem.tail = pad


def _write_audio_mods_xml(pool_ids, pool_events, out_path):
    """WoT event-remap descriptor: load our bank, redirect each original vo_*
    event to that pool's mod event. One <event> per (pool, game_event)."""
    root = ET.Element("audio_mods.xml")
    load = ET.SubElement(root, "loadBanks")
    bank = ET.SubElement(load, "bank")
    ET.SubElement(bank, "name").text = f"{BANK_NAME}.bnk"
    ET.SubElement(bank, "priority").text = "50"
    events = ET.SubElement(root, "events")
    n = 0
    for pid in pool_ids:
        mod_ev = _mod_event(pid)
        for game_event in pool_events.get(pid, []):
            ev = ET.SubElement(events, "event")
            ET.SubElement(ev, "name").text = game_event   # original WG event
            ET.SubElement(ev, "mod").text = mod_ev        # our replacement event
            n += 1
    _indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return n


def _write_meta_xml(out_path):
    """The .wotmod manifest placed at the mod-archive root."""
    root = ET.Element("root")
    ET.SubElement(root, "id").text = MOD_ID
    ET.SubElement(root, "version").text = MOD_VERSION
    ET.SubElement(root, "name").text = MOD_NAME
    ET.SubElement(root, "description").text = MOD_DESCRIPTION
    _indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)


def _write_wwise_events_txt(pool_ids, pools, pool_events, clips_by_pool, out_path):
    """Human checklist for the Wwise step."""
    lines = [
        "Wwise build checklist — Quotemine / archer_wot",
        "=" * 52,
        "",
        f"SoundBank to create/generate: {BANK_NAME}.bnk",
        "",
        "For each pool below: import the clips from its RC_<pool>/ folder into a",
        "Random Container, then create a Play Event with the exact name shown and",
        "add it to the SoundBank. audio_mods.xml already wires the source events.",
        "",
    ]
    for pid in pool_ids:
        disp = pools.get(pid, {}).get("display", pid)
        evs = pool_events.get(pid, [])
        lines.append(f"[{pid}]  {disp}")
        lines.append(f"    clips folder : RC_{pid}/  ({clips_by_pool.get(pid, 0)} clips)")
        lines.append(f"    create event : {_mod_event(pid)}")
        lines.append(f"    stands in for {len(evs)} game event(s):")
        for ev in evs:
            lines.append(f"        - {ev}")
        lines.append("")
    Path(out_path).write_text("\n".join(lines))


def _write_readme(pool_ids, total_clips, out_path):
    Path(out_path).write_text(
        f"{MOD_NAME}\n"
        f"{'=' * len(MOD_NAME)}\n\n"
        f"{MOD_DESCRIPTION}\n\n"
        f"{total_clips} clips across {len(pool_ids)} battle events.\n\n"
        "Install: drop the .wotmod into\n"
        "  World_of_Tanks/mods/<game_version>/\n"
        "and launch. Remove the file to uninstall.\n\n"
        "This is an additive voiceover mod (it does not overwrite WG banks).\n"
    )


def package(ctx):
    pkg = ctx["package_dir"]
    workdir = ctx["workdir"]
    entries = ctx["entries"]
    pools = ctx["pools"]
    pool_events = ctx["pool_events"]

    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)

    manifest_entries = []
    csv_rows = []
    clips_by_pool = {}
    # Preserve config pool order for the emitted files; only include pools that
    # actually have clips (remapping an event to an empty container = silence).
    pools_present = []
    for e in entries:
        pid = e["pool_id"]
        if pid not in clips_by_pool:
            pools_present.append(pid)
        clips_by_pool[pid] = clips_by_pool.get(pid, 0) + 1

        container = f"RC_{pid}"
        dest_dir = pkg / container
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(e["file"]).name
        src = workdir / e["file"]
        if src.exists():
            shutil.copy2(src, dest)
        rel = str(dest.relative_to(pkg))
        manifest_entries.append({
            "pool_id": pid, "file": rel, "character": e["character"],
            "text": e["text"], "source": e["source"],
        })
        csv_rows.append({
            "container": container,
            "mod_event": _mod_event(pid),
            "game_events": " ".join(pool_events.get(pid, [])),
            "file": rel,
            "character": e["character"],
        })

    ctx["downstream"].write_manifest(manifest_entries, pools, pool_events,
                                     pkg / "manifest.json")

    with open(pkg / "wwise_import.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["container", "mod_event", "game_events",
                                          "file", "character"])
        w.writeheader()
        w.writerows(csv_rows)

    n_events = _write_audio_mods_xml(pools_present, pool_events, pkg / "audio_mods.xml")
    _write_meta_xml(pkg / "meta.xml")
    _write_wwise_events_txt(pools_present, pools, pool_events, clips_by_pool,
                            pkg / "wwise_events.txt")
    _write_readme(pools_present, len(csv_rows), pkg / "README.txt")

    print(f"[package:archer_wot] {len(csv_rows)} clips into "
          f"{len(pools_present)} RC_ containers")
    print(f"  audio_mods.xml: {n_events} event remap(s) across {len(pools_present)} pools")
    print(f"  emitted: manifest.json, wwise_import.csv, wwise_events.txt, "
          f"audio_mods.xml, meta.xml, README.txt")
    print(f"  next: create the {BANK_NAME}.bnk in Wwise (see wwise_events.txt), "
          f"then: python pipeline/build_wotmod.py --project {ctx['proj'].name} "
          f"--banks <GeneratedSoundBanks/WinHighRes>")
