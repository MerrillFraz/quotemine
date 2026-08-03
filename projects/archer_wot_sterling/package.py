# -*- coding: utf-8 -*-
"""
archer_wot_sterling/package.py — pack identity for the Sterling-only WoT pack.

Same layout as the ensemble pack; only the identity differs. See
pipeline/build_wotpack.py.

The distinct mod_id and bank_name keep this pack's files from colliding with
the ensemble pack's inside the game's mods folder. They do NOT make the two
co-installable: both remap the same vo_* events, so whichever loads last wins.
Character packs are alternatives — install one.
"""

import build_wotpack

IDENT = build_wotpack.Identity(
    mod_id="com.merrillfraz.quotemine.archer.sterling",
    mod_name="Quotemine — Sterling",
    mod_description=(
        "Single-character crew voiceover pack: Sterling Archer, from the "
        "animated series Archer, on every World of Tanks standard-battle "
        "event. Catchphrase-led, so it favours the line fans want over the "
        "line that fits. Built with Quotemine."
    ),
    mod_version="1.0.0",
    bank_name="quotemine_sterling",
    mod_event_prefix="vo_qms_",
)


def package(ctx):
    return build_wotpack.build(ctx, IDENT)
