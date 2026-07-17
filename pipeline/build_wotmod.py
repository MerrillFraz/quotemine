#!/usr/bin/env python3
"""
build_wotmod.py — assemble an installable .wotmod from the Stage 6 package plus
the Wwise-generated soundbank(s).  [CPU, no models]

Stage 6 (06_package.py + a project package.py) emits, into work/<project>/package/:
  - meta.xml         the mod manifest (archive root)
  - audio_mods.xml   the event-remap descriptor (goes in res/audioww/)
The one thing it can't produce on Linux is the compiled `.bnk` — that comes out
of Wwise on Windows (GeneratedSoundBanks/WinHighRes/). This script joins the two:

    <ModName>.wotmod   (a ZIP, STORED / no compression — WoT requirement)
      meta.xml
      res/audioww/
        <bank>.bnk ...        (copied from --banks)
        audio_mods.xml        (copied from the package)

Usage:
    python pipeline/build_wotmod.py --project archer_wot \
        --banks "/mnt/c/Users/merri/git/wot.wwise/wwise_project/ru/GeneratedSoundBanks/WinHighRes"
    # optional: --out some/path/MyPack.wotmod

It verifies that every bank named in audio_mods.xml is present in --banks, so a
missing/renamed bank fails loudly here instead of as silent in-game.
"""

import argparse
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from project import load_project  # noqa: E402

# WoT reads sound mods from res/audioww/ inside the archive.
AUDIOWW = "res/audioww"


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "mod").strip("_") or "mod"


def _banks_in_descriptor(audio_mods_xml):
    """Bank filenames the descriptor says to load, e.g. ['quotemine.bnk']."""
    root = ET.parse(audio_mods_xml).getroot()
    return [b.findtext("name", "").strip()
            for b in root.findall("./loadBanks/bank")
            if b.findtext("name", "").strip()]


def _mod_name(meta_xml):
    root = ET.parse(meta_xml).getroot()
    return root.findtext("name") or root.findtext("id") or "mod"


def main():
    ap = argparse.ArgumentParser(description="Assemble a .wotmod from package + banks.")
    ap.add_argument("--project", default="archer_wot")
    ap.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    ap.add_argument("--banks", required=True,
                    help="Folder with the Wwise-generated .bnk files (WinHighRes).")
    ap.add_argument("--out", default=None,
                    help="Output .wotmod path (default: work/<project>/dist/<ModName>.wotmod).")
    args = ap.parse_args()

    proj = load_project(args.project, args.workdir)
    pkg = proj.workdir / "package"
    meta = pkg / "meta.xml"
    descriptor = pkg / "audio_mods.xml"
    for p in (meta, descriptor):
        if not p.is_file():
            sys.exit(f"Missing {p.name} in {pkg} — run Stage 6 (06_package.py) first.")

    banks_dir = Path(args.banks)
    if not banks_dir.is_dir():
        sys.exit(f"--banks is not a directory: {banks_dir}")

    # Resolve every bank the descriptor references; fail loudly if any is absent.
    wanted = _banks_in_descriptor(descriptor)
    if not wanted:
        sys.exit(f"{descriptor} lists no banks under <loadBanks> — nothing to package.")
    resolved = []
    for name in wanted:
        hit = banks_dir / name
        if not hit.is_file():
            # tolerate case differences on case-sensitive filesystems
            alt = next((f for f in banks_dir.glob("*.bnk")
                        if f.name.lower() == name.lower()), None)
            hit = alt
        if not hit or not hit.is_file():
            have = ", ".join(sorted(f.name for f in banks_dir.glob("*.bnk"))) or "(none)"
            sys.exit(f"Bank '{name}' from audio_mods.xml not found in {banks_dir}.\n"
                     f"  .bnk files present: {have}\n"
                     f"  Generate the SoundBank in Wwise first (see package/wwise_events.txt).")
        resolved.append(hit)

    out = Path(args.out) if args.out else (proj.workdir / "dist" / f"{_slug(_mod_name(meta))}.wotmod")
    out.parent.mkdir(parents=True, exist_ok=True)

    # Stage the archive tree, then zip it STORED (WoT requires no compression).
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / AUDIOWW).mkdir(parents=True)
        shutil.copy2(meta, tmp / "meta.xml")
        shutil.copy2(descriptor, tmp / AUDIOWW / "audio_mods.xml")
        for bnk in resolved:
            shutil.copy2(bnk, tmp / AUDIOWW / bnk.name)

        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
            for f in sorted(tmp.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(tmp).as_posix())

    print(f"[wotmod] {out}")
    print(f"  banks   : {', '.join(b.name for b in resolved)}")
    print(f"  install : copy into World_of_Tanks/mods/<game_version>/")


if __name__ == "__main__":
    main()
