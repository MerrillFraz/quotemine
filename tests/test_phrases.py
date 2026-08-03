"""phrases.py pure-logic: wikitext parsing and corpus-rate attribution.

No network and no DB — `scrape` and the SQL in `probe` aren't exercised here.
These cover the parts that decide WHICH phrases and WHICH character end up in a
pack's POOL_FILTERS, which is where the tool is easiest to get quietly wrong.
"""


# --- strip_markup ------------------------------------------------------------

def test_strip_markup_unwraps_piped_links(phrases_mod):
    assert phrases_mod.strip_markup("[[Bort|the guest]] arrives") == "the guest arrives"


def test_strip_markup_unwraps_plain_links_and_bold(phrases_mod):
    assert phrases_mod.strip_markup("'''[[Phrasing]]''' - Archer") == "Phrasing - Archer"


def test_strip_markup_drops_refs_and_templates(phrases_mod):
    assert phrases_mod.strip_markup("Pam{{Clear}}<ref>a source</ref> speaks") == \
        "Pam speaks"


# --- normalize_phrase: variants must collapse to ONE key ---------------------

def test_normalize_phrase_collapses_case_and_punctuation(phrases_mod):
    # The prototype emitted "phrasing" three times because these differed.
    assert phrases_mod.normalize_phrase("Phrasing!") == "phrasing"
    assert phrases_mod.normalize_phrase("PHRASING") == "phrasing"
    assert phrases_mod.normalize_phrase("'''Phrasing'''") == "phrasing"


def test_normalize_phrase_keeps_internal_apostrophes(phrases_mod):
    assert phrases_mod.normalize_phrase("Can't have nice things!") == \
        "can't have nice things"


def test_normalize_phrase_collapses_whitespace(phrases_mod):
    assert phrases_mod.normalize_phrase("  danger   zone  ") == "danger zone"


# --- section: hand-edited wikis spell one heading many ways ------------------

WIKI = """
==Plot==
words
==Running Gags / Callbacks==
*'''Phrasing''' - Archer
*'''Get some!''' - Pam
==Quotes==
: '''Krieger: '''"The laws of robotics..."
:'''Archer''' ''(interrupting)'': "Made-up shit."
==Gallery==
pictures
"""


def test_section_extracts_named_body(phrases_mod):
    body = phrases_mod.section(WIKI, phrases_mod.GAGS_SECTION)
    assert "Get some!" in body


def test_section_stops_at_the_next_heading(phrases_mod):
    body = phrases_mod.section(WIKI, phrases_mod.GAGS_SECTION)
    assert "Krieger" not in body and "pictures" not in body


def test_section_matches_heading_spelling_variants(phrases_mod):
    # All of these occur on the real Archer wiki.
    for heading in ["Running Gags / Callbacks", "Running Gags/Callbacks",
                    "Running Gags", "[[Running Gags]] / Callbacks",
                    "Runnings Gags / Callbacks", "Callbacks"]:
        text = f"==Plot==\nx\n=={heading}==\n*'''Sploosh''' - Pam\n==End==\n"
        body = phrases_mod.section(text, phrases_mod.GAGS_SECTION)
        assert "Sploosh" in body, heading


def test_section_absent_returns_empty(phrases_mod):
    assert phrases_mod.section("==Plot==\nwords\n", phrases_mod.GAGS_SECTION) == ""


# --- mine_gags ---------------------------------------------------------------

ROSTER = ["Archer", "Lana", "Pam", "Cyril"]


def test_mine_gags_attributes_by_named_character(phrases_mod):
    gags = phrases_mod.mine_gags({"Ep1": WIKI}, ROSTER)
    assert gags["get some"]["Pam"] == 1
    assert gags["phrasing"]["Archer"] == 1


def test_mine_gags_counts_episodes_not_mentions(phrases_mod):
    # The canon signal is how many EPISODES cite a gag; a page listing it twice
    # must not inflate that.
    page = ("==Running Gags==\n*'''Phrasing''' - Archer\n"
            "*'''Phrasing''' - Archer again\n")
    gags = phrases_mod.mine_gags({"Ep1": page, "Ep2": page}, ROSTER)
    assert gags["phrasing"]["Archer"] == 2


def test_mine_gags_ignores_unattributed_bullets(phrases_mod):
    page = "==Running Gags==\n*'''Some gag''' - nobody in the roster\n"
    assert phrases_mod.mine_gags({"Ep1": page}, ROSTER) == {}


# --- mine_quotes: both speaker formats seen in the wild ----------------------

def test_mine_quotes_parses_colon_inside_and_outside_bold(phrases_mod):
    quotes = phrases_mod.mine_quotes({"Ep1": WIKI}, ROSTER + ["Krieger"])
    assert quotes["Krieger"] == ["The laws of robotics..."]
    assert quotes["Archer"] == ["Made-up shit."]


# --- attribute: rate, not raw count -----------------------------------------

def test_attribute_prefers_rate_over_raw_count(phrases_mod):
    # Archer speaks 42% of all lines, so he wins the raw count for "get some"
    # while Pam says it far more often relative to her screen time. Measured
    # from the real corpus: Archer 14 hits of 14446, Pam 7 of 3141.
    totals = {"Archer": 14446, "Pam": 3141}
    assert phrases_mod.attribute({"Archer": 14, "Pam": 7}, totals) == "Pam"


def test_attribute_agrees_with_raw_count_when_unambiguous(phrases_mod):
    totals = {"Archer": 14446, "Pam": 3141}
    assert phrases_mod.attribute({"Archer": 10, "Pam": 1}, totals) == "Archer"


def test_attribute_handles_a_sole_speaker(phrases_mod):
    assert phrases_mod.attribute({"Cyril": 4}, {"Cyril": 2504}) == "Cyril"


def test_attribute_of_nothing_is_none(phrases_mod):
    assert phrases_mod.attribute({}, {"Archer": 1}) is None
