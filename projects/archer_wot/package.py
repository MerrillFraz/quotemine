# -*- coding: utf-8 -*-
"""
archer_wot/package.py — target-specific packaging for the WoT voice pack.

This is the optional project code hook (see pipeline/06_package.py). When
present, Stage 6 calls package(ctx) instead of its generic default. It composes
the shared helpers in pipeline/downstream.py rather than reinventing them.

Layout produced (Wwise-oriented): each pool becomes one Random Container folder
`RC_<pool_id>/` holding its cleaned clips, plus:
  - manifest.json  — the generic pool -> game-events -> clips map
  - wwise_import.csv — one row per clip: container, source event(s), file
So you can drag each RC_ folder into a Wwise Random Container wired to that
pool's vo_* events.
"""

import csv
import shutil
from pathlib import Path


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
    for e in entries:
        pid = e["pool_id"]
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
            "game_events": " ".join(pool_events.get(pid, [])),
            "file": rel,
            "character": e["character"],
        })

    ctx["downstream"].write_manifest(manifest_entries, pools, pool_events,
                                     pkg / "manifest.json")

    with open(pkg / "wwise_import.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["container", "game_events", "file", "character"])
        w.writeheader()
        w.writerows(csv_rows)

    print(f"[package:archer_wot] {len(csv_rows)} clips into "
          f"{len(set(r['container'] for r in csv_rows))} RC_ containers")
