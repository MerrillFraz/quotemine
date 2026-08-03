"""Stage 3 (match) pure-logic: the per-pool candidate window and keyword-bonus
knobs. No GPU/models — the embedding-heavy parts of cmd_match aren't exercised;
these cover the extracted helpers that implement the two knobs."""

import pytest


# --- embedding_window: union of the global window + every per-pool override ---

def test_embedding_window_no_overrides_is_global(match_mod):
    assert match_mod.embedding_window(0.4, 2.2, {}) == (0.4, 2.2)


def test_embedding_window_widens_to_cover_a_wide_pool(match_mod):
    # battle_start wants 3.0-7.5s; the union must reach up to 7.5 so those
    # longer lines get embedded (they're outside the 0.4-2.2 global window).
    lo, hi = match_mod.embedding_window(0.4, 2.2, {"battle_start": (3.0, 7.5)})
    assert (lo, hi) == (0.4, 7.5)


def test_embedding_window_spans_min_of_mins_and_max_of_maxes(match_mod):
    lo, hi = match_mod.embedding_window(
        0.4, 2.2, {"a": (0.2, 3.0), "b": (1.0, 7.5)})
    assert (lo, hi) == (0.2, 7.5)


# --- window_positions: restrict the ranked set to one pool's window ----------

def _ids_durs():
    # id -> duration; ids order is what window_positions indexes into.
    ids = [10, 11, 12, 13, 14]
    durs = {10: 0.5, 11: 1.5, 12: 2.2, 13: 4.0, 14: 6.5}
    return ids, durs


def test_window_positions_default_window_excludes_long_lines(match_mod):
    ids, durs = _ids_durs()
    pos = match_mod.window_positions(ids, durs, 0.4, 2.2)
    assert [ids[k] for k in pos] == [10, 11, 12]      # 4.0s / 6.5s excluded


def test_window_positions_wide_window_admits_the_long_line(match_mod):
    ids, durs = _ids_durs()
    pos = match_mod.window_positions(ids, durs, 3.0, 7.5)
    assert [ids[k] for k in pos] == [13, 14]          # only the long rally lines


def test_window_positions_bounds_are_inclusive(match_mod):
    ids, durs = _ids_durs()
    pos = match_mod.window_positions(ids, durs, 2.2, 4.0)
    assert [ids[k] for k in pos] == [12, 13]           # both endpoints included


# --- combined_score: per-pool keyword bonus floats terse gold ----------------

def test_combined_score_no_bonus_for_non_keyword(match_mod):
    assert match_mod.combined_score(0.44, False, 0.6) == 0.44


def test_combined_score_adds_bonus_for_keyword_hit(match_mod):
    assert match_mod.combined_score(0.20, True, 0.6) == 0.80


def test_large_pool_bonus_floats_kw_hit_above_better_semantic(match_mod):
    # you_penetrated case: a terse "Boom" (low sem, keyword hit) with a big
    # per-pool bonus should outrank a mediocre non-keyword semantic line.
    boom = match_mod.combined_score(0.20, True, 0.6)
    grazed = match_mod.combined_score(0.44, False, 0.6)
    assert boom > grazed


def test_small_global_bonus_leaves_kw_hit_below_better_semantic(match_mod):
    # With the global KW_BONUS, a filler keyword hit must NOT outrank a genuinely
    # better semantic match.
    kw_filler = match_mod.combined_score(0.20, True, 0.03)
    better_sem = match_mod.combined_score(0.44, False, 0.03)
    assert kw_filler < better_sem


def test_pool_kw_bonus_override_semantics(match_mod):
    # cmd_match selects the bonus via pool_kw_bonus.get(pool_id, KW_BONUS).
    pool_kw_bonus = {"you_penetrated": 0.6}
    assert pool_kw_bonus.get("you_penetrated", 0.03) == 0.6   # override wins
    assert pool_kw_bonus.get("target_lost", 0.03) == 0.03     # falls back global


