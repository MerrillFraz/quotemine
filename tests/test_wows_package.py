"""archer_wows state-routing: which audio the game actually plays.

`_path_matches`, `_build_mod_xml` and `_wems_in_manifest` are deterministic and
dependency-free (no ffmpeg, no GPU, no DB) but decide the whole deliverable — a
regression here surfaces only as the wrong voice line mid-battle, or as silence
where a pool was meant to be. Fixtures are inline XML, so these never touch the
real 2,264-line reference schema.
"""

import xml.etree.ElementTree as ET

import pytest


def _path(**states):
    """A <Path> with a <StateList> and an empty <FilesList>."""
    p = ET.Element("Path")
    sl = ET.SubElement(p, "StateList")
    for name, value in states.items():
        s = ET.SubElement(sl, "State")
        ET.SubElement(s, "Name").text = name
        ET.SubElement(s, "Value").text = value
    ET.SubElement(p, "FilesList")
    return p


# --- _path_matches -----------------------------------------------------------

def test_path_matches_empty_filter_matches_everything(wows_pkg_mod):
    p = _path(VO_Torpedo_Location="Port")
    assert wows_pkg_mod._path_matches(p, None) is True
    assert wows_pkg_mod._path_matches(p, {}) is True


def test_path_matches_single_state(wows_pkg_mod):
    p = _path(VO_Torpedo_Location="Port")
    assert wows_pkg_mod._path_matches(p, {"VO_Torpedo_Location": ["Port"]})
    assert not wows_pkg_mod._path_matches(p, {"VO_Torpedo_Location": ["Starboard"]})


def test_path_matches_accepts_any_allowed_value(wows_pkg_mod):
    p = _path(VO_Torpedo_Location="Bow")
    filt = {"VO_Torpedo_Location": ["Bow", "Stern"]}
    assert wows_pkg_mod._path_matches(p, filt)


def test_path_matches_requires_every_variable(wows_pkg_mod):
    # ALL pairs must hold, not any.
    p = _path(VO_Torpedo_Location="Port", VO_Plane_Status="Alive")
    assert wows_pkg_mod._path_matches(
        p, {"VO_Torpedo_Location": ["Port"], "VO_Plane_Status": ["Alive"]})
    assert not wows_pkg_mod._path_matches(
        p, {"VO_Torpedo_Location": ["Port"], "VO_Plane_Status": ["Dead"]})


def test_path_matches_missing_state_var_does_not_match(wows_pkg_mod):
    # A filter naming a variable the path doesn't carry must fail, not pass.
    p = _path(VO_Torpedo_Location="Port")
    assert not wows_pkg_mod._path_matches(p, {"VO_Plane_Status": ["Alive"]})


def test_path_matches_pathless_states_are_empty(wows_pkg_mod):
    p = ET.Element("Path")
    ET.SubElement(p, "FilesList")
    assert wows_pkg_mod._path_matches(p, None)
    assert not wows_pkg_mod._path_matches(p, {"VO_Anything": ["x"]})


# --- _build_mod_xml ----------------------------------------------------------

def _reference(tmp_path, event_name, paths):
    root = ET.Element("AudioModification.xml")
    am = ET.SubElement(root, "AudioModification")
    ev = ET.SubElement(am, "ExternalEvent")
    ET.SubElement(ev, "Name").text = event_name
    c = ET.SubElement(ev, "Container")
    for p in paths:
        c.append(p)
    out = tmp_path / "reference_mod.xml"
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    return out


@pytest.fixture
def build(wows_pkg_mod, tmp_path, monkeypatch):
    """_build_mod_xml against an inline reference schema."""
    def run(event_name, paths, event_routes):
        ref = _reference(tmp_path, event_name, paths)
        monkeypatch.setattr(wows_pkg_mod, "REFERENCE_MOD_XML", ref)
        out = tmp_path / "mod.xml"
        res = wows_pkg_mod._build_mod_xml(event_routes, out)
        return res, ET.parse(out).getroot()
    return run


def _files(root):
    """{state value: [wem names]} for each surviving Path."""
    got = {}
    for path in root.iter("Path"):
        key = path.findtext(".//State/Value")
        got[key] = [f.findtext("Name") for f in path.iter("File")]
    return got


def test_build_fills_matching_path_and_drops_the_rest(build):
    (written, slots, routed), root = build(
        "ev", [_path(Loc="Port"), _path(Loc="Starboard")],
        {"ev": [("pool_port", {"Loc": ["Port"]}, ["a.wem", "b.wem"])]})
    assert (written, slots) == (1, 1)
    assert routed == {"pool_port"}
    # The unmatched path is removed so the game falls back to its own voice.
    assert _files(root) == {"Port": ["a.wem", "b.wem"]}


