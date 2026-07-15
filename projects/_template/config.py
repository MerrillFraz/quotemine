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
}


# --- REQUIRED: event pools --------------------------------------------------
# Each pool: (pool_id, display, suggested_char, keywords, description, [event_ids])
#   keywords    -> literal FTS terms; keep them distinctive, avoid stopwords
#                  ("got", "them", "in") which flood the keyword pass.
#   description -> a natural-language sentence of what the event MEANS; this is
#                  where semantic-match quality lives.
#   event_ids   -> the real target IDs this pool wires to (game soundbank event
#                  strings, or any labels meaningful to your downstream).
POOLS = [
    ("example_event", "Example event", None,
     "example keywords here",
     "a natural-language description of what this event means",
     ["target_event_id_1", "target_event_id_2"]),
]