# --- character filter: applied BEFORE the top-N cut --------------------------

def _ids_durs_chars():
    ids = [10, 11, 12, 13]
    durs = {10: 1.0, 11: 1.0, 12: 1.0, 13: 1.0}
    chars = {10: "Archer", 11: "Pam", 12: "Archer", 13: None}
    return ids, durs, chars


def test_window_positions_without_chars_is_unfiltered(match_mod):
    ids, durs, chars = _ids_durs_chars()
    assert match_mod.window_positions(ids, durs, 0.4, 2.2) == [0, 1, 2, 3]


def test_window_positions_restricts_to_named_characters(match_mod):
    ids, durs, chars = _ids_durs_chars()
    pos = match_mod.window_positions(ids, durs, 0.4, 2.2, ["Archer"], chars)
    assert [ids[k] for k in pos] == [10, 12]


def test_window_positions_char_filter_drops_unattributed(match_mod):
    ids, durs, chars = _ids_durs_chars()
    pos = match_mod.window_positions(ids, durs, 0.4, 2.2, ["Pam"], chars)
    assert [ids[k] for k in pos] == [11]


def test_window_positions_combines_duration_and_character(match_mod):
    ids = [10, 11, 12]
    durs = {10: 0.5, 11: 5.0, 12: 1.0}
    chars = {10: "Archer", 11: "Archer", 12: "Archer"}
    pos = match_mod.window_positions(ids, durs, 0.4, 2.2, ["Archer"], chars)
    assert [ids[k] for k in pos] == [10, 12]


# --- phrase queries: what plain keywords cannot express ----------------------

def test_fts_phrase_query_quotes_each_phrase_whole(match_mod):
    # The point of the feature: "danger zone" must stay ONE phrase. The keyword
    # path whitespace-splits, which would degrade it to "danger" OR "zone".
    assert match_mod.fts_phrase_query(["danger zone"]) == '"danger zone"'


def test_fts_phrase_query_ors_multiple_phrases(match_mod):
    assert match_mod.fts_phrase_query(["danger zone", "phrasing"]) == \
        '"danger zone" OR "phrasing"'


def test_fts_phrase_query_strips_fts_breaking_punctuation(match_mod):
    assert match_mod.fts_phrase_query(['he said "boom"!']) == '"he said boom"'


def test_fts_phrase_query_keeps_apostrophes(match_mod):
    assert match_mod.fts_phrase_query(["can't have nice things"]) == \
        '"can\'t have nice things"'


def test_fts_phrase_query_empty_is_falsy(match_mod):
    # An empty FTS5 MATCH is a syntax error, so callers must be able to skip.
    assert match_mod.fts_phrase_query([]) == ""
    assert match_mod.fts_phrase_query(None) == ""
    assert match_mod.fts_phrase_query(["", "  ", "!!"]) == ""


# --- embedding window folds in the phrase window when phrases are in play ----

def test_embedding_window_extra_widens_for_phrase_window(match_mod):
    lo, hi = match_mod.embedding_window(0.4, 2.2, {}, extra=[(0.4, 8.0)])
    assert (lo, hi) == (0.4, 8.0)


# --- combined_score: phrase bonus outranks the keyword bonus -----------------

def test_combined_score_phrase_hit_takes_phrase_bonus(match_mod):
    assert match_mod.combined_score(0.30, 0, 0.08, True, 0.6) == pytest.approx(0.90)


def test_combined_score_phrase_bonus_wins_when_line_is_both(match_mod):
    both = match_mod.combined_score(0.30, 1, 0.08, True, 0.6)
    kw_only = match_mod.combined_score(0.30, 1, 0.08, False, 0.6)
    assert both > kw_only
    assert both == pytest.approx(0.90)


def test_combined_score_unchanged_without_phrases(match_mod):
    # Existing projects pass no phrase args; behaviour must be identical.
    assert match_mod.combined_score(0.30, 1, 0.08) == \
        match_mod.combined_score(0.30, 1, 0.08, False, 0.6)
