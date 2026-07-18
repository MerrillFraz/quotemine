# -*- coding: utf-8 -*-
"""
archer_wot — the worked example project.

Attributes lines from the animated series *Archer* (14 seasons) and matches
them to real World of Tanks standard-battle voiceover events, for a crew-callout
voice pack. This is the reference config: copy projects/_template/ for your own
show/film/game and fill in the same five things.

Contract (see pipeline/project.py for the full spec):
    parse(filename) -> (group_idx, item_idx)   here: season, episode
    label(g, i)     -> str                      here: "S03E12"
    CHARACTERS      -> roster
    BANDS           -> era-bands over season (voices drift across 14 seasons)
    POOLS           -> WoT event pools
    TUNING          -> per-corpus knobs
"""

import re

# Provenance: group_idx = season, item_idx = episode.
# S03E12, s3.e12, 3x12, etc.
_SXXEXX = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")
_NXXN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")


def parse(filename):
    m = _SXXEXX.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _NXXN.search(filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def label(group_idx, item_idx):
    g = "??" if group_idx is None else f"{group_idx:02d}"
    i = "??" if item_idx is None else f"{item_idx:02d}"
    return f"S{g}E{i}"


CHARACTERS = ["Archer", "Lana", "Malory", "Cyril", "Pam", "Cheryl", "Krieger", "Ray"]

# Voices drift over a 14-season run; a centroid built on one era underperforms
# on others. Bands group seasons so tagging spreads references across eras.
BANDS = [("S01-S03", 1, 3), ("S04-S07", 4, 7), ("S08-S11", 8, 11), ("S12-S14", 12, 14)]

# Per-corpus knobs. Only what differs from pipeline/project.py DEFAULT_TUNING
# strictly needs to be here, but the full set is restated for clarity since this
# is the reference example. Tuned on a 12 GB RTX 3080.
TUNING = {
    "LANGUAGE": "en",
    "MIN_SPEAKERS": 2,
    "MAX_SPEAKERS": 10,
    "MAX_WORD_GAP_S": 0.45,
    "MIN_UTTERANCE_S": 0.30,
    "MAX_UTTERANCE_S": 8.00,
    "EPISODES_PER_BAND": 8,
    "CLUSTERS_PER_EPISODE": 8,
    "UTTS_PER_CLUSTER": 6,
    "TALK_FLOOR_S": 60.0,
    "UTT_MIN_S": 0.8,
    "UTT_MAX_S": 3.0,
    "UTT_MIN_WORDS": 3,
    "ISOLATION_PAD_S": 0.5,
    "MIN_SNR_DB": 8.0,
    "CLUSTER_EMBED_UTTS": 12,
    "DEFAULT_THRESHOLD": 0.50,
    "CAND_MIN_S": 0.4,
    "CAND_MAX_S": 2.2,
    # battle_start is a rally line (usually 3-6s), not a terse callout, so it
    # gets a wider window; every other pool keeps the global 0.4-2.2s callout
    # window. See docs/tuning.md (POOL_CAND_WINDOWS).
    "POOL_CAND_WINDOWS": {"battle_start": (3.0, 7.5)},
    "TOP_SEMANTIC": 60,
    "KW_BONUS": 0.03,
    # Float keyword hits to the top of the board for pools where the literal
    # words are the signal (terse trash-talk / frustration / relief that scores
    # low on semantic similarity), instead of burying them under mediocre
    # semantics. you_penetrated leans hardest on this.
    "POOL_KW_BONUS": {
        "you_penetrated": 0.6,
        "you_no_pen": 0.4,
        "fire_out": 0.4,
        "base_captured": 0.4,
    },
    "AUDITION_TOP_N": 25,
    "PREVIEW_EDIT_PAD_S": 1.0,
    "NUDGE_STEP_S": 0.05,
    "CLEAN_PAD_S": 0.10,
    "LOUDNORM_LUFS": -16.0,
    "BANDPASS_HZ": None,
    "FADE_MS": 15,
}

# Real World of Tanks standard-battle voiceover events, pooled by emotional beat.
# Source: Voiceover_old.wwu from the official WG Wwise project (WoT 2.x).
# Each POOL groups game events that share a beat, because the damage panel
# already tells the player WHICH module/crew was hit — the voice line is flavor,
# not information. At the Wwise stage each pool becomes ONE Random Container,
# wired to fire on ALL of that pool's game events.
#
# POOLS format: (pool_id, display, suggested_char, keywords, description, [game_event_ids])
#   keywords    -> FTS pass (literal, distinctive terms; avoid stopwords)
#   description -> semantic pass (natural-language meaning of the event)
#   game_events -> the real vo_* IDs this pool's container will be wired to
#
# Retune note: for a source with no literal tank vocabulary, the FTS keyword
# pass mostly surfaces false friends ("lost"->"lost consciousness"), so keywords
# are blanked on the combat/mechanical pools and kept only where a term is
# genuinely distinctive. Descriptions are POV-locked (aggressor vs. victim) and
# describe the emotional beat, not the tank mechanic ("match the vibe, not the
# verb"). KW_BONUS is lowered to 0.03 so surviving keyword hits can't outrank a
# better semantic match. See docs/tuning.md.
POOLS = [
    # ---- Combat: braggy tier (the player did it) ----------------------------
    ("you_penetrated", "You penetrated", "Archer", "nailed boom hurt rampage",
     "cocky and gloating right after you hit and hurt someone — trash-talking, taunting them that they just got nailed",
     ["vo_enemy_hp_damaged_by_projectile_by_player",
      "vo_enemy_hp_damaged_by_projectile_and_gun_damaged_by_player",
      "vo_enemy_hp_damaged_by_projectile_and_chassis_damaged_by_player",
      "vo_enemy_hp_damaged_by_explosion_at_direct_hit_by_player"]),

    ("you_no_pen", "You failed to penetrate", "Archer", "dammit goddammit kidding",
     "exasperated and pissed off that your attack did nothing — frustrated cursing and disbelief, oh come on, you have got to be kidding me, useless",
     ["vo_armor_not_pierced_by_player",
      "vo_enemy_no_hp_damage_at_no_attempt_by_player",
      "vo_enemy_no_hp_damage_at_attempt_and_gun_damaged_by_player",
      "vo_enemy_no_hp_damage_at_attempt_and_chassis_damaged_by_player",
      "vo_enemy_no_hp_damage_at_no_attempt_and_gun_damaged_by_player",
      "vo_enemy_no_hp_damage_at_no_attempt_and_chassis_damaged_by_player"]),

    ("you_ricochet", "Your shot ricocheted", "Archer", "",
     "dismissive and mocking that it just bounced off harmlessly and did nothing at all",
     ["vo_armor_ricochet_by_player"]),

    ("you_splashed", "You splashed them", "Archer", "",
     "a close blast that still caught them — not a direct hit but they felt it",
     ["vo_damage_by_near_explosion_by_player"]),

    ("you_set_fire", "You set them ablaze", "Cheryl", "burning ablaze cook lit",
     "gleeful, almost gloating, that you set them on fire and they're burning",
     ["vo_enemy_fire_started_by_player"]),

    ("you_killed_enemy", "You destroyed an enemy", "Archer", "killed dead boom",
     "cocky and triumphant right after getting a kill — a smug one-liner over a fresh body, someone's dead and you're pleased with yourself",
     ["vo_enemy_killed_by_player"]),

    # ---- Combat: neutral / ally ---------------------------------------------
    ("enemy_down", "An enemy went down", "Archer", "",
     "casually acknowledging that another one just went down — noting a kill without taking credit",
     ["vo_enemy_killed", "vo_vehicle_destroyed"]),

    ("team_kill", "You hit an ally", "Cyril", "sorry oops whoops accident",
     "awkward, sheepish apology for screwing up and hitting one of your own by mistake",
     ["vo_ally_killed_by_player"]),

    # ---- Your tank: taking damage -------------------------------------------
    ("took_damage", "Module damaged", "Archer", "",
     "reacting to suddenly taking a hit — pain, alarm, something on you just got hurt or broke",
     ["vo_gun_damaged", "vo_engine_damaged", "vo_track_damaged",
      "vo_radio_damaged", "vo_fuel_tank_damaged", "vo_surveying_devices_damaged",
      "vo_turret_rotator_damaged", "vo_ammo_bay_damaged"]),

    ("module_wrecked", "Module destroyed", "Archer", "",
     "alarm that something critical just got wrecked and is now completely useless — a bad, disabling hit",
     ["vo_gun_destroyed", "vo_engine_destroyed", "vo_track_destroyed",
      "vo_surveying_devices_destroyed", "vo_turret_rotator_destroyed"]),

    ("crew_down", "Crew knocked out", "Malory", "passed knocked",
     "concern that one of your people just got hurt and knocked out — someone's down",
     ["vo_commander_killed", "vo_driver_killed", "vo_gunner_killed",
      "vo_loader_killed", "vo_radioman_killed", "vo_crew_deactivated"]),

    ("repaired", "Module repaired", "Krieger", "",
     "relieved acknowledgment that it's working again and you're back in action — there we go, good to go, back in business, all set",
     ["vo_gun_functional", "vo_engine_functional", "vo_track_functional",
      "vo_track_functional_can_move", "vo_surveying_devices_functional",
      "vo_turret_rotator_functional"]),

    ("on_fire", "On fire", "Cheryl", "burning",
     "panic and alarm that you yourself are on fire and burning — needs to be put out now",
     ["vo_fire_started"]),

    ("fire_out", "Fire extinguished", "Archer", "handled",
     "relief that the emergency is over and dealt with — handled it, all good now, crisis averted, we're okay",
     ["vo_fire_stopped"]),

    # ---- Battle flow --------------------------------------------------------
    ("battle_start", "Battle start", "Archer", "",
     "rallying the crew as things kick off — here we go, get ready, let's move",
     ["vo_start_battle"]),

    ("base_captured", "Base captured", "Archer", "captured victory",
     "satisfaction that the objective is taken and it's ours now — we did it, we won, the position is ours",
     ["vo_target_captured"]),

    ("target_lost", "Target lost", "Lana", "",
     "they slipped away and you've lost track of them — gone, out of sight, can't find them now",
     ["vo_target_lost", "vo_target_unlocked"]),
]
