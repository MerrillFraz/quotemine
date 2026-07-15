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
    "TOP_SEMANTIC": 60,
    "KW_BONUS": 0.08,
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
POOLS = [
    # ---- Combat: braggy tier (the player did it) ----------------------------
    ("you_penetrated", "You penetrated", "Archer",
     "through them nailed clean",
     "triumphant, you punched a shot clean through the enemy armor and dealt damage",
     ["vo_enemy_hp_damaged_by_projectile_by_player",
      "vo_enemy_hp_damaged_by_projectile_and_gun_damaged_by_player",
      "vo_enemy_hp_damaged_by_projectile_and_chassis_damaged_by_player",
      "vo_enemy_hp_damaged_by_explosion_at_direct_hit_by_player"]),

    ("you_no_pen", "You failed to penetrate", "Archer",
     "bounced nothing armor thick useless",
     "frustrated, your shot hit the enemy but failed to penetrate their armor",
     ["vo_armor_not_pierced_by_player",
      "vo_enemy_no_hp_damage_at_no_attempt_by_player",
      "vo_enemy_no_hp_damage_at_attempt_and_gun_damaged_by_player",
      "vo_enemy_no_hp_damage_at_attempt_and_chassis_damaged_by_player",
      "vo_enemy_no_hp_damage_at_no_attempt_and_gun_damaged_by_player",
      "vo_enemy_no_hp_damage_at_no_attempt_and_chassis_damaged_by_player"]),

    ("you_ricochet", "Your shot ricocheted", "Archer",
     "bounced off skipped ricochet glanced",
     "your shot glanced off the enemy armor and ricocheted away, no damage",
     ["vo_armor_ricochet_by_player"]),

    ("you_splashed", "You splashed them", "Archer",
     "covered them close blast splash",
     "your high-explosive shell splashed and covered the enemy in the blast",
     ["vo_damage_by_near_explosion_by_player"]),

    ("you_set_fire", "You set them ablaze", "Cheryl",
     "fire burning lit ablaze cook",
     "gleeful, you set the enemy tank on fire",
     ["vo_enemy_fire_started_by_player"]),

    ("you_killed_enemy", "You destroyed an enemy", "Archer",
     "dead killed destroyed done boom next",
     "triumphant and smug, you personally destroyed an enemy tank",
     ["vo_enemy_killed_by_player"]),

    # ---- Combat: neutral / ally ---------------------------------------------
    ("enemy_down", "An enemy went down", "Archer",
     "down less gone another dead",
     "an enemy tank was destroyed by an ally, acknowledged",
     ["vo_enemy_killed", "vo_vehicle_destroyed"]),

    ("team_kill", "You hit an ally", "Cyril",
     "sorry accident oops wasn't friendly whoops",
     "awkward, you damaged or killed a friendly tank by mistake",
     ["vo_ally_killed_by_player"]),

    # ---- Your tank: taking damage -------------------------------------------
    ("took_damage", "Module damaged", "Archer",
     "hit hurt ow took broken",
     "you took a hit and a module was damaged",
     ["vo_gun_damaged", "vo_engine_damaged", "vo_track_damaged",
      "vo_radio_damaged", "vo_fuel_tank_damaged", "vo_surveying_devices_damaged",
      "vo_turret_rotator_damaged", "vo_ammo_bay_damaged"]),

    ("module_wrecked", "Module destroyed", "Archer",
     "wrecked gone destroyed shot dead useless",
     "a critical hit destroyed one of your modules",
     ["vo_gun_destroyed", "vo_engine_destroyed", "vo_track_destroyed",
      "vo_surveying_devices_destroyed", "vo_turret_rotator_destroyed"]),

    ("crew_down", "Crew knocked out", "Malory",
     "down hurt cold hit man passed",
     "one of your crew was concussed and knocked out",
     ["vo_commander_killed", "vo_driver_killed", "vo_gunner_killed",
      "vo_loader_killed", "vo_radioman_killed", "vo_crew_deactivated"]),

    ("repaired", "Module repaired", "Krieger",
     "fixed working back again running",
     "a damaged module or the crew was repaired and is working again",
     ["vo_gun_functional", "vo_engine_functional", "vo_track_functional",
      "vo_track_functional_can_move", "vo_surveying_devices_functional",
      "vo_turret_rotator_functional"]),

    ("on_fire", "On fire", "Cheryl",
     "fire burning flames hot smoke put",
     "alarm, your tank has caught fire and is burning",
     ["vo_fire_started"]),

    ("fire_out", "Fire extinguished", "Archer",
     "extinguished done relief handled",
     "relief, the fire on your tank has been put out",
     ["vo_fire_stopped"]),

    # ---- Battle flow --------------------------------------------------------
    ("battle_start", "Battle start", "Archer",
     "start begin ready move people",
     "the battle is beginning, rallying the crew as it kicks off",
     ["vo_start_battle"]),

    ("base_captured", "Base captured", "Archer",
     "cap base taken ours point captured",
     "the base has been captured",
     ["vo_target_captured"]),

    ("target_lost", "Target lost", "Lana",
     "gone lost vanished slipped away",
     "you lost sight of the enemy target",
     ["vo_target_lost", "vo_target_unlocked"]),
]
