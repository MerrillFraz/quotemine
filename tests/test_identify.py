"""Band-based episode selection in Stage 2.

Locks in the fix for the generic-path bug: a single-band project whose items
have no parseable provenance (group_idx IS NULL, e.g. a flat game) must still
select those items — the old `group_idx BETWEEN lo AND hi` filter dropped them.
"""

import sqlite3

import pytest


@pytest.fixture
def db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE episodes(id INTEGER PRIMARY KEY, group_idx INT, "
                "item_idx INT, wav_path TEXT)")
    con.executemany("INSERT INTO episodes VALUES (?,?,?,?)", [
        (1, None, None, "/a.wav"),    # flat game: no provenance
        (2, None, None, "/b.wav"),
        (3, 2, 5, "/c.wav"),          # has provenance
        (4, 9, 1, None),              # no audio yet -> never selected
    ])
    return con


def test_single_band_selects_null_provenance(identify_mod, db):
    eps = identify_mod.episodes_for_band(db, -10**9, 10**9, single_band=True)
    ids = {r["id"] for r in eps}
    assert ids == {1, 2, 3}           # all with audio, null provenance included
    assert 4 not in ids               # excluded: no wav_path


def test_multi_band_filters_by_range_and_excludes_null(identify_mod, db):
    eps = identify_mod.episodes_for_band(db, 1, 3, single_band=False)
    ids = {r["id"] for r in eps}
    assert ids == {3}                 # only the in-range, audio-bearing item
    # null-provenance items (1, 2) are correctly outside a real numeric band


def test_multi_band_out_of_range_is_empty(identify_mod, db):
    eps = identify_mod.episodes_for_band(db, 20, 30, single_band=False)
    assert eps == []
