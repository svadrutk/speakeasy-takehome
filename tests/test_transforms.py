"""Unit tests for data transforms (Component 3).

price_to_prob and compute_delta are the deterministic, load-bearing logic
most worth defending in review (D21). extract_market_fields has
characterization tests (implementation existed first; tests lock down the
D28 opponent-extraction logic and graceful-degradation contract).

Internal numeric type: integer basis points (0-10000), per D9 — no float
arithmetic anywhere. 5% = 500 bp. The endpoint converts bp -> float at the
JSON output boundary only.
"""

from datetime import date, datetime, timedelta, timezone

from main import compute_delta, extract_market_fields, price_to_prob


def test_price_to_prob_happy_path():
    assert price_to_prob("0.0500") == 500


def test_price_to_prob_none_input():
    assert price_to_prob(None) is None


def test_price_to_prob_empty_string():
    assert price_to_prob("") is None


def test_price_to_prob_zero():
    assert price_to_prob("0.0000") == 0


def test_price_to_prob_one_hundred_percent():
    assert price_to_prob("1.0000") == 10000


def test_price_to_prob_negative():
    assert price_to_prob("-0.05") is None


def test_price_to_prob_over_one():
    assert price_to_prob("1.5000") is None


def test_price_to_prob_garbage():
    assert price_to_prob("abc") is None


def test_price_to_prob_non_round_value():
    # The float-drift trap: int(float("0.0537") * 10000) can yield 536 or
    # 537.0000001 -> 537 depending on drift. Decimal-based parse is exact.
    assert price_to_prob("0.0537") == 537


# --- compute_delta ----------------------------------------------------------
# Window: list[(unix_seconds_float, prob_bp_int)], oldest->newest.
# Current sample is NOT in the window (caller appends it after).
# span = window[-1].ts - window[0].ts; if span >= 60s, return current - oldest.
# Returns int bp delta (sign preserved), or None if window < 1m / empty / invalid.


def test_compute_delta_happy_path():
    # Window spans 80s; oldest prob 3000 bp, current 3600 bp.
    window = [(1000.0, 3000), (1080.0, 3100)]
    assert compute_delta(window, 3600) == 600


def test_compute_delta_exactly_one_minute():
    # Boundary: span is exactly 60s. >= 60 must qualify (not return None).
    # A common bug: using > 60 instead of >= 60. This test catches it.
    window = [(1000.0, 3000), (1060.0, 3100)]
    assert compute_delta(window, 3600) == 600


def test_compute_delta_cold_start():
    # Window spans only 40s -> not enough history, return None.
    window = [(1000.0, 3000), (1040.0, 3100)]
    assert compute_delta(window, 3600) is None


def test_compute_delta_empty_window():
    assert compute_delta([], 3600) is None


def test_compute_delta_single_sample():
    # One sample can't span 1m (span = 0). Return None.
    window = [(1000.0, 3000)]
    assert compute_delta(window, 3600) is None


def test_compute_delta_zero_delta():
    # Prob unchanged over 1m. 0 is a valid delta, NOT None.
    window = [(1000.0, 3000), (1060.0, 3000)]
    assert compute_delta(window, 3000) == 0


def test_compute_delta_negative_delta():
    # Prob dropped over 1m. Sign must be preserved (negative delta).
    window = [(1000.0, 3600), (1060.0, 3500)]
    assert compute_delta(window, 3000) == -600


def test_compute_delta_none_current():
    # No current reading -> can't compute delta. Defensive None.
    window = [(1000.0, 3000), (1060.0, 3100)]
    assert compute_delta(window, None) is None


def test_compute_delta_none_window():
    # No window at all (e.g. endpoint hit before watcher ever ran). Defensive None.
    assert compute_delta(None, 3600) is None


# --- extract_market_fields --------------------------------------------------
# Characterization tests (D28): the implementation existed first; these lock
# down opponent extraction from the market title, status mapping, and the
# graceful-degradation contract (missing/garbage fields -> None, never crash).


def _per_match_market(**overrides) -> dict:
    """Build a sample KXWCGAME per-match market dict. Override fields via kwargs."""
    base = {
        "ticker": "KXWCGAME-26JUN24SCOBRA-BRA",
        "title": "Scotland vs Brazil Winner?",
        "yes_sub_title": "Brazil",
        "status": "active",
        "last_price_dollars": "0.5300",
        "volume_fp": "7000000",
        "liquidity_dollars": "5000",
        "updated_time": "2026-06-24T21:00:00Z",
    }
    base.update(overrides)
    return base


