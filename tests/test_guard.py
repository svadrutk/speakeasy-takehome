"""Unit tests for the hallucination guard (Component 5).

verify_narrative: extract numbers from LLM prose, compare to source_data.
  - Global-set comparison: each prose number must be close to SOME source
    number, in any plausible form (prob as 0.05 or 5.0, volume as 12500000
    or 12.5). Tolerance via math.isclose(rel_tol=0.02, abs_tol=0.05).
  - Known limitation #1: catches invented STATISTICS, not invented PROSE
    events that carry no numbers. Mitigated by the constrained system
    prompt (D18); logged in DECISIONS.md.
  - Known limitation #2: global-set can't catch field-conflation (volume
    12.5M misread as 12.5% prob). Logged as a v1 gap.

template_narrative: deterministic fallback. Same input -> same string.
  Handles every None field by omitting that clause (D17: narrative is
  always present and human-readable, even in failure).

source_data here is DISPLAY-FORMAT (floats) -- the same dict given to
generate_narrative. The endpoint (Component 6) converts internal basis
points -> float before calling either function (D9, D27).
"""

from main import _build_user_prompt, template_narrative, verify_narrative


# --- verify_narrative: pass cases ------------------------------------------


def test_verify_correct_numbers_pass():
    # LLM echoes 5.0% (prob), 5.2% (previous = prob - delta), 12.5M (volume).
    # All derivable from source within tolerance.
    data = {"current_prob": 0.05, "delta_1m": -0.002, "volume": 12500000}
    narrative = "Brazil's probability is 5.0%, down from 5.2%. Volume: 12.5M."
    assert verify_narrative(narrative, data) is True


def test_verify_no_numbers_pass():
    # Thin narrative with no numbers is allowed (system prompt: "if the facts
    # are thin, keep it short"). No numbers -> nothing to contradict.
    data = {"current_prob": 0.05, "delta_1m": None, "volume": None}
    narrative = "Brazil look steady heading into the match."
    assert verify_narrative(narrative, data) is True


def test_verify_rounding_tolerance_passes():
    # LLM rounds 0.0502 -> "5%". 5.0 vs 5.02 must be within tolerance.
    data = {"current_prob": 0.0502, "delta_1m": None, "volume": None}
    narrative = "Probability is 5%."
    assert verify_narrative(narrative, data) is True


def test_verify_all_none_source_numberless_prose_passes():
    # Kalshi returned garbage -> all fields None. LLM kept it prose-only.
    data = {"current_prob": None, "delta_1m": None, "volume": None}
    narrative = "Market data is thin right now."
    assert verify_narrative(narrative, data) is True


# --- verify_narrative: fail cases ------------------------------------------


def test_verify_wrong_number_fails():
    # The canonical hallucination: data says 5%, LLM says 34%.
    data = {"current_prob": 0.05, "delta_1m": None, "volume": None}
    narrative = "Brazil's probability is 34%."
    assert verify_narrative(narrative, data) is False


def test_verify_empty_narrative_fails():
    # Empty prose is a failure -> triggers template fallback (D17: narrative
    # is always present and human-readable). Must be special-cased BEFORE the
    # no-numbers rule, otherwise "" would vacuously pass.
    data = {"current_prob": 0.05, "delta_1m": None, "volume": None}
    assert verify_narrative("", data) is False


def test_verify_invented_statistic_fails():
    # LLM invents a score "3-0". 3 is not derivable from {0.05} -> fail.
    # (Known gap: a prose-only invention like "Brazil looked dominant" with
    # no numbers would NOT be caught -- see limitation #1 in the docstring.)
    data = {"current_prob": 0.05, "delta_1m": None, "volume": None}
    narrative = "Brazil won 3-0 against Argentina."
    assert verify_narrative(narrative, data) is False


def test_verify_all_none_source_numbered_prose_fails():
    # All source fields None -> allowed-number set is empty. Any number the
    # LLM emits has nothing to match against -> fail.
    data = {"current_prob": None, "delta_1m": None, "volume": None}
    narrative = "Probability is 5%."
    assert verify_narrative(narrative, data) is False


# --- template_narrative: deterministic fallback ----------------------------


def test_template_happy_path_exact():
    # Exact-match pins the format. previous = current - delta = 0.052.
    data = {"team": "Brazil", "current_prob": 0.05, "delta_1m": -0.002, "volume": 12500000}
    assert template_narrative(data) == (
        "Brazil's current probability is 5.0%, down from 5.2% previously. Volume: 12.5M."
    )


def test_template_none_prob_is_unavailable():
    # No probability -> "unavailable". Must never emit the literal "None".
    data = {"team": "Brazil", "current_prob": None, "delta_1m": None, "volume": None}
    result = template_narrative(data)
    assert "unavailable" in result
    assert "None" not in result


