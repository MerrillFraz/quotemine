"""Downstream (stages 4-6) pure-logic: manifest shape, picks import, hook resolution.

Audio/ffmpeg is validated by hand (like the ML stages), not here."""

import json
from types import SimpleNamespace

import downstream
import pytest


# --- cut span bounds (pure pad math; ffmpeg itself validated by hand) --------

def test_span_bounds_symmetric_pad():
    # end 3.0, start 1.0, 0.1 pad both ends -> [0.9, 3.1], dur 2.2
    ss, dur = downstream._span_bounds(1.0, 3.0, 0.1, 0.1)
    assert ss == pytest.approx(0.9)
    assert dur == pytest.approx(2.2)


def test_span_bounds_asymmetric_lead_in_out():
    # +0.2 lead-in, +0.3 lead-out on a [1.0, 3.0] line
    ss, dur = downstream._span_bounds(1.0, 3.0, 0.30, 0.40)
    assert ss == pytest.approx(0.70)          # 1.0 - 0.30
    assert dur == pytest.approx(3.40 - 0.70)  # (3.0 + 0.40) - ss


def test_span_bounds_negative_head_moves_in_point_later():
    # a negative head delta tightens the front: in-point moves later, not earlier
    ss, _ = downstream._span_bounds(1.00, 2.0, -0.20, 0.0)
    assert ss == pytest.approx(1.20)          # 1.00 - (-0.20)


def test_span_bounds_floors_at_zero_near_file_head():
    # pad_head larger than start would go negative -> floored to 0
    ss, _ = downstream._span_bounds(0.05, 2.0, 0.20, 0.0)
    assert ss == 0.0


# --- write_manifest ---------------------------------------------------------

def test_manifest_groups_clips_by_pool_with_events(tmp_path):
    entries = [
        {"pool_id": "greet", "file": "greet/1.wav", "character": "A", "text": "hi", "source": "/v.mkv"},
        {"pool_id": "greet", "file": "greet/2.wav", "character": "B", "text": "hey", "source": "/v.mkv"},
        {"pool_id": "bye", "file": "bye/3.wav", "character": "A", "text": "later", "source": "/v.mkv"},
    ]
    pools = {"greet": {"display": "Greeting", "suggested_char": "A"},
             "bye": {"display": "Farewell", "suggested_char": None},
             "empty": {"display": "Unused", "suggested_char": None}}
    pool_events = {"greet": ["evt_g1", "evt_g2"], "bye": ["evt_b1"], "empty": ["evt_e1"]}

    out = downstream.write_manifest(entries, pools, pool_events, tmp_path / "m.json")

    assert out["total_clips"] == 3
    written = json.loads((tmp_path / "m.json").read_text())
    by_id = {p["pool_id"]: p for p in written["pools"]}
    assert by_id["greet"]["clip_count"] == 2
    assert by_id["greet"]["game_events"] == ["evt_g1", "evt_g2"]
    assert {c["character"] for c in by_id["greet"]["clips"]} == {"A", "B"}
    assert by_id["bye"]["clip_count"] == 1
    assert by_id["empty"]["clip_count"] == 0          # pool with no picks still listed


# --- picks import (Stage 4) -------------------------------------------------

def test_import_loads_picks_table(audition_mod, tmp_path):
    db = audition_mod.connect(tmp_path)            # creates the picks table
    picks = [{"pool_id": "greet", "utterance_id": 1},
             {"pool_id": "greet", "utterance_id": 2},
             {"pool_id": "bye", "utterance_id": 3}]
    pf = tmp_path / "picks.json"
    pf.write_text(json.dumps(picks))

    audition_mod.cmd_import(SimpleNamespace(picksfile=str(pf)), db)

    rows = db.execute("SELECT pool_id, utterance_id FROM picks ORDER BY utterance_id").fetchall()
    assert [(r["pool_id"], r["utterance_id"]) for r in rows] == [("greet", 1), ("greet", 2), ("bye", 3)]


def test_import_carries_lead_in_out_deltas(audition_mod, tmp_path):
    db = audition_mod.connect(tmp_path)
    picks = [{"pool_id": "greet", "utterance_id": 1, "head_s": 0.1, "tail_s": 0.3},
             {"pool_id": "bye", "utterance_id": 2}]        # pre-tuning row: no deltas
    pf = tmp_path / "picks.json"
    pf.write_text(json.dumps(picks))

    audition_mod.cmd_import(SimpleNamespace(picksfile=str(pf)), db)

    rows = {r["utterance_id"]: r for r in
            db.execute("SELECT utterance_id, head_s, tail_s FROM picks")}
    assert (rows[1]["head_s"], rows[1]["tail_s"]) == (0.1, 0.3)
    assert (rows[2]["head_s"], rows[2]["tail_s"]) == (0.0, 0.0)  # defaults


def test_import_is_idempotent_replace(audition_mod, tmp_path):
    db = audition_mod.connect(tmp_path)
    pf = tmp_path / "picks.json"
    pf.write_text(json.dumps([{"pool_id": "p", "utterance_id": 9}]))
    audition_mod.cmd_import(SimpleNamespace(picksfile=str(pf)), db)
    # re-import a different set: prior picks are cleared, not accumulated
    pf.write_text(json.dumps([{"pool_id": "p", "utterance_id": 10}]))
    audition_mod.cmd_import(SimpleNamespace(picksfile=str(pf)), db)
    ids = [r["utterance_id"] for r in db.execute("SELECT utterance_id FROM picks")]
    assert ids == [10]


# --- package hook resolution (Stage 6) --------------------------------------

def test_hook_present_for_archer_wot(package_mod):
    hook = package_mod.load_package_hook("archer_wot")
    assert callable(hook)


def test_hook_absent_returns_none(package_mod):
    assert package_mod.load_package_hook("does_not_exist_xyz") is None
