# -*- coding: utf-8 -*-
"""
_template/package.py — OPTIONAL project code hook for Stage 6 (packaging).

Delete this file to use the engine's generic packaging (copy cleaned clips into
package/<pool>/ + manifest.json). Keep and edit it to produce a layout specific
to your target (a game soundbank, a sampler, a JSON pack, etc.).

If present, pipeline/06_package.py calls package(ctx) instead of its default.
Compose the shared helpers in pipeline/downstream.py rather than reinventing
clip handling. Two worked examples:
  - projects/archer_wot/package.py   — WoT: event-remap descriptor + .wotmod
  - projects/archer_wows/package.py  — WoWs: clones a reference mod.xml and
        routes each pool to its event's states via the pool's optional 7th-field
        filter (ctx["proj"].POOLS[i][6]); unmatched states are dropped.

ctx keys:
  proj         -> the resolved Project (proj.POOLS carries any 7th-field filters)
  package_dir  -> Path to write the deliverable into (you create/populate it)
  workdir      -> Path to work/<project>/ (clip files are relative to this)
  entries      -> [{pool_id, utterance_id, file, character, text, source}, ...]
  pools        -> {pool_id: {"display":..., "suggested_char":...}}
  pool_events  -> {pool_id: [game_event, ...]}
  downstream   -> the pipeline/downstream.py module (write_manifest, etc.)
"""

import shutil
from pathlib import Path


def package(ctx):
    pkg = ctx["package_dir"]
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)

    manifest_entries = []
    for e in ctx["entries"]:
        # EXAMPLE: flat layout, one folder per pool. Replace with your target's.
        dest_dir = pkg / e["pool_id"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(e["file"]).name
        src = ctx["workdir"] / e["file"]
        if src.exists():
            shutil.copy2(src, dest)
        manifest_entries.append({
            "pool_id": e["pool_id"], "file": str(dest.relative_to(pkg)),
            "character": e["character"], "text": e["text"], "source": e["source"],
        })

    ctx["downstream"].write_manifest(manifest_entries, ctx["pools"],
                                     ctx["pool_events"], pkg / "manifest.json")
