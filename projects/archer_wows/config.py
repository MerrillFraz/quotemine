# -*- coding: utf-8 -*-
"""
archer_wows — Archer voicepak for World of Warships.

Same Archer source corpus as `archer_wot`, matched to real World of Warships
voiceover events for a commander/announcer voice pack (distributed via Aslain's
Modpack). WoWs plays every announcer line through a single voice slot, so the
per-event `suggested_char` here is an ensemble *curation hint* for the audition
board (never a filter — see pipeline/03_match.py), not a per-role crew like WoT.

Contract (see pipeline/project.py):
    parse(filename) -> (season, episode)   [reused from archer_wot]
    label(g, i)     -> "S03E12"            [reused from archer_wot]
    CHARACTERS      -> Archer roster       [reused from archer_wot]
    BANDS           -> era-bands           [reused from archer_wot]
    POOLS           -> WoWs event pools    [7-tuples; see below]
    TUNING          -> per-corpus knobs

POOLS are 7-tuples:
    (pool_id, display, suggested_char, keywords, description, [game_event_ids],
     state_filter)

The 7th element is a **state-routing filter** — a dict {state_var: [values]} — or
None. It is opaque to the engine (03_match ignores it); only this project's
package.py reads it, to fill ONLY the mod.xml <Path> blocks of an event whose
WoWs states match. This is how several pools can share one event (e.g. the four
`torpedo_*` pools all target Play_VO_Ship_Alarms_Torpedo_Danger, each routed to
one VO_Torpedo_Location). None = fill the whole event (no segregation). Paths no
pool matches are dropped, so the game falls back to its default voice there.

The game_event IDs and state values are real WoWs strings mined from an extracted
voicepack's mod.xml. Highschool Fleet collab, scenario PVE, and tutorial events
are intentionally out of scope.
"""

import re

# Provenance: group_idx = season, item_idx = episode. Reused from archer_wot.
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

# Voice drift is a property of the source, not the game — same bands as archer_wot.
BANDS = [("S01-S03", 1, 3), ("S04-S07", 4, 7), ("S08-S11", 8, 11), ("S12-S14", 12, 14)]

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
    # Longer-line pools get a wider candidate window; terse callouts keep the
    # global 0.4-2.2s. Most quick-commands / callouts are short → global.
    "POOL_CAND_WINDOWS": {
        "battle_start": (3.0, 7.5),
        "result_stinger": (0.4, 3.0),
        "domination_winning": (0.5, 4.0),
        "domination_losing": (0.5, 4.0),
        "ship_dying": (0.5, 4.5),
        "map_border": (0.5, 4.0),
        "qc_tactic_enemy": (0.5, 4.0),
        "qc_tactic_ally": (0.5, 4.0),
    },
    "TOP_SEMANTIC": 60,
    "KW_BONUS": 0.03,
    # Float literal hits to the top for the keyword/hybrid pools where the words
    # ARE the signal (see docs/tuning.md). First-draft values — cheap to retune.
    "POOL_KW_BONUS": {
        # combat brag
        "good_hit": 0.6, "first_kill": 0.5, "double_kill": 0.5,
        "you_fragged_mine": 0.5, "team_hit": 0.4,
        # neutral / battle flow
        "result_stinger": 0.5, "battle_start": 0.3, "battle_timer": 0.4,
        # directional torpedoes — the direction word is everything
        "torpedo_ahead": 0.6, "torpedo_back": 0.6, "torpedo_left": 0.6, "torpedo_right": 0.6,
        # quick commands — each is a literal social/tactical phrase
        "qc_aye_aye": 0.6, "qc_no_way": 0.6, "qc_thank_you": 0.6, "qc_good_luck": 0.6,
        "qc_good_game": 0.6, "qc_need_support": 0.5, "qc_need_smoke": 0.6,
        "qc_need_air_defence": 0.5, "qc_need_vision": 0.5, "qc_sos": 0.6, "qc_back": 0.5,
        # consumables / weapons / alarms with a distinctive noun
        "cons_smoke": 0.6, "cons_engine": 0.5, "cons_aa": 0.5,
        "on_fire": 0.4, "flooding": 0.4, "module_engine": 0.4, "module_steering": 0.4,
        "engine_telegraph": 0.5, "weather_changed": 0.5, "autopilot": 0.5,
        # subs
        "sub_detected": 0.4, "sub_dive_limit": 0.4, "sub_energy_low": 0.4,
        # CV callouts
        "pilots_killed": 0.4, "pilots_hit": 0.4, "pilots_ready": 0.4,
    },
    "AUDITION_TOP_N": 25,
    "PREVIEW_EDIT_PAD_S": 1.0,
    "NUDGE_STEP_S": 0.05,
    "CLEAN_PAD_S": 0.10,
    # In-game VO: COMPRESS_VO maximizes every clip to ~0 dBFS like the game's own
    # voice (a -16 LUFS broadcast target is inaudible under gunfire/music — and
    # loudnorm misfires on the many sub-3s callouts; see docs/gotchas.md).
    # LOUDNORM_LUFS is unused while COMPRESS_VO is on — kept as the off fallback.
    "COMPRESS_VO": True,
    "LOUDNORM_LUFS": -16.0,
    "BANDPASS_HZ": None,
    "FADE_MS": 15,
}

