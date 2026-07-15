#!/usr/bin/env python3
"""
project.py — the handoff between the generic pipeline and a specific project.

The three stage scripts (01_index, 02_identify, 03_match) contain no
show/film/game-specific knowledge. Everything particular to a corpus — how
filenames encode provenance, the character roster, era-bands, the event pools,
and every per-corpus tuning knob — lives in `projects/<name>/config.py`. Each
stage takes `--project <name>` and calls `load_project()` here to pull it in.

A project config module must expose:

    parse(filename)  -> (group_idx, item_idx)   ints, or (None, None)
    CHARACTERS       -> list[str]               the roster (required, non-empty)
    POOLS            -> list[tuple]             event pools (see any config.py)

and may optionally expose:

    label(group_idx, item_idx) -> str    human display; default "g<G>i<I>"
    BANDS  -> list[(name, lo, hi)] | None  era groups over group_idx;
                                           None -> one implicit band ("all")
    TUNING -> dict                         overrides merged over DEFAULT_TUNING

`group_idx`/`item_idx` are just orderable integers. A TV show maps them to
season/episode; a film to disc/scene; a game may leave both None and rely on
the filename as identity. Nothing here assumes television.
"""

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"

# Per-corpus knobs, with generic-sensible defaults. A project's TUNING dict is
# merged OVER these, so a config only restates what differs from the default.
DEFAULT_TUNING = {
    # Stage 1 — index
    "LANGUAGE": "en",           # pinned ASR language; skips detection
    "MIN_SPEAKERS": 2,
    "MAX_SPEAKERS": 10,         # set close to the real per-item speaker ceiling
    "MAX_WORD_GAP_S": 0.45,
    "MIN_UTTERANCE_S": 0.30,
    "MAX_UTTERANCE_S": 8.00,
    # Stage 2 — identify (sampling)
    "EPISODES_PER_BAND": 8,
    "CLUSTERS_PER_EPISODE": 8,
    "UTTS_PER_CLUSTER": 6,
    "TALK_FLOOR_S": 60.0,
    "UTT_MIN_S": 0.8,
    "UTT_MAX_S": 3.0,
    "UTT_MIN_WORDS": 3,
    "ISOLATION_PAD_S": 0.5,
    "MIN_SNR_DB": 8.0,
    # Stage 2 — identify (assignment)
    "CLUSTER_EMBED_UTTS": 12,
    "DEFAULT_THRESHOLD": 0.50,
    # Stage 3 — match
    "CAND_MIN_S": 0.4,
    "CAND_MAX_S": 2.2,
    "TOP_SEMANTIC": 60,
    "KW_BONUS": 0.08,
    # Stage 4 — audition
    "AUDITION_TOP_N": 25,       # candidates per pool put on the board
    "PREVIEW_PAD_S": 0.15,      # padding around preview clips
    # Stage 5 — clean
    "CLEAN_PAD_S": 0.10,        # padding kept on the final cut from source
    "LOUDNORM_LUFS": -16.0,     # integrated-loudness target
    "BANDPASS_HZ": None,        # (low, high) to band-limit, or None
    "FADE_MS": 15,              # head/tail fade on finals
}

# Any group_idx falls inside this when a project declares no bands.
_ALL_BAND = ("all", -10**9, 10**9)


class Project:
    """Resolved, validated project config with defaults applied."""

    def __init__(self, name, module, workdir):
        self.name = name
        self.workdir = workdir

        self.parse = module.parse
        self.CHARACTERS = list(module.CHARACTERS)
        self.POOLS = list(module.POOLS)

        bands = getattr(module, "BANDS", None)
        self.BANDS = list(bands) if bands else [_ALL_BAND]

        self._label = getattr(module, "label", None)

        self.TUNING = dict(DEFAULT_TUNING)
        self.TUNING.update(getattr(module, "TUNING", {}) or {})

    def label(self, group_idx, item_idx):
        if self._label:
            return self._label(group_idx, item_idx)
        g = "?" if group_idx is None else group_idx
        i = "?" if item_idx is None else item_idx
        return f"g{g}i{i}"

    def band_of(self, group_idx):
        if group_idx is None:
            return self.BANDS[0][0] if len(self.BANDS) == 1 else None
        for name, lo, hi in self.BANDS:
            if lo <= group_idx <= hi:
                return name
        return None

    def __getitem__(self, key):
        """Sugar so stages can read project['MAX_SPEAKERS']."""
        return self.TUNING[key]


def load_project(name, workdir=None):
    """Load projects/<name>/config.py, validate its contract, apply defaults."""
    cfg = PROJECTS_DIR / name / "config.py"
    if not cfg.is_file():
        avail = sorted(p.name for p in PROJECTS_DIR.iterdir()
                       if (p / "config.py").is_file()) if PROJECTS_DIR.is_dir() else []
        raise SystemExit(
            f"Unknown project '{name}': no {cfg}.\n"
            f"Available projects: {', '.join(avail) or '(none)'}\n"
            f"Copy projects/_template/ to start a new one."
        )

    spec = importlib.util.spec_from_file_location(f"project_{name}", cfg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    missing = [a for a in ("parse", "CHARACTERS", "POOLS") if not hasattr(module, a)]
    if missing:
        raise SystemExit(f"Project '{name}' config is missing: {', '.join(missing)}")
    if not callable(module.parse):
        raise SystemExit(f"Project '{name}': parse must be callable.")
    if not list(module.CHARACTERS):
        raise SystemExit(f"Project '{name}': CHARACTERS is empty — add your roster "
                         f"to {cfg} before running.")

    wd = Path(workdir) if workdir else (REPO_ROOT / "work" / name)
    return Project(name, module, wd)
