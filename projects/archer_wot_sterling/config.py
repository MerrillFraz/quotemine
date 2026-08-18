# -*- coding: utf-8 -*-
"""
archer_wot_sterling — a single-character WoT pack: Sterling Archer only.

A variant of `archer_wot` over the SAME corpus. Same filename parsing, same
roster, same era bands, same 17 battle-event pools — the only difference is that
every pool is restricted to one character and seeded with his catchphrases.

The corpus is shared, not re-mined:

    python pipeline/fork_corpus.py --from archer_wot --to archer_wot_sterling

copies the ~80 MB corpus.db (keeping the transcripts, the human character
tagging, and the Stage 3 embedding cache) and clears only the project layer. No
GPU stage runs again, and the 5.6 GB of audio is shared rather than duplicated.

Design note — this pack trades accuracy for fan-service. A single character
cannot cover 17 battle events with a well-matched line for each, so pools that
have no catchphrase fall back to ordinary semantic matching within his lines.
Some of those fills are mediocre; that is the accepted cost of never breaking
the single-voice illusion. See docs/tuning.md (POOL_FILTERS).
"""

from project import load_sibling_config

_wot = load_sibling_config("archer_wot")

# Provenance parsing, roster and era bands are the corpus's, not the pack's.
parse = _wot.parse
label = _wot.label
CHARACTERS = _wot.CHARACTERS
BANDS = _wot.BANDS

# The event pools are the target game's, not the corpus's — identical to the
# ensemble pack. `suggested_char` inside them is now moot (every pool is
# Archer), but it is only ever a display hint, so it costs nothing to keep.
POOLS = _wot.POOLS

# The featured character. Everything below is derived from this, so a sibling
# pack for another character is this file with the name changed and its own
# phrase assignments.
LEAD = "Archer"

# Catchphrases per pool, from:
#   python pipeline/phrases.py --project archer_wot probe --character Archer
# Only phrases that survived the corpus probe are here — a fan wiki lists many
# more, but a phrase with no transcript hits is just a dead search key. Hits are
# noted so it's obvious which pools are carried by a real catchphrase and which
# are leaning on the semantic fallback.
#
# Assignment is the editorial step, and it is deliberately loose: "just the tip"
# on a penetrating hit and "that's how you get ants" on taking damage are not
# what the events mean, they're what's funny. That is the entire point of the
# character packs.
_PHRASES = {
    "battle_start":     ["danger zone", "rampage"],          # 10, 19 hits
    "you_penetrated":   ["just the tip", "phrasing"],        #  4, 29
    "you_killed_enemy": ["phrasing", "get some"],            # 29, 14
    "you_no_pen":       ["had something for this",           #  7
                         "eat a dick"],                      #  7
    "you_set_fire":     ["burn"],                            # 32
    "on_fire":          ["burn"],                            # 32
    "took_damage":      ["ants"],                            # 54  ("...get ants")
    "module_wrecked":   ["tinnitus"],                        # 11
    "team_kill":        ["idiot"],                           # 117
    "crew_down":        ["idiot"],                           # 117
    "target_lost":      ["pout"],                            #  9
    "you_ricochet":     ["classic her"],                     #  4
}

TUNING = {
    **_wot.TUNING,
    # "*" restricts every pool to the lead; per-pool entries merge over it.
    "POOL_FILTERS": {
        "*": {"chars": [LEAD]},
        **{pid: {"phrases": ph} for pid, ph in _PHRASES.items()},
    },
}
