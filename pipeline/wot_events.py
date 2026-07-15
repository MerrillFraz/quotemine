# -*- coding: utf-8 -*-
"""
wot_events.py — Real World of Tanks standard-battle voiceover events, pooled.

Source: Voiceover_old.wwu from the official WG Wwise project (WoT 2.x).
Each POOL groups game events that share an emotional beat, because:
  (a) the damage panel already tells the player WHICH module/crew was hit —
      the voice line is flavor, not information;
  (b) the source material can't reliably distinguish "loader knocked out" from
      "radioman knocked out" anyway.

At Stage 9 (Wwise), each pool becomes ONE Random Container; the container is
wired to fire on ALL of that pool's `events`. So one pool of 6 Archer lines
gives variety across every game event mapped to it.

POOLS format: (pool_id, display, suggested_char, keywords, description, [game_event_ids])
- keywords    -> FTS pass
- description -> semantic pass (derived from WG's own Russian event comments)
- game_events -> the real vo_* IDs this pool's container will be wired to
"""

POOLS = [
    # ---- Combat: braggy tier (the player did it) ----------------------------
    ("you_penetrated", "You penetrated", "Archer",
     "through got them nailed clean right in",
     "triumphant, you punched a shot clean through the enemy armor and dealt damage",
     ["vo_enemy_hp_damaged_by_projectile_by_player",
      "vo_enemy_hp_damaged_by_projectile_and_gun_damaged_by_player",
      "vo_enemy_hp_damaged_by_projectile_and_chassis_damaged_by_player",
      "vo_enemy_hp_damaged_by_explosion_at_direct_hit_by_player"]),

    ("you_no_pen", "You failed to penetrate", "Archer",
     "bounced nothing armor thick damn useless",
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
     "covered got them close blast splash",
     "your high-explosive shell splashed and covered the enemy in the blast",
     ["vo_damage_by_near_explosion_by_player"]),

    ("you_set_fire", "You set them ablaze", "Cheryl",
     "fire burning lit ablaze cook",
     "gleeful, you set the enemy tank on fire",
     ["vo_enemy_fire_started_by_player"]),

    ("you_killed_enemy", "You destroyed an enemy", "Archer",
     "got him dead killed destroyed done boom next",
     "triumphant and smug, you personally destroyed an enemy tank",
     ["vo_enemy_killed_by_player"]),

    # ---- Combat: neutral / ally ---------------------------------------------
    ("enemy_down", "An enemy went down", "Archer",
     "down one less gone another dead",
     "an enemy tank was destroyed by an ally, acknowledged",
     ["vo_enemy_killed", "vo_vehicle_destroyed"]),

    ("team_kill", "You hit an ally", "Cyril",
     "sorry accident oops wasn't friendly whoops",
     "awkward, you damaged or killed a friendly tank by mistake",
     ["vo_ally_killed_by_player"]),

    # ---- Your tank: taking damage -------------------------------------------
    ("took_damage", "Module damaged", "Archer",
     "hit hurt damn ow took that broken",
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
     "down hurt out cold hit man passed",
     "one of your crew was concussed and knocked out",
     ["vo_commander_killed", "vo_driver_killed", "vo_gunner_killed",
      "vo_loader_killed", "vo_radioman_killed", "vo_crew_deactivated"]),

    ("repaired", "Module repaired", "Krieger",
     "fixed working back good again running",
     "a damaged module or the crew was repaired and is working again",
     ["vo_gun_functional", "vo_engine_functional", "vo_track_functional",
      "vo_track_functional_can_move", "vo_surveying_devices_functional",
      "vo_turret_rotator_functional"]),

    ("on_fire", "On fire", "Cheryl",
     "fire burning flames hot smoke put out",
     "alarm, your tank has caught fire and is burning",
     ["vo_fire_started"]),

    ("fire_out", "Fire extinguished", "Archer",
     "out extinguished done relief handled",
     "relief, the fire on your tank has been put out",
     ["vo_fire_stopped"]),

    # ---- Battle flow --------------------------------------------------------
    ("battle_start", "Battle start", "Archer",
     "start begin ready move go people here we",
     "the battle is beginning, rallying the crew as it kicks off",
     ["vo_start_battle"]),

    ("base_captured", "Base captured", "Archer",
     "cap base taken ours point captured",
     "the base has been captured",
     ["vo_target_captured"]),

    ("target_lost", "Target lost", "Lana",
     "gone lost where vanished slipped away",
     "you lost sight of the enemy target",
     ["vo_target_lost", "vo_target_unlocked"]),
]
