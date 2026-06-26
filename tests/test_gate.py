"""Unit tests for the notification gate (Component 9).

should_notify(current_bp, previous_bp, threshold) -> bool is THE deterministic
gate (D5): a relative-probability-delta threshold decides WHEN to notify. Pure
function, no I/O, unit-testable without an LLM. Chosen over LLM-as-gatekeeper
(untestable, can flood or go silent) and absolute-delta (dumb across the
range -- 5%->10% is huge, 80%->85% is noise; relative fixes that).

Inputs are INTEGER BASIS POINTS (D27: no float arithmetic in the pipeline).
5% = 500 bp. current/previous are int 0..10000 or None (cold start / data
blip). The threshold stays a float (0.20) -- the comparison is
`abs(cur - prev) >= thr * prev`, a single float multiply on the RHS with an
int on the LHS. Not drift-prone like money subtraction (D27 rationale).

prev=0 handling (the div-by-zero case): prev=0, cur>0 -> True (any nonzero
move from 0% is a qualitative shift in a prediction market -- "they're on the
board"); prev=0, cur=0 -> False. Rejected absolute-delta-fallback (magic
number to defend) and treat-as-cold-start (0%->50% would never notify).

Negative bp is NOT tested: price_to_prob (D27) rejects out-of-range -> None,
so the gate sees None, never negative bp. The gate trusts its caller.
"""

from watcher.core import should_notify


# --- Happy path --------------------------------------------------------------


def test_delta_exceeds_threshold():
    # prev=500bp (5%), cur=650bp (6.5%), thr=0.20 -> rel delta 0.30 >= 0.20.
    assert should_notify(650, 500, 0.20) is True


# --- Below / zero / duplicate -----------------------------------------------


def test_delta_below_threshold():
    # prev=500bp, cur=550bp, thr=0.20 -> rel delta 0.10 < 0.20.
    assert should_notify(550, 500, 0.20) is False


def test_zero_delta():
    # prev=cur -> 0 movement. Duplicate reading, no news.
    assert should_notify(500, 500, 0.20) is False


# --- Boundary (SPEC-named: exactly at threshold) ----------------------------


def test_exactly_at_threshold_inclusive():
    # prev=500bp, cur=600bp, thr=0.20 -> rel delta exactly 0.20. `>=` inclusive.
    # A `>` implementation would fail here and silently miss the exact case.
    assert should_notify(600, 500, 0.20) is True


# --- Negative delta (a drop is also news) -----------------------------------


def test_negative_delta_exceeds_threshold():
    # prev=5000bp (50%), cur=3500bp (35%), thr=0.20 -> rel delta 0.30. A fall
    # is as newsworthy as a rise.
    assert should_notify(3500, 5000, 0.20) is True


# --- None inputs (cold start / data blip) -----------------------------------


def test_both_none_cold_start():
    # No data at all -> no baseline -> no notify.
    assert should_notify(None, None, 0.20) is False


def test_previous_none_first_reading():
    # First reading: current exists but no previous to compare -> no notify.
    assert should_notify(500, None, 0.20) is False


def test_current_none_data_blip():
    # Previous exists but current missing -> can't notify on no current data.
    assert should_notify(None, 500, 0.20) is False


# --- Division by zero (prev=0) ----------------------------------------------


def test_prev_zero_current_nonzero():
    # prev=0%, cur=1% -> any nonzero move from zero is a qualitative shift.
    assert should_notify(100, 0, 0.20) is True


def test_prev_zero_current_zero():
    # prev=0%, cur=0% -> no movement.
    assert should_notify(0, 0, 0.20) is False


# --- Per-team override threshold (D6) ---------------------------------------


def test_lower_threshold_fires_where_default_wouldnt():
    # prev=500bp, cur=550bp, thr=0.05 -> 0.10 >= 0.05 -> True (False at 0.20).
    assert should_notify(550, 500, 0.05) is True


def test_higher_threshold_suppresses_where_default_would_fire():
    # prev=500bp, cur=600bp, thr=0.30 -> 0.20 < 0.30 -> False (True at 0.20).
    assert should_notify(600, 500, 0.30) is False


# --- The D5 rationale (relative > absolute -- the reviewer probe) -----------


def test_high_prob_small_absolute_move_is_noise():
    # prev=8000bp (80%), cur=8400bp (84%), thr=0.20 -> rel delta 0.05 < 0.20.
    # +4pp absolute is noise at the top of the range. THIS is why relative.
    assert should_notify(8400, 8000, 0.20) is False


def test_high_prob_large_relative_move_fires():
    # prev=8000bp (80%), cur=9600bp (96%), thr=0.20 -> rel delta 0.20 >= 0.20.
    # Same neighborhood as #13 but a real swing crosses the gate.
    assert should_notify(9600, 8000, 0.20) is True


# --- Scale / extreme bp -----------------------------------------------------


def test_collapse_to_zero_fires():
    # prev=9999bp (99.99%), cur=0bp -> rel delta 1.0. A collapse is news.
    assert should_notify(0, 9999, 0.20) is True


def test_top_of_range_no_movement():
    # prev=9999bp, cur=9999bp -> no movement at the ceiling.
    assert should_notify(9999, 9999, 0.20) is False
