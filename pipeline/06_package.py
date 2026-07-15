#!/usr/bin/env python3
"""
06_package.py — Stage 6: assemble cleaned clips into a deliverable.  [CPU]

Generic default: copy the Stage 5 finals into work/<project>/package/<pool>/ and
write manifest.json (each pool -> its wired game events -> its clips).

If the project ships projects/<name>/package.py defining `package(ctx)`, that
hook runs instead, producing a target-specific layout (e.g. Wwise-oriented
container naming). This is the "projects may carry code" pattern — the hook
composes the shared helpers in pipeline/downstream.py.
"""

import argparse
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import project as project_mod
from project import load_project
import downstream


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    return db


def load_package_hook(name):
    """Return projects/<name>/package.py's `package` callable, or None."""
    path = project_mod.PROJECTS_DIR / name / "package.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"package_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, "package", None)


def collect_entries(db, proj):
    """One entry per cleaned final, with the metadata Stage 6 needs."""
    rows = db.execute(
        """SELECT f.pool_id, f.utterance_id, f.path AS file,
                  u.character, u.text, e.path AS source
           FROM finals f
           JOIN utterances u ON u.id = f.utterance_id
           JOIN episodes e   ON e.id = u.episode_id
           ORDER BY f.pool_id""").fetchall()
    return [dict(r) for r in rows]


def cmd_package(args, db):
    proj = args.proj
    entries = collect_entries(db, proj)
    if not entries:
        sys.exit("No finals. Run Stage 5 `clean` first.")

    pools = {p[0]: {"display": p[1], "suggested_char": p[2]} for p in proj.POOLS}
    pool_events = {p[0]: list(p[5]) for p in proj.POOLS}
    pkg = proj.workdir / "package"

    hook = load_package_hook(args.project)
    if hook:
        print(f"[package] using projects/{args.project}/package.py hook")
        hook({
            "proj": proj, "workdir": proj.workdir, "package_dir": pkg,
            "entries": entries, "pools": pools, "pool_events": pool_events,
            "downstream": downstream,
        })
        print(f"[package] done -> {pkg}")
        return

    # Generic default: copy finals into package/<pool>/ + manifest.json
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    manifest_entries = []
    for e in entries:
        src = proj.workdir / e["file"]
        dest_dir = pkg / e["pool_id"]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(e["file"]).name
        if src.exists():
            shutil.copy2(src, dest)
        manifest_entries.append({
            "pool_id": e["pool_id"], "file": str(dest.relative_to(pkg)),
            "character": e["character"], "text": e["text"], "source": e["source"],
        })
    out = downstream.write_manifest(manifest_entries, pools, pool_events,
                                    pkg / "manifest.json")
    print(f"[package] {out['total_clips']} clips across {len(pools)} pools -> {pkg}")
    print(f"  manifest: {pkg / 'manifest.json'}")


def main():
    p = argparse.ArgumentParser(description="Package cleaned clips (Stage 6).")
    p.add_argument("--project", default="archer_wot")
    p.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    args = p.parse_args()

    args.proj = load_project(args.project, args.workdir)
    db = connect(args.proj.workdir)
    cmd_package(args, db)
    db.close()


if __name__ == "__main__":
    main()
