"""Utterance segmentation — the corpus-shaping core of Stage 1."""

TUNING = {"MAX_WORD_GAP_S": 0.45, "MIN_UTTERANCE_S": 0.30, "MAX_UTTERANCE_S": 8.0}


def _result(words):
    return {"segments": [{"words": words}]}


def test_splits_on_speaker_change(index_mod):
    utts = index_mod.words_to_utterances(_result([
        {"word": "hi", "start": 0.0, "end": 0.4, "speaker": "S0"},
        {"word": "there", "start": 0.4, "end": 0.8, "speaker": "S0"},
        {"word": "who", "start": 0.85, "end": 1.3, "speaker": "S1"},
    ]), TUNING)
    assert len(utts) == 2
    assert utts[0]["speaker"] == "S0"
    assert utts[0]["text"] == "hi there"
    assert utts[1]["speaker"] == "S1"
    assert utts[1]["text"] == "who"


def test_splits_on_word_gap(index_mod):
    utts = index_mod.words_to_utterances(_result([
        {"word": "one", "start": 0.0, "end": 0.4, "speaker": "S0"},
        {"word": "two", "start": 2.0, "end": 2.4, "speaker": "S0"},  # gap 1.6 > 0.45
    ]), TUNING)
    assert len(utts) == 2


def test_min_length_filter_drops_too_short(index_mod):
    utts = index_mod.words_to_utterances(_result([
        {"word": "hm", "start": 0.0, "end": 0.1, "speaker": "S0"},  # 0.1 < 0.30
    ]), TUNING)
    assert utts == []


def test_max_length_forces_a_split(index_mod):
    words = [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.5, "speaker": "S0"}
             for i in range(12)]  # spans ~11.5s, no gaps > 0.45
    utts = index_mod.words_to_utterances(_result(words), TUNING)
    assert len(utts) >= 2
    assert all(u["duration"] <= TUNING["MAX_UTTERANCE_S"] + 1e-6 for u in utts)


def test_drops_words_with_missing_timestamps(index_mod):
    utts = index_mod.words_to_utterances(_result([
        {"word": "kept", "start": 0.0, "end": 0.4, "speaker": "S0"},
        {"word": "dropped", "start": None, "end": None, "speaker": "S0"},
        {"word": "kept2", "start": 0.4, "end": 0.8, "speaker": "S0"},
    ]), TUNING)
    assert len(utts) == 1
    assert utts[0]["text"] == "kept kept2"
    assert utts[0]["words"] == 2


def test_word_count_and_bounds(index_mod):
    utts = index_mod.words_to_utterances(_result([
        {"word": "a", "start": 0.0, "end": 0.3, "speaker": "S0"},
        {"word": "b", "start": 0.3, "end": 0.6, "speaker": "S0"},
    ]), TUNING)
    assert len(utts) == 1
    u = utts[0]
    assert u["words"] == 2
    assert u["start"] == 0.0 and u["end"] == 0.6
    assert abs(u["duration"] - 0.6) < 1e-9