def test_extract_market_fields_none_market():
    # None market -> all-None dict (D17: degrade gracefully, never crash).
    result = extract_market_fields(None)
    assert result == {
        "opponent": None,
        "match_status": None,
        "market": None,
        "current_prob": None,
        "volume": None,
        "liquidity": None,
        "last_updated": None,
    }


def test_extract_market_fields_per_match_happy_path():
    # Per-match: opponent from title, status active -> scheduled, all fields populated.
    result = extract_market_fields(_per_match_market())
    assert result["opponent"] == "scotland"
    assert result["match_status"] == "scheduled"
    assert result["market"] == "KXWCGAME-26JUN24SCOBRA-BRA"
    assert result["current_prob"] == 5300
    assert result["volume"] == 7000000
    assert result["liquidity"] == 5000
    assert result["last_updated"] == "2026-06-24T21:00:00Z"


def test_extract_market_fields_tournament_winner():
    # Tournament-winner: no " vs " in title -> opponent None, match_status None.
    market = {
        "ticker": "KXMENWORLDCUP-26-BR",
        "title": "Will the Brazil win the 2026 Men's World Cup?",
        "yes_sub_title": "Brazil",
        "status": "active",
        "last_price_dollars": "0.0500",
    }
    result = extract_market_fields(market)
    assert result["opponent"] is None
    assert result["match_status"] is None
    assert result["current_prob"] == 500


def test_extract_market_fields_missing_fields_no_crash():
    # Only a ticker present -> .get() returns None for the rest, no KeyError.
    result = extract_market_fields({"ticker": "SOME-TICKER"})
    assert result["opponent"] is None
    assert result["match_status"] is None
    assert result["market"] == "SOME-TICKER"
    assert result["current_prob"] is None
    assert result["volume"] is None
    assert result["liquidity"] is None
    assert result["last_updated"] is None


def test_extract_market_fields_status_finalized_is_closed():
    # Per-match with status "finalized" -> match_status "closed".
    result = extract_market_fields(_per_match_market(status="finalized"))
    assert result["match_status"] == "closed"


def test_extract_market_fields_opponent_team_first_in_title():
    # Our team is on the LEFT of " vs " -> opponent is on the right.
    result = extract_market_fields(
        _per_match_market(title="Brazil vs Scotland Winner?", yes_sub_title="Brazil")
    )
    assert result["opponent"] == "scotland"


def test_extract_market_fields_opponent_ticker_based():
    # Opponent extracted from ticker, not title (structured 3-letter codes).
    # Ticker suffix=MAR (our team=Morocco), middle 6 chars contain both codes.
    result = extract_market_fields(
        _per_match_market(
            ticker="KXWCGAME-26JUN29NEDMAR-MAR",
            title="Netherlands vs Morocco Winner?",
            yes_sub_title="Reg Time: Morocco",
        ),
        team_name="Morocco",
    )
    assert result["opponent"] == "netherlands"
    assert result["match_status"] == "scheduled"


def test_extract_market_fields_empty_dict_no_crash():
    # Empty dict (e.g. Kalshi returned 200 with empty body) -> all None, no crash.
    result = extract_market_fields({})
    assert result["opponent"] is None
    assert result["match_status"] is None
    assert result["market"] is None
    assert result["current_prob"] is None
    assert result["volume"] is None
    assert result["liquidity"] is None
    assert result["last_updated"] is None


def test_extract_market_fields_garbage_price_is_none():
    # Garbage in last_price_dollars -> price_to_prob returns None, propagates (D27).
    result = extract_market_fields(_per_match_market(last_price_dollars="abc"))
    assert result["current_prob"] is None


# --- match_status: date-based (D26) -----------------------------------------
# Kalshi's 'active' status is set for EVERY KXWCGAME market until settled, so
# it can't tell scheduled from ongoing from past. The parsed match_date
# (threaded from fetch_per_match_market) disambiguates. Tests build match_date
# relative to date.today() so they're correct any day they run.


def test_match_status_future_is_scheduled():
    # match_date tomorrow, status active -> scheduled (not yet played).
    tomorrow = date.today() + timedelta(days=1)
    result = extract_market_fields(_per_match_market(), match_date=tomorrow)
    assert result["match_status"] == "scheduled"


def test_match_status_today_is_ongoing():
    # match_date today, status active -> ongoing (D26: 'active' can't tell, date can).
    # Known imprecision: we have the match DATE, not kickoff TIME, so "ongoing"
    # for today is a best-guess (could be an 8pm kickoff not yet started). Logged
    # in DECISIONS; the real fix needs a kickoff timestamp Kalshi doesn't expose.
    today = date.today()
    result = extract_market_fields(_per_match_market(), match_date=today)
    assert result["match_status"] == "ongoing"


