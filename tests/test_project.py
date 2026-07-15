"""Project loading + config contract — the portability surface."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import project as proj_mod


def make_module(**overrides):
    base = dict(parse=lambda f: (None, None), CHARACTERS=["A", "B"], POOLS=[])
    base.update(overrides)
    return SimpleNamespace(**base)


# --- band normalization -----------------------------------------------------

def test_bands_none_becomes_single_all_band():
    p = proj_mod.Project("x", make_module(BANDS=None), Path("/tmp/x"))
    assert len(p.BANDS) == 1
    assert p.BANDS[0][0] == "all"
    # single band covers every group_idx, and null provenance too
    assert p.band_of(None) == "all"
    assert p.band_of(0) == "all"
    assert p.band_of(999) == "all"


def test_multi_band_ranges_and_null_provenance():
    p = proj_mod.Project("x", make_module(BANDS=[("early", 1, 3), ("late", 4, 7)]),
                         Path("/tmp/x"))
    assert p.band_of(2) == "early"
    assert p.band_of(5) == "late"
    assert p.band_of(9) is None            # outside every band
    assert p.band_of(None) is None         # ambiguous with real bands


# --- tuning merge -----------------------------------------------------------

def test_tuning_overrides_merge_over_defaults():
    p = proj_mod.Project("x", make_module(TUNING={"MAX_SPEAKERS": 4}), Path("/tmp/x"))
    assert p.TUNING["MAX_SPEAKERS"] == 4                                   # override
    assert p.TUNING["KW_BONUS"] == proj_mod.DEFAULT_TUNING["KW_BONUS"]     # fallback
    assert p["MAX_SPEAKERS"] == 4                                          # item sugar


def test_no_tuning_uses_all_defaults():
    p = proj_mod.Project("x", make_module(), Path("/tmp/x"))
    assert p.TUNING == proj_mod.DEFAULT_TUNING


# --- label ------------------------------------------------------------------

def test_default_label_when_project_omits_one():
    p = proj_mod.Project("x", make_module(), Path("/tmp/x"))
    assert p.label(1, 2) == "g1i2"
    assert p.label(None, None) == "g?i?"


def test_project_supplied_label_wins():
    p = proj_mod.Project("x", make_module(label=lambda g, i: f"S{g}E{i}"), Path("/tmp/x"))
    assert p.label(3, 12) == "S3E12"


# --- load_project validation + resolution -----------------------------------

def _write_project(tmp_path, name, body):
    d = tmp_path / name
    d.mkdir()
    (d / "config.py").write_text(body)
    return d


def test_unknown_project_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(proj_mod, "PROJECTS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        proj_mod.load_project("does_not_exist")


def test_empty_roster_rejected(monkeypatch, tmp_path):
    _write_project(tmp_path, "p",
                   "def parse(f): return (None, None)\nCHARACTERS = []\nPOOLS = []\n")
    monkeypatch.setattr(proj_mod, "PROJECTS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        proj_mod.load_project("p")


def test_missing_required_attr_rejected(monkeypatch, tmp_path):
    _write_project(tmp_path, "p", "CHARACTERS = ['A']\nPOOLS = []\n")  # no parse
    monkeypatch.setattr(proj_mod, "PROJECTS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        proj_mod.load_project("p")


def test_load_project_happy_path(monkeypatch, tmp_path):
    _write_project(tmp_path, "p",
                   "def parse(f): return (1, 2)\n"
                   "CHARACTERS = ['A']\n"
                   "POOLS = [('x', 'X', None, 'k', 'd', ['e'])]\n")
    monkeypatch.setattr(proj_mod, "PROJECTS_DIR", tmp_path)
    p = proj_mod.load_project("p")
    assert p.CHARACTERS == ["A"]
    assert p.parse("anything") == (1, 2)
    assert p.workdir.name == "p"           # default workdir = work/<name>


def test_explicit_workdir_override(monkeypatch, tmp_path):
    _write_project(tmp_path, "p",
                   "def parse(f): return (1, 2)\nCHARACTERS=['A']\nPOOLS=[]\n")
    monkeypatch.setattr(proj_mod, "PROJECTS_DIR", tmp_path)
    p = proj_mod.load_project("p", workdir=str(tmp_path / "custom"))
    assert p.workdir == tmp_path / "custom"


# --- the real bundled projects ----------------------------------------------

def test_archer_wot_parse_and_label():
    p = proj_mod.load_project("archer_wot")
    assert p.parse("Archer (2009) - S03E12 - Blood Test.mkv") == (3, 12)
    assert p.parse("some.3x12.file.mkv") == (3, 12)
    assert p.parse("no_provenance_here.mkv") == (None, None)
    assert p.label(3, 12) == "S03E12"
    assert p.label(None, None) == "S??E??"
    assert len(p.BANDS) == 4


def test_template_has_empty_roster():
    # The template is intentionally not runnable until a roster is added.
    with pytest.raises(SystemExit):
        proj_mod.load_project("_template")
