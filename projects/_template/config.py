# -*- coding: utf-8 -*-
"""
_template — copy this directory to projects/<your_name>/ and fill it in.

    cp -r projects/_template projects/my_show
    # edit projects/my_show/config.py, then:
    python pipeline/01_index.py --project my_show scan /path/to/videos

A project supplies everything specific to your source and target. The three
stage scripts are generic and read only what's below. See pipeline/project.py
for the full contract and the per-knob DEFAULT_TUNING values you inherit.

`group_idx` / `item_idx` are just orderable integers used for sorting, banding,
and provenance. A TV show maps them to (season, episode); a film to (disc,
scene); a game with flat cutscene files can return (None, None) and rely on the
filename itself as identity. Nothing in the pipeline assumes television.
"""

import re


# --- REQUIRED: filename -> provenance ---------------------------------------
# Return two orderable ints (or None, None if a file carries no usable order).
# Example below parses SxxExx; replace with whatever your filenames encode.
_PATTERN = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")


def parse(filename):
    m = _PATTERN.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# --- OPTIONAL: provenance -> human label ------------------------------------
# Shown in the tagger and diagnostics. Default (if you delete this) is "g<G>i<I>".
def label(group_idx, item_idx):
    if group_idx is None:
        return "unknown"
    return f"{group_idx}-{item_idx}"


# --- REQUIRED: the character roster -----------------------------------------
# The names you'll tag reference lines with and attribute utterances to.
CHARACTERS = [
    # "Alice",
    # "Bob",
]


# --- OPTIONAL: era-bands over group_idx -------------------------------------
# Voices drift across a long-running series; bands make you spread reference
# tags across eras so a centroid isn't built on one era only. Each band is
# (name, lo_group, hi_group), inclusive. Set to None if your source has no
# meaningful drift (a film, a single game) — the pipeline uses one implicit band.
BANDS = None
# BANDS = [("early", 1, 3), ("mid", 4, 7), ("late", 8, 12)]


# --- OPTIONAL: per-corpus tuning overrides ----------------------------------
# Only list knobs that differ from pipeline/project.py DEFAULT_TUNING.
# MAX_SPEAKERS especially: set it close to your real per-item speaker ceiling
# (too high wastes time, too low silently merges speakers — see docs/gotchas.md).
TUNING = {
    # "MAX_SPEAKERS": 8,
    # "LANGUAGE": "en",
    # "MIN_SNR_DB": 8.0,
    # Downstream (stages 4-6):
    # "AUDITION_TOP_N": 25,     # candidates per pool on the audition board
    # "LOUDNORM_LUFS": -16.0,   # final loudness target (broadcast; used when
    #                           # COMPRESS_VO is off)
    # "COMPRESS_VO": True,      # in-game voice? maximize to ~0 dBFS like game
    #                           # voice instead of loudnorm — see docs/tuning.md.
    #                           # Broadcast -16 LUFS is inaudible in a game mix.
    # "BANDPASS_HZ": (300, 3400),  # e.g. telephone band, or None
}


# --- REQUIRED: event pools --------------------------------------------------
# Each pool: (pool_id, display, suggested_char, keywords, description, [event_ids])
#   keywords    -> literal FTS terms; keep them distinctive, avoid stopwords
#                  ("got", "them", "in") which flood the keyword pass.
#   description -> a natural-language sentence of what the event MEANS; this is
#                  where semantic-match quality lives.
#   event_ids   -> the real target IDs this pool wires to (game soundbank event
#                  strings, or any labels meaningful to your downstream).
#
# OPTIONAL 7th element: a state-routing filter {state_var: [values]} (or None).
# The engine ignores it; only your package.py reads it, to fire a pool in a
# subset of an event's states — so several pools can share one event in
# different contexts. See projects/archer_wows/ for the worked example and
# docs/tuning.md for the contract. A 6-tuple (or None) fills the whole event.
POOLS = [
    ("example_event", "Example event", None,
     "example keywords here",
     "a natural-language description of what this event means",
     ["target_event_id_1", "target_event_id_2"]),

    # State-segregated example: two pools sharing one event, split by a state.
    # ("spot_battleship", "Spotted a battleship", None, "big huge",
    #  "calling out a large, dangerous target", ["target_spot_event"],
    #  {"target_type": ["battleship"]}),
    # ("spot_destroyer", "Spotted a destroyer", None, "small fast",
    #  "calling out a small, sneaky target", ["target_spot_event"],
    #  {"target_type": ["destroyer"]}),
]