def test_match_status_past_is_closed():
    # match_date yesterday, status still active (Kalshi keeps active until settled)
    # -> closed (the match has been played). Conflation: "closed" here means
    # "sporting result known", not "market settled" — same conflation D26 flagged.
    yesterday = date.today() - timedelta(days=1)
    result = extract_market_fields(_per_match_market(), match_date=yesterday)
    assert result["match_status"] == "closed"


def test_match_status_settled_overrides_date():
    # status finalized beats date: a finalized market is closed even on match day.
    today = date.today()
    result = extract_market_fields(_per_match_market(status="finalized"), match_date=today)
    assert result["match_status"] == "closed"


def test_match_status_no_date_falls_back_to_active_scheduled():
    # No match_date threaded (direct caller, or tournament-winner path) -> old
    # 'active' -> 'scheduled' behavior. Backward-compat: callers that don't pass
    # match_date still get the pre-D26-fix behavior rather than a crash.
    result = extract_market_fields(_per_match_market())
    assert result["match_status"] == "scheduled"


def test_match_status_tournament_winner_ignores_date():
    # Tournament-winner has no opponent -> match_status None even if a date is
    # passed. match_status is only meaningful for per-match markets.
    market = {
        "ticker": "KXMENWORLDCUP-26-BR",
        "title": "Will the Brazil win the 2026 Men's World Cup?",
        "yes_sub_title": "Brazil",
        "status": "active",
        "last_price_dollars": "0.0500",
    }
    result = extract_market_fields(market, match_date=date.today())
    assert result["match_status"] is None


# --- match_status: occurrence_datetime (D26 fix, primary path) --------------
# Kalshi sets `occurrence_datetime` (exact UTC kickoff) on every per-match
# market. It supersedes the ticker-string date: the ticker embeds the LOCAL
# matchday, so a late-ET kickoff crosses the UTC date boundary and the day-
# grained calc mislabels it. occurrence_datetime is UTC + minute-precise.
# 30m pre-kickoff grace: flip scheduled->ongoing 30m before kickoff.


def _now_utc():
    return datetime.now(timezone.utc)


def test_match_status_occurrence_far_future_is_scheduled():
    # Kickoff 5h out -> well past the 30m grace -> scheduled.
    occ = _now_utc() + timedelta(hours=5)
    result = extract_market_fields(_per_match_market(occurrence_datetime=occ.isoformat()))
    assert result["match_status"] == "scheduled"


def test_match_status_occurrence_just_outside_grace_is_scheduled():
    # Kickoff 31m out -> just past the 30m grace boundary -> scheduled.
    occ = _now_utc() + timedelta(minutes=31)
    result = extract_market_fields(_per_match_market(occurrence_datetime=occ.isoformat()))
    assert result["match_status"] == "scheduled"


def test_match_status_occurrence_inside_grace_is_ongoing():
    # Kickoff 10m out -> within the 30m grace -> ongoing (imminent kickoff).
    occ = _now_utc() + timedelta(minutes=10)
    result = extract_market_fields(_per_match_market(occurrence_datetime=occ.isoformat()))
    assert result["match_status"] == "ongoing"


def test_match_status_occurrence_just_past_kickoff_is_ongoing():
    # Kickoff 5m ago, still active (not finalized) -> ongoing (in play).
    occ = _now_utc() - timedelta(minutes=5)
    result = extract_market_fields(_per_match_market(occurrence_datetime=occ.isoformat()))
    assert result["match_status"] == "ongoing"


def test_match_status_occurrence_settled_overrides_time():
    # status finalized beats occurrence: a finalized market is closed even in
    # the ongoing window. Settlement is authoritative.
    occ = _now_utc() - timedelta(minutes=5)
    result = extract_market_fields(
        _per_match_market(status="finalized", occurrence_datetime=occ.isoformat())
    )
    assert result["match_status"] == "closed"


def test_match_status_occurrence_garbage_falls_back_to_date():
    # Garbage occurrence_datetime -> don't crash, fall back to match_date path.
    result = extract_market_fields(
        _per_match_market(occurrence_datetime="not-a-timestamp"),
        match_date=date.today() + timedelta(days=1),
    )
    assert result["match_status"] == "scheduled"


def test_match_status_occurrence_empty_falls_back_to_date():
    # No occurrence_datetime at all -> match_date fallback (D26 legacy path).
    result = extract_market_fields(
        _per_match_market(),
        match_date=date.today() + timedelta(days=1),
    )
    assert result["match_status"] == "scheduled"


def test_match_status_occurrence_none_and_no_date_falls_back_to_active():
    # No occurrence, no date, status active -> scheduled (last-resort fallback).
    result = extract_market_fields(_per_match_market())
    assert result["match_status"] == "scheduled"
