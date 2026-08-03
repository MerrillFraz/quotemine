#!/usr/bin/env python3
"""
build_wowsmod.py — assemble a zipped, Aslain-ready World of Warships voice mod
from the Stage 6 package plus the Wwise-encoded `.wem` files.  [CPU, no models]

Stage 6 (06_package.py + projects/<project>/package.py) emits, into
work/<project>/package/mod/:
  - mod.xml           the AudioModification manifest (names every .wem it uses)
  - <clips>.wem       one per clip — but still WAV bytes at that stage
The one thing it can't produce on Linux is real Wwise-encoded `.wem` audio;
that comes out of Wwise / WwiseConsole (the WoWs analog of WoT's `.bnk`). This
script joins the two:

    <MOD_FOLDER>.zip
      res_mods/banks/Mods/<MOD_FOLDER>/
        mod.xml                (copied from the package)
        <clips>.wem ...        (copied from --wems, the ENCODED files)

It verifies every .wem named in mod.xml is present in --wems, so a missing or
mis-encoded clip fails loudly here instead of as silence in-game. The zip's
internal tree drops straight into a WoWs install (or an Aslain submission).

Usage:
    python pipeline/build_wowsmod.py --project archer_wows \
        --wems "/mnt/c/…/WwiseProject/GeneratedSoundBanks/Windows"
    # optional: --out some/path/Archer.zip
"""

import argparse
import importlib.util
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project as project_mod  # noqa: E402
from project import load_project  # noqa: E402


def _mod_folder(name):
    """Read MOD_FOLDER from the project's package.py (fallback: project name)."""
    path = project_mod.PROJECTS_DIR / name / "package.py"
    if path.is_file():
        spec = importlib.util.spec_from_file_location(f"package_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "MOD_FOLDER", name)
    return name


def _wems_in_manifest(mod_xml):
    """Every distinct .wem filename referenced by mod.xml's <FilesList> entries."""
    root = ET.parse(mod_xml).getroot()
    return sorted({(f.findtext("Name") or "").strip()
                   for f in root.iter("File")
                   if (f.findtext("Name") or "").strip()})


def main():
    ap = argparse.ArgumentParser(description="Assemble a zipped WoWs voice mod from package + encoded .wem files.")
    # archer_wows, not the repo-wide archer_wot default: this builder is
    # WoWs-only, and pointing it at a WoT workdir fails with a misleading
    # "run Stage 6 first" instead of "wrong project".
    ap.add_argument("--project", default="archer_wows")
    ap.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    ap.add_argument("--wems", required=True,
                    help="Folder with the Wwise-encoded .wem files.")
    ap.add_argument("--out", default=None,
                    help="Output .zip path (default: work/<project>/dist/<MOD_FOLDER>.zip).")
    args = ap.parse_args()

    proj = load_project(args.project, args.workdir)
    mod_xml = proj.workdir / "package" / "mod" / "mod.xml"
    if not mod_xml.is_file():
        sys.exit(f"Missing {mod_xml} — run Stage 6 (06_package.py) first.")

    wems_dir = Path(args.wems)
    if not wems_dir.is_dir():
        sys.exit(f"--wems is not a directory: {wems_dir}")

    wanted = _wems_in_manifest(mod_xml)
    if not wanted:
        sys.exit(f"{mod_xml} references no .wem files — nothing to package.")

    # Resolve every referenced .wem; fail loudly (with the full missing list) if any absent.
    present = {p.name: p for p in wems_dir.glob("*.wem")}
    lower = {k.lower(): v for k, v in present.items()}
    resolved = {}
    missing = []
    for name in wanted:
        hit = present.get(name) or lower.get(name.lower())
        if hit:
            resolved[name] = hit
        else:
            missing.append(name)
    if missing:
        shown = "\n  ".join(missing[:20])
        more = f"\n  … and {len(missing) - 20} more" if len(missing) > 20 else ""
        sys.exit(f"{len(missing)} .wem file(s) named in mod.xml are missing from {wems_dir}:\n"
                 f"  {shown}{more}\n"
                 f"  Encode them in Wwise first (see package/convert_wem.txt).")

    folder = _mod_folder(args.project)
    out = Path(args.out) if args.out else (proj.workdir / "dist" / f"{folder}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    arc_root = f"res_mods/banks/Mods/{folder}"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(mod_xml, f"{arc_root}/mod.xml")
        for name, path in sorted(resolved.items()):
            z.write(path, f"{arc_root}/{name}")

    print(f"[wowsmod] {out}")
    print(f"  packed  : mod.xml + {len(resolved)} .wem into {arc_root}/")
    print(f"  install : unzip into World_of_Warships/bin/<build>/  "
          f"(gives res_mods/banks/Mods/{folder}/)")


if __name__ == "__main__":
    main()