# Shorthand event IDs.
_DOM = ["Play_VO_Domination_Status"]
_FRAG = ["Play_VO_Frags"]
_TORP = ["Play_VO_Ship_Alarms_Torpedo_Danger"]
_QC = ["Play_VO_Quick_Commands"]
_CONS = ["Play_VO_Consumable"]
_MOD = ["Play_VO_Alarm_Defective_Modules"]
_PIL = ["Play_VO_Pilots_Status"]

# POOLS: (pool_id, display, suggested_char, keywords, description, [events], state_filter)
POOLS = [
    # ======== Battle flow & result ==========================================
    ("battle_start", "Battle start", "Archer", "danger zone rally lets here",
     "rallying the crew as the battle kicks off — here we go, game on, everybody get ready, let's do this",
     ["Play_VO_Start_Battle"], None),

    ("result_stinger", "Battle result (generic)", "Archer",
     "typical perfect terrific wonderful fantastic unbelievable figures whatever anyway",
     "a dry, sardonic, deadpan one-liner as it all wraps up — flat sarcasm like 'typical,' 'perfect,' 'unbelievable,' or an unbothered 'anyway / whatever'; anticlimactic, lands the same either way, never openly celebrating or complaining",
     ["Play_VO_Result_Stinger"], None),

    ("domination_winning", "Winning the caps", "Archer", "winning ahead got",
     "cocky momentum as your team pulls ahead on the objective — we've got this, we're winning, keep it up",
     _DOM, {"VO_Domination": ["D_Ally", "W_Ally",
                              "VO_Domination_Advantage_Ally", "VO_Domination_Close_Win_Ally"]}),

    ("domination_losing", "Losing the caps", "Malory", "losing behind blowing",
     "frustrated that your team is falling behind on the objective — come on, we're losing this, pull it together",
     _DOM, {"VO_Domination": ["D_Enemy", "W_Enemy",
                              "VO_Domination_Advantage_Enemy", "VO_Domination_Close_Win_Enemy"]}),

    ("battle_timer", "Time running out", "Pam", "time hurry seconds",
     "anxious that the clock is almost up and there's barely any time left — hurry, we're running out of time",
     ["Play_VO_Timer_5"], None),

    # ======== Combat: you did it ============================================
    ("first_kill", "First kill", "Archer", "first classic nailed boom",
     "cocky and pleased about drawing first blood — first one down and you want everyone to know it was you",
     ["Play_VO_FirstKill"], None),

    ("double_kill", "Double kill", "Archer", "two double boom rampage",
     "gleeful, over-the-top bragging about wrecking two of them back to back — on a rampage and loving it",
     ["Play_VO_DoubleKill"], None),

    ("you_fragged_mine", "You sank a ship", "Archer", "dead killed sank boom bagged",
     "a smug one-liner right after sinking someone yourself — another one dead and you're very pleased with yourself",
     _FRAG, {"isPlayer": ["True"]}),

    ("you_fragged_ally", "Ally sank a ship", "Lana", "",
     "casually noting that another one just went down when a teammate scored — acknowledging the kill without taking any credit",
     _FRAG, {"isPlayer": ["False"]}),

    ("good_hit", "Solid hit", "Archer", "nailed boom crushed direct",
     "cocky and gloating right after landing a big clean hit — trash-talking that you just nailed them dead center",
     ["Play_VO_Hit_Feedback_Good_Hit"], None),

    # ======== Detection & torpedo warnings ==================================
    ("enemy_spotted", "Enemy spotted", "Lana", "spotted contact incoming",
     "calling out that you've just laid eyes on an enemy ship — there they are, contact, I see one",
     ["Play_VO_Detection_Enemy"], None),

    ("priority_target", "You are targeted", "Lana", "aiming focused shooting",
     "nervous realization that a bunch of guns are now pointed right at you — they're all aiming at us, this is bad",
     ["Play_VO_Priority_Target"], None),

    ("torpedo_ahead", "Torpedoes — ahead", "Lana", "ahead front coming",
     "urgent alarm that torpedoes are in the water directly in front of you — dead ahead, coming right at us",
     _TORP, {"VO_Torpedo_Location": ["Torpedo_Ahead"]}),

    ("torpedo_back", "Torpedoes — behind", "Lana", "behind back rear",
     "urgent alarm that torpedoes are coming up from behind you — they're on our tail, right behind us",
     _TORP, {"VO_Torpedo_Location": ["Torpedo_Back"]}),

    ("torpedo_left", "Torpedoes — port", "Lana", "left port side",
     "urgent alarm that torpedoes are coming in from the left — hard to the left, they're on our port side",
     _TORP, {"VO_Torpedo_Location": ["Torpedo_Left"]}),

    ("torpedo_right", "Torpedoes — starboard", "Lana", "right starboard side",
     "urgent alarm that torpedoes are coming in from the right — hard right, they're on our starboard side",
     _TORP, {"VO_Torpedo_Location": ["Torpedo_Right"]}),

    # ======== Quick commands (F-keys) =======================================
    ("qc_aye_aye", "“Affirmative!”", "Archer", "aye roger affirmative sir",
     "a crisp acknowledgment of an order — aye aye, roger that, on it, yes sir, got it",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_AYE_AYE"]}),

    ("qc_no_way", "“Negative!”", "Archer", "no way nope negative",
     "a flat refusal or disbelief — no way, nope, not happening, absolutely not",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_NO_WAY"]}),

    ("qc_thank_you", "“Thanks!”", "Cyril", "thanks thank appreciate",
     "a quick, genuine thank-you — thanks, thank you, appreciate it",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_THANK_YOU", "CMD_QUICK_THANK_YOU_ALLY_SHIP"]}),

    ("qc_good_luck", "“Good luck!”", "Ray", "good luck",
     "wishing the team good luck as things get going — good luck out there, good luck everyone",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_GOOD_LUCK"]}),

    ("qc_good_game", "“Good game!”", "Archer", "good game played",
     "a sporting good-game at the end — good game, well played, nicely done",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_GOOD_GAME", "CMD_QUICK_GOOD_GAME_ALLY_SHIP"]}),

    ("qc_caramba", "“Caramba!”", "Archer", "dammit damn son bitch",
     "a sharp, exasperated curse-of-the-moment — dammit, son of a bitch, oh come on, you have got to be kidding",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_CARAMBA"]}),

    ("qc_need_support", "“Need support!”", "Pam", "help support cover backup",
     "calling for backup — I need support here, cover me, get over here and help, backup now",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_NEED_SUPPORT",
                                 "CMD_QUICK_NEED_SUPPORT_ALLY_PLANE",
                                 "CMD_QUICK_NEED_SUPPORT_ALLY_SHIP"]}),

    ("qc_need_smoke", "“Need smoke!”", "Pam", "smoke cover screen",
     "asking for a smoke screen to hide behind — pop smoke, I need smoke, cover us up",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_NEED_SMOKE"]}),

    ("qc_need_air_defence", "“Need AA!”", "Pam", "air defense planes flak",
     "asking for anti-air cover against incoming planes — need air defense, shoot those planes down, cover the sky",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_NEED_AIR_DEFENCE"]}),

    ("qc_need_vision", "“Need vision!”", "Pam", "eyes spot vision where",
     "asking someone to spot for you — I need eyes on them, where are they, get me vision, spot that",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_NEED_VISION"]}),

    ("qc_sos", "“SOS!”", "Pam", "sos mayday help save",
     "a desperate cry for help — SOS, mayday, somebody help, save me, I'm in trouble",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_SOS"]}),

    ("qc_back", "“Fall back!”", "Lana", "fall back retreat pull",
     "calling a retreat — fall back, pull back, get out of there, retreat now",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_BACK", "CMD_QUICK_BACK_ALLY_SHIP"]}),

    ("qc_tactic_enemy", "Mark an enemy", "Archer", "there focus that get",
     "pointing out an enemy target to the team — there, get that one, focus that, over there, take it out",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_TACTIC_ENEMY_BASE", "CMD_QUICK_TACTIC_ENEMY_BUILDING",
                                 "CMD_QUICK_TACTIC_ENEMY_PLANE", "CMD_QUICK_TACTIC_ENEMY_POINT",
                                 "CMD_QUICK_TACTIC_ENEMY_SHIP"]}),

    ("qc_tactic_ally", "Mark a spot", "Archer", "over there that go",
     "directing the team's attention to a spot or ally — over there, that one, go there, right here",
     _QC, {"VO_Quick_Commands": ["CMD_QUICK_TACTIC_ALLY_BASE", "CMD_QUICK_TACTIC_ALLY_BUILDING",
                                 "CMD_QUICK_TACTIC_ALLY_PLANE", "CMD_QUICK_TACTIC_ALLY_POINT",
                                 "CMD_QUICK_TACTIC_ALLY_SHIP"]}),

    # ======== Consumables & weapons =========================================
    ("cons_smoke", "Smoke ready", "Krieger", "smoke screen cover",
     "the smoke generator is ready to deploy — smoke's up, ready to lay a screen",
     _CONS, {"HelpType": ["Smoke"]}),

    ("cons_engine", "Engine boost ready", "Krieger", "boost faster speed engine",
     "the engine boost is ready — more speed on tap, punch it, ready to open her up",
     _CONS, {"HelpType": ["Engine"]}),

    ("cons_aa", "Defensive AA ready", "Krieger", "air defense flak",
     "defensive anti-air fire is ready — flak up, ready to fill the sky, shred those planes",
     _CONS, {"HelpType": ["AirDefenseDisp"]}),

    ("cons_scout", "Spotter plane up", "Ray", "eyes spotter plane top",
     "the spotter plane is up and giving you eyes — up top, extra range, we can see further now",
     _CONS, {"HelpType": ["Scout"]}),

    ("cons_fighter", "Fighters ready", "Ray", "fighters cover planes",
     "the fighter consumable is ready to protect you from aircraft — fighters up, cover overhead",
     _CONS, {"HelpType": ["Fighter", "CallFighters"]}),

    ("consumable_end", "Consumable expired", "Krieger", "done over gone",
     "flat acknowledgment that the effect just wore off — annnd it's gone, that's done, back to normal",
     ["Play_VO_Consumable_End"], None),

    ("consumable_interrupted", "Consumable interrupted", "Krieger", "stopped interrupted",
     "annoyed that the gadget got cut off before it finished — well that got interrupted, dammit",
     ["Play_VO_Consumable_Interrupted"], None),

    ("air_support_ready", "Air support ready", "Krieger", "planes ready support",
     "the airstrike squadron has been rearmed and is ready to launch again — planes fueled and good to go",
     ["Play_VO_Reload_Air_Support"], None),

    ("air_support_shot", "Air strike called", "Ray", "send strike attack hit",
     "ordering the aircraft in to hit a target — send them in, hit them from above, strike now, go get 'em",
     ["Play_VO_Ship_Weapon_AirSupport_Shot"], None),

    ("depth_charge", "Depth charges away", "Archer", "drop charges bombs down",
     "casually announcing you're dropping depth charges on something lurking below — go say hi to the fishes, down you go",
     ["Play_VO_Ship_Weapon_DC_Roll_Shot",
      "Play_VO_Ship_Weapon_DC_Shoot_Shot",
      "Play_VO_Ship_Weapon_DC_Slide_Shot"], None),

    # ======== Damage & alarms (your ship) ===================================
    ("taking_damage", "Taking damage", "Archer", "hit hurts ow",
     "reacting to suddenly getting hit — pain, alarm, that one actually hurt",
     ["Play_VO_Ship_Avatar_Damage"], None),

    ("on_fire", "On fire", "Cheryl", "fire burning ablaze hot",
     "your own ship is burning and it needs to be dealt with — half panic, half weird delight at the flames",
     ["Play_VO_Fire_Alarm"], None),

    ("flooding", "Flooding", "Malory", "water flooding leak sinking",
     "alarm that you're taking on water and starting to flood — we've got a leak, seal it, we're going under",
     ["Play_VO_Ship_Avatar_Flooding"], None),

    ("module_engine", "Engine knocked out", "Archer", "engine motor power",
     "alarm that the engine just got knocked out — we've lost power, the engine's gone, we're dead in the water",
     _MOD, {"Module_Type": ["Module_Type_Engine"]}),

    ("module_steering", "Steering knocked out", "Archer", "steering rudder wheel controls",
     "alarm that the steering just got knocked out — we can't steer, the rudder's jammed, she won't turn",
     _MOD, {"Module_Type": ["Module_Type_Steering_Wheel", "StreeringWheel"]}),

    ("module_secondaries", "Secondaries knocked out", "Archer", "guns secondary",
     "alarm that a gun / secondary battery just got knocked out — lost a gun, that battery's down",
     _MOD, {"Module_Type": ["Module_Type_MG"]}),

    ("module_torps", "Torpedo tubes knocked out", "Archer", "tubes launchers torpedo",
     "alarm that the torpedo tubes just got knocked out — lost the launchers, tubes are wrecked",
     _MOD, {"Module_Type": ["Module_Type_TA"]}),

    ("ship_dying", "Ship going down", "Malory", "dying sinking down goodbye",
     "dramatic reaction to the ship being on its last legs and about to go under — this is it, we're done, abandon ship",
     ["Play_VO_Last_Hope", "Play_VO_Damage_Death"], None),

    ("team_hit", "You hit an ally", "Cyril", "sorry oops whoops accident",
     "awkward, sheepish apology for screwing up and hitting one of your own by mistake",
     ["Play_VO_Friendly_Hit"], None),

    ("teamkill_punish", "Teamkiller warning", "Cyril", "",
     "getting scolded for repeatedly hitting your own team — a shamed, in-trouble reaction to being flagged",
     ["Play_VO_Teamkill_Punishment"], None),

    # ======== Submarine =====================================================
    ("sub_detected", "Submarine detected", "Krieger", "sub below underwater detected",
     "wary alert that a submarine is lurking nearby — there's a sub down there, something's below us",
     ["Play_VO_Submarine_Detected"], None),

    ("sub_dive_limit", "At depth limit", "Krieger", "deep depth crush limit",
     "tense warning that you're as deep as you can go before the hull fails — any deeper and we implode",
     ["Play_VO_Submarine_Dive_Limit"], None),

    ("sub_energy_full", "Battery charged", "Krieger", "full charged topped",
     "satisfied note that the battery is topped off and fully charged again — all cells green, we're maxed",
     ["Play_VO_Submarine_Energy_Full"], None),

    ("sub_energy_low", "Battery low", "Krieger", "low empty dead power",
     "worried that the battery is nearly drained and you need to surface — running on fumes down here",
     ["Play_VO_Submarine_Alarm_Energy_Danger", "Play_VO_Submarine_Energy_Over"], None),

    # ======== Carrier air group (CV-only) ===================================
    ("pilots_spot", "Squadron contact", "Ray", "contact target eyes",
     "a pilot calling out that the squadron has eyes on a target below — contact, tally, got 'em spotted",
     ["Play_VO_Pilots_ID"], None),

    ("pilots_airborne", "Squadron airborne", "Ray", "airborne up off",
     "the squadron has just launched and is climbing away — wheels up, airborne, off we go",
     _PIL, {"VO_Plane_Status": ["Airborne", "TAKEOFF_ENDED"]}),

    ("pilots_inbound", "Attack run", "Ray", "engaging rolling attack tally",
     "the squadron is lining up and starting its attack run — rolling in, engaging, attacking now, tally-ho",
     _PIL, {"VO_Plane_Status": ["AtPos", "ATTACK_STARTED", "Engage"]}),

    ("pilots_killed", "Squadron scores", "Ray", "splash down got",
     "a pilot confirming the squadron just killed its target — splash one, target down, got him, scratch one",
     _PIL, {"VO_Plane_Status": ["Destroy", "TangoDown"]}),

    ("pilots_hit", "Squadron under fire", "Ray", "hit fire mayday",
     "the squadron is taking fire and losing planes — we're hit, taking fire, mayday, they're on us",
     _PIL, {"VO_Plane_Status": ["UNDER_ATTACK", "UnderAttack"]}),

    ("pilots_rtb", "Squadron returning", "Ray", "home back landing",
     "the squadron is heading back to the carrier to rearm — heading home, RTB, coming in to land",
     _PIL, {"VO_Plane_Status": ["LANDING_INITIATED", "Land"]}),

    ("pilots_ready", "Squadron ready", "Ray", "ready standing",
     "the squadron is fueled, armed, and ready to launch — ready to go, standing by, good to launch",
     _PIL, {"VO_Plane_Status": ["Ready"]}),

    # ======== Ancillary =====================================================
    ("map_border", "Approaching border", "Malory", "back turn edge",
     "nagging, exasperated warning that you're wandering off the edge of the map — turn around, where do you think you're going",
     ["Play_VO_Alarm_Map_Border"], None),

    ("engine_telegraph", "Engine order", "Cyril", "full speed ahead stop slow",
     "relaying a change of speed to the engine room — ahead full, all stop, back her down, slow ahead",
     ["Play_VO_Telegraph"], None),

    ("autopilot", "Autopilot engaged", "Cyril", "autopilot course hands",
     "confirming the ship is now steering itself on a set course — autopilot's on, course set, hands off",
     ["Play_VO_Autopilot"], {"VO_Autopilot": ["VO_Autopilot_On"]}),

    ("weather_changed", "Weather changed", "Cyril", "storm fog snow seas",
     "noting that the weather just turned — fog rolling in, a storm, snow, visibility dropping, rough seas",
     ["Play_VO_Alarm_Weather_Changed"], None),
]
