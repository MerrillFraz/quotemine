"""Shared test fixtures.

Puts pipeline/ on sys.path so `import project` works, and provides a loader for
the digit-prefixed stage modules (01_index.py etc.), which can't be imported by
name. These tests cover only the pure-logic core — no GPU, models, or audio — so
loading the stage modules is safe (heavy deps are lazy-imported inside functions).
"""

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "pipeline"
sys.path.insert(0, str(PIPELINE))

import pytest


def _load(relpath, name):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def index_mod():
    return _load("pipeline/01_index.py", "index_stage")


@pytest.fixture(scope="session")
def identify_mod():
    return _load("pipeline/02_identify.py", "identify_stage")


@pytest.fixture(scope="session")
def match_mod():
    return _load("pipeline/03_match.py", "match_stage")


@pytest.fixture(scope="session")
def audition_mod():
    return _load("pipeline/04_audition.py", "audition_stage")


@pytest.fixture(scope="session")
def package_mod():
    return _load("pipeline/06_package.py", "package_stage")


@pytest.fixture(scope="session")
def wows_pkg_mod():
    """projects/archer_wows/package.py — the state-routing hook."""
    return _load("projects/archer_wows/package.py", "wows_package")


@pytest.fixture(scope="session")
def wowsmod_mod():
    return _load("pipeline/build_wowsmod.py", "build_wowsmod")
