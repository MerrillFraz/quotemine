# -*- coding: utf-8 -*-
"""
archer_wot/package.py — pack identity for the ensemble WoT voice pack.

This is the optional project code hook (see pipeline/06_package.py). When
present, Stage 6 calls package(ctx) instead of its generic default.

The WoT layout itself — RC_<pool>/ containers, audio_mods.xml, meta.xml, the
Wwise checklist — lives in pipeline/build_wotpack.py, because every WoT-targeted
project emits exactly the same shape and differs only in the identity below.
See that module for how the Wwise + .wotmod build fits together, and
projects/archer_wot/WWISE.md for the manual Wwise step.
"""

import build_wotpack

IDENT = build_wotpack.Identity(
    mod_id="com.merrillfraz.quotemine.archer",
    mod_name="Quotemine — Archer Crew Voices",
    mod_description=(
        "Crew voiceover pack: lines from the animated series Archer, matched to "
        "World of Tanks standard-battle events. Built with Quotemine."
    ),
    mod_version="1.1.0",   # 1.1: retuned lead-in/out on ~60 lines, dropped 2
    bank_name="quotemine",
    mod_event_prefix="vo_qm_",
)


def package(ctx):
    return build_wotpack.build(ctx, IDENT)