def test_build_replaces_reference_files_rather_than_appending(build):
    p = _path(Loc="Port")
    fl = p.find("FilesList")
    stale = ET.SubElement(fl, "File")
    ET.SubElement(stale, "Name").text = "wargaming_original.wem"
    (_, slots, _), root = build("ev", [p],
                                {"ev": [("pool", {"Loc": ["Port"]}, ["ours.wem"])]})
    assert _files(root) == {"Port": ["ours.wem"]}


def test_build_skips_events_no_pool_covers(build):
    (written, slots, routed), root = build("ev", [_path(Loc="Port")], {})
    assert (written, slots, routed) == (0, 0, set())
    assert root.find(".//ExternalEvent") is None


def test_build_skips_event_when_no_path_matches(build):
    # Pool wired to the event, but its state value doesn't exist there.
    (written, slots, routed), root = build(
        "ev", [_path(Loc="Port")],
        {"ev": [("pool", {"Loc": ["Nonexistent"]}, ["a.wem"])]})
    assert (written, slots) == (0, 0)
    assert routed == set()          # so the caller can warn about it


def test_build_prefers_the_more_specific_route(build):
    # The regression this pins: route order comes from Stage 6 row order, so a
    # blanket (None-filter) pool listed FIRST would otherwise swallow the whole
    # event and the state-scoped pool would never place a clip.
    routes = [("blanket", None, ["blanket.wem"]),
              ("specific", {"Loc": ["Port"]}, ["specific.wem"])]
    (_, slots, routed), root = build(
        "ev", [_path(Loc="Port"), _path(Loc="Starboard")], {"ev": routes})
    assert _files(root) == {"Port": ["specific.wem"], "Starboard": ["blanket.wem"]}
    assert routed == {"blanket", "specific"}
    assert slots == 2


def test_build_specificity_is_independent_of_route_order(build):
    # Same expectation with the routes supplied the other way round.
    routes = [("specific", {"Loc": ["Port"]}, ["specific.wem"]),
              ("blanket", None, ["blanket.wem"])]
    _, root = build("ev", [_path(Loc="Port"), _path(Loc="Starboard")],
                    {"ev": routes})
    assert _files(root) == {"Port": ["specific.wem"], "Starboard": ["blanket.wem"]}


def test_build_reports_every_pool_that_placed_clips(build):
    routes = [("p1", {"Loc": ["Port"]}, ["a.wem"]),
              ("p2", {"Loc": ["Starboard"]}, ["b.wem"])]
    (_, slots, routed), _ = build(
        "ev", [_path(Loc="Port"), _path(Loc="Starboard")], {"ev": routes})
    assert routed == {"p1", "p2"} and slots == 2


# --- _why_stranded: the three causes need different fixes --------------------

def test_why_stranded_no_event_wired(wows_pkg_mod):
    msg = wows_pkg_mod._why_stranded("p", {}, {"ev"})
    assert "no game_event wired" in msg


def test_why_stranded_event_absent_from_reference(wows_pkg_mod):
    msg = wows_pkg_mod._why_stranded("p", {"p": ["typo_event"]}, {"ev"})
    assert "not in the reference schema" in msg and "typo_event" in msg


def test_why_stranded_falls_through_to_state_filter(wows_pkg_mod):
    msg = wows_pkg_mod._why_stranded("p", {"p": ["ev"]}, {"ev"})
    assert "state filter matched no <Path>" in msg


# --- _wems_in_manifest -------------------------------------------------------

def _mod_xml(tmp_path, names):
    root = ET.Element("AudioModification.xml")
    fl = ET.SubElement(ET.SubElement(root, "AudioModification"), "FilesList")
    for n in names:
        ET.SubElement(ET.SubElement(fl, "File"), "Name").text = n
    out = tmp_path / "mod.xml"
    ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
    return out


def test_wems_in_manifest_is_sorted_and_deduped(wowsmod_mod, tmp_path):
    p = _mod_xml(tmp_path, ["b.wem", "a.wem", "b.wem"])
    assert wowsmod_mod._wems_in_manifest(p) == ["a.wem", "b.wem"]


def test_wems_in_manifest_ignores_blank_names(wowsmod_mod, tmp_path):
    p = _mod_xml(tmp_path, ["a.wem", "", "   "])
    assert wowsmod_mod._wems_in_manifest(p) == ["a.wem"]


def test_wems_in_manifest_empty_when_no_files(wowsmod_mod, tmp_path):
    assert wowsmod_mod._wems_in_manifest(_mod_xml(tmp_path, [])) == []
