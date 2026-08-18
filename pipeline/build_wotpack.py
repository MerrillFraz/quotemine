# -*- coding: utf-8 -*-
"""
build_wotpack.py — emit a World of Tanks Wwise/.wotmod package layout.

The Stage 6 emitter shared by every WoT-targeted project. It was originally
inline in projects/archer_wot/package.py; it lives here because a corpus can
produce many WoT packs (an ensemble pack, then one per character) that differ
only in pack IDENTITY — bank name, mod id, display name — and not at all in
layout. A project's package.py is then a dozen lines: build an Identity and
call build().

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
new Wwise Event named `<prefix><pool_id>` that plays its `RC_<pool_id>`
container; audio_mods.xml points every one of that pool's original vo_* events
at it.

Because remapping is by event name, two packs that cover the same events are
ALTERNATIVES, not additions — installing a character pack alongside the ensemble
pack leaves which one wins undefined. Give each pack a distinct mod_id and bank
name so their files never collide, and ship one at a time.

Downstream, pipeline/build_wotmod.py takes the Wwise-generated `.bnk` plus the
audio_mods.xml + meta.xml emitted here and assembles the installable .wotmod.
"""

import csv
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Identity:
    """Everything that distinguishes one WoT pack from another.

    `bank_name` and `mod_id` must be unique per pack: the bank becomes a real
    filename inside the archive, and WoT keys installed mods on the id.
    """
    mod_id: str
    mod_name: str
    mod_description: str
    mod_version: str = "1.0.0"
    bank_name: str = "quotemine"        # -> <bank_name>.bnk in Wwise
    mod_event_prefix: str = "vo_qm_"    # each pool's new event: <prefix><pool_id>

    def mod_event(self, pool_id):
        return f"{self.mod_event_prefix}{pool_id}"


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


def _write_audio_mods_xml(ident, pool_ids, pool_events, out_path):
    """WoT event-remap descriptor: load our bank, redirect each original vo_*
    event to that pool's mod event. One <event> per (pool, game_event)."""
    root = ET.Element("audio_mods.xml")
    load = ET.SubElement(root, "loadBanks")
    bank = ET.SubElement(load, "bank")
    ET.SubElement(bank, "name").text = f"{ident.bank_name}.bnk"
    ET.SubElement(bank, "priority").text = "50"
    events = ET.SubElement(root, "events")
    n = 0
    for pid in pool_ids:
        mod_ev = ident.mod_event(pid)
        for game_event in pool_events.get(pid, []):
            ev = ET.SubElement(events, "event")
            ET.SubElement(ev, "name").text = game_event   # original WG event
            ET.SubElement(ev, "mod").text = mod_ev        # our replacement event
            n += 1
    _indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return n


def _write_meta_xml(ident, out_path):
    """The .wotmod manifest placed at the mod-archive root."""
    root = ET.Element("root")
    ET.SubElement(root, "id").text = ident.mod_id
    ET.SubElement(root, "version").text = ident.mod_version
    ET.SubElement(root, "name").text = ident.mod_name
    ET.SubElement(root, "description").text = ident.mod_description
    _indent(root)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)


def _write_wwise_events_txt(ident, project, pool_ids, pools, pool_events,
                            clips_by_pool, out_path):
    """Human checklist for the Wwise step."""
    title = f"Wwise build checklist — Quotemine / {project}"
    lines = [
        title,
        "=" * max(len(title), 52),
        "",
        f"SoundBank to create/generate: {ident.bank_name}.bnk",
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
        lines.append(f"    create event : {ident.mod_event(pid)}")
        lines.append(f"    stands in for {len(evs)} game event(s):")
        for ev in evs:
            lines.append(f"        - {ev}")
        lines.append("")
    Path(out_path).write_text("\n".join(lines))


def _write_readme(ident, pool_ids, total_clips, out_path):
    Path(out_path).write_text(
        f"{ident.mod_name}\n"
        f"{'=' * len(ident.mod_name)}\n\n"
        f"{ident.mod_description}\n\n"
        f"{total_clips} clips across {len(pool_ids)} battle events.\n\n"
        "Install: drop the .wotmod into\n"
        "  World_of_Tanks/mods/<game_version>/\n"
        "and launch. Remove the file to uninstall.\n\n"
        "This is an additive voiceover mod (it does not overwrite WG banks).\n"
    )


def build(ctx, ident):
    """Emit the full WoT package layout into ctx['package_dir'].

    ctx is Stage 6's hook context (see pipeline/06_package.py); `ident` is an
    Identity. Returns the number of clips written.
    """
    pkg = ctx["package_dir"]
    workdir = ctx["workdir"]
    entries = ctx["entries"]
    pools = ctx["pools"]
    pool_events = ctx["pool_events"]
    project = ctx["proj"].name

    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)

    manifest_entries = []
    csv_rows = []
    clips_by_pool = {}
    # Only include pools that actually have clips — remapping a game event to an
    # empty container is silence, which is worse than leaving the stock line.
    # Order follows `entries` (Stage 6 sorts by pool_id), not config order.
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
            "mod_event": ident.mod_event(pid),
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

    n_events = _write_audio_mods_xml(ident, pools_present, pool_events,
                                     pkg / "audio_mods.xml")
    _write_meta_xml(ident, pkg / "meta.xml")
    _write_wwise_events_txt(ident, project, pools_present, pools, pool_events,
                            clips_by_pool, pkg / "wwise_events.txt")
    _write_readme(ident, pools_present, len(csv_rows), pkg / "README.txt")

    print(f"[package:{project}] {len(csv_rows)} clips into "
          f"{len(pools_present)} RC_ containers")
    print(f"  audio_mods.xml: {n_events} event remap(s) across {len(pools_present)} pools")
    print(f"  emitted: manifest.json, wwise_import.csv, wwise_events.txt, "
          f"audio_mods.xml, meta.xml, README.txt")
    print(f"  next: create the {ident.bank_name}.bnk in Wwise (see wwise_events.txt), "
          f"then: python pipeline/build_wotmod.py --project {project} "
          f"--banks <GeneratedSoundBanks/WinHighRes>")
    return len(csv_rows)