def test_template_none_delta_omits_up_down_clause():
    # Prob present, no delta -> show prob + volume, but NO "previously" clause.
    data = {"team": "Brazil", "current_prob": 0.05, "delta_1m": None, "volume": 12500000}
    result = template_narrative(data)
    assert "5.0%" in result
    assert "Volume:" in result
    assert "previously" not in result


def test_template_none_volume_omits_volume_clause():
    data = {"team": "Brazil", "current_prob": 0.05, "delta_1m": None, "volume": None}
    result = template_narrative(data)
    assert "Volume:" not in result


def test_template_positive_delta_says_up_from():
    # delta > 0 -> prob rose. previous = current - delta = 0.06 - 0.01 = 0.05 -> "5.0%".
    data = {"team": "Brazil", "current_prob": 0.06, "delta_1m": 0.01, "volume": None}
    result = template_narrative(data)
    assert "up from 5.0% previously" in result


def test_template_zero_prob_shows_zero_not_unavailable():
    # 0 is a real probability, not "unavailable".
    data = {"team": "Brazil", "current_prob": 0.0, "delta_1m": None, "volume": None}
    result = template_narrative(data)
    assert "0.0%" in result
    assert "unavailable" not in result


def test_template_is_deterministic():
    data = {"team": "Brazil", "current_prob": 0.05, "delta_1m": -0.002, "volume": 12500000}
    assert template_narrative(data) == template_narrative(data)


# --- _build_user_prompt: labeled prompt (D18/D29 fix) -----------------------
# Bug: the old `json.dumps(data)` sent a bare {"volume": 18147393} and the 8B
# model called it "18 million viewers" — confusing betting volume with audience.
# Fix: label each field, especially volume = dollars wagered (betting activity).
# None fields are OMITTED so there's no surface to hallucinate against.


def test_prompt_labels_volume_as_betting_activity():
    # The bug: bare int under "volume" -> LLM said "viewers". Fix: explicit label.
    data = {
        "team": "Japan",
        "opponent": "Sweden",
        "match_status": "ongoing",
        "current_prob": 0.38,
        "delta_1m": None,
        "volume": 18147393,
    }
    prompt = _build_user_prompt(data)
    assert "betting" in prompt.lower() or "wagered" in prompt.lower()
    assert "dollars" in prompt.lower()


def test_prompt_omits_volume_when_none():
    # No volume -> no volume line at all (remove the surface for hallucination).
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": 0.05,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "volume" not in prompt.lower()


def test_prompt_handles_none_opponent():
    # Tournament-winner (no opponent) -> graceful label, never the literal "None".
    data = {
        "team": "Brazil",
        "opponent": None,
        "match_status": None,
        "current_prob": 0.05,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "None" not in prompt
    assert "tournament" in prompt.lower() or "no specific opponent" in prompt.lower()


def test_prompt_handles_none_delta_cold_start():
    # delta None (cold start) -> cold-start wording, never literal "None".
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": 0.05,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "None" not in prompt
    assert "1 minute" in prompt or "history" in prompt


def test_prompt_handles_none_prob_defensive():
    # The endpoint won't call generate_narrative with None prob (uses template),
    # but _build_user_prompt must not crash if it does (defensive coding).
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": None,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "unavailable" in prompt
    assert "None" not in prompt


def test_prompt_delta_up_shows_direction_and_previous():
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": 0.06,
        "delta_1m": 0.01,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "up" in prompt
    assert "5.0%" in prompt  # prev = 0.06 - 0.01 = 0.05


def test_prompt_delta_down_shows_direction_and_previous():
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": 0.05,
        "delta_1m": -0.002,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "down" in prompt
    assert "5.2%" in prompt  # prev = 0.05 - (-0.002) = 0.052


def test_prompt_delta_zero_says_unchanged():
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": 0.05,
        "delta_1m": 0.0,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "unchanged" in prompt


def test_prompt_match_status_translated_to_game_timing():
    # Bug: "Match status: ongoing" -> LLM said "the prediction market is ongoing"
    # (conflating the market, tradeable for days, with the match). Fix: describe
    # the GAME's timing in plain English, labeled "Game timing" not "Match status".
    data = {
        "team": "Japan",
        "opponent": "Sweden",
        "match_status": "ongoing",
        "current_prob": 0.39,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "Game timing" in prompt
    assert "the match is today" in prompt
    # The ambiguous raw enum must not appear as a labeled status.
    assert "Match status: ongoing" not in prompt


def test_prompt_match_status_scheduled_is_future():
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "scheduled",
        "current_prob": 0.05,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "the match is in the future" in prompt


def test_prompt_match_status_closed_is_played():
    data = {
        "team": "Brazil",
        "opponent": "Scotland",
        "match_status": "closed",
        "current_prob": 0.05,
        "delta_1m": None,
        "volume": None,
    }
    prompt = _build_user_prompt(data)
    assert "the match has been played" in prompt
