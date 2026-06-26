"""Single source of truth for team mappings, thresholds, and env vars."""

from __future__ import annotations

import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# --- Team mappings ----------------------------------------------------------
# Each entry verified against Kalshi's live API (June 2026).
# Key: lowercase team name with underscores (user input is normalized to this).
# tw: tournament-winner suffix  -> KXMENWORLDCUP-26-{tw}
# pm: per-match suffix          -> searched in KXWCGAME event tickers
#
# Naming note: the spec originally called these code2/code3, assuming
# tournament-winner always uses 2-letter codes. It doesn't — 16 teams use
# 3-letter codes there (DZA, BIH, CPV, etc.). Renamed to tw/pm for accuracy.

TEAMS: dict[str, dict[str, str]] = {
    "algeria": {"tw": "DZA", "pm": "DZA"},
    "argentina": {"tw": "AR", "pm": "ARG"},
    "australia": {"tw": "AU", "pm": "AUS"},
    "austria": {"tw": "AT", "pm": "AUT"},
    "belgium": {"tw": "BE", "pm": "BEL"},
    "bosnia": {"tw": "BIH", "pm": "BIH"},
    "brazil": {"tw": "BR", "pm": "BRA"},
    "canada": {"tw": "CA", "pm": "CAN"},
    "cape_verde": {"tw": "CPV", "pm": "CPV"},
    "colombia": {"tw": "CO", "pm": "COL"},
    "congo_dr": {"tw": "COD", "pm": "COD"},
    "croatia": {"tw": "HR", "pm": "CRO"},
    "curacao": {"tw": "CUW", "pm": "CUW"},
    "czechia": {"tw": "CZE", "pm": "CZE"},
    "ecuador": {"tw": "EC", "pm": "ECU"},
    "egypt": {"tw": "EGY", "pm": "EGY"},
    "england": {"tw": "GB", "pm": "ENG"},
    "france": {"tw": "FR", "pm": "FRA"},
    "germany": {"tw": "DE", "pm": "GER"},
    "ghana": {"tw": "GH", "pm": "GHA"},
    "haiti": {"tw": "HTI", "pm": "HTI"},
    "iran": {"tw": "IR", "pm": "IRI"},
    "iraq": {"tw": "IRQ", "pm": "IRQ"},
    "ivory_coast": {"tw": "CIV", "pm": "CIV"},
    "japan": {"tw": "JP", "pm": "JPN"},
    "jordan": {"tw": "JOR", "pm": "JOR"},
    "mexico": {"tw": "MX", "pm": "MEX"},
    "morocco": {"tw": "MA", "pm": "MAR"},
    "netherlands": {"tw": "NL", "pm": "NED"},
    "new_zealand": {"tw": "NZL", "pm": "NZL"},
    "norway": {"tw": "NO", "pm": "NOR"},
    "panama": {"tw": "PAN", "pm": "PAN"},
    "paraguay": {"tw": "PY", "pm": "PAR"},
    "portugal": {"tw": "PT", "pm": "POR"},
    "qatar": {"tw": "QAT", "pm": "QAT"},
    "saudi_arabia": {"tw": "SA", "pm": "KSA"},
    "scotland": {"tw": "SC", "pm": "SCO"},
    "senegal": {"tw": "SN", "pm": "SEN"},
    "south_africa": {"tw": "RSA", "pm": "RSA"},
    "south_korea": {"tw": "KR", "pm": "KOR"},
    "spain": {"tw": "ES", "pm": "ESP"},
    "sweden": {"tw": "SE", "pm": "SWE"},
    "switzerland": {"tw": "CH", "pm": "SUI"},
    "tunisia": {"tw": "TN", "pm": "TUN"},
    "turkey": {"tw": "TR", "pm": "TUR"},
    "usa": {"tw": "US", "pm": "USA"},
    "uruguay": {"tw": "UY", "pm": "URU"},
    "uzbekistan": {"tw": "UZB", "pm": "UZB"},
}

# --- Thresholds -------------------------------------------------------------
DEFAULT_THRESHOLD: float = 0.20  # 20% relative delta -> notify (D5)
INTER_TEAM_DELAY_SECONDS: float = 0.30

# Per-team notification cooldown. Prevents oscillation spam: a market bouncing
# 500<->650 bp every poll would fire the gate every 30s without this. 5 min
# balances responsiveness (a real event in the 6th minute still fires) against
# noise. Configurable via env so an operator can tune without a code change.
COOLDOWN_SECONDS: int = int(os.environ.get("COOLDOWN_SECONDS", "300"))

# --- Env vars (with defaults) ----------------------------------------------
KALSHI_BASE_URL: str = os.environ.get(
    "KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"
)
CACHE_TTL_SECONDS: int = int(os.environ.get("CACHE_TTL_SECONDS", "30"))
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "meta-llama/llama-3.1-8b-instruct")


# --- Watch list (config file) ----------------------------------------------
# The watcher polls only these teams (SPEC.md: Watcher(teams)), not all 48.
# Loaded from a JSON file so per-team thresholds can be added later without
# a format change (AGENTS.md: 'threshold overridable per team'). Falls back
# to a default list if the file is missing or unreadable. Invalid entries
# (not in TEAMS) are warned on stderr and filtered out.
_WATCH_TEAMS_FILE = os.environ.get(
    "WATCH_TEAMS_FILE",
    os.path.join(os.path.dirname(__file__), "watch_teams.json"),
)


def _load_watch_teams(path: str) -> list[str]:
    """Load the watch list from a JSON file (flat list of team names).

    Missing file -> silent default (harmless first run). Malformed file or
    read error -> warn on stderr, use default. Invalid team names (not in
    TEAMS) -> warn on stderr, filter out.
    """
    default = ["brazil", "argentina", "usa", "germany", "france"]
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            print(f"WATCH_TEAMS: {path} is not a JSON list, using default", file=sys.stderr)
            raw = default
    except FileNotFoundError:
        raw = default
    except (json.JSONDecodeError, OSError) as e:
        print(f"WATCH_TEAMS: error reading {path}: {e}, using default", file=sys.stderr)
        raw = default
    teams = [str(t).strip().lower() for t in raw if str(t).strip()]
    valid: list[str] = []
    for t in teams:
        if t in TEAMS:
            valid.append(t)
        else:
            print(f"WATCH_TEAMS: '{t}' is not a known team, skipping", file=sys.stderr)
    return valid


WATCH_TEAMS: list[str] = _load_watch_teams(_WATCH_TEAMS_FILE)


def get_team(name: str) -> dict[str, str] | None:
    """Case-insensitive, whitespace-tolerant team lookup.
    'Brazil' -> TEAMS['brazil'], 'south korea' -> TEAMS['south_korea'].
    Returns None if not found."""
    normalized = name.strip().lower().replace(" ", "_").replace("-", "_")
    return TEAMS.get(normalized)


if __name__ == "__main__":
    # Quick smoke test: verify every TW ticker resolves on the live API.
    import httpx

    client = httpx.Client(timeout=10)
    ok, bad = 0, 0
    for team, codes in TEAMS.items():
        ticker = f"KXMENWORLDCUP-26-{codes['tw']}"
        r = client.get(f"{KALSHI_BASE_URL}/markets/{ticker}")
        if r.status_code == 200 and r.json().get("market", {}).get("ticker"):
            ok += 1
        else:
            bad += 1
            print(f"FAIL {team}: {ticker} -> {r.status_code}")
    client.close()
    print(f"\n{ok} OK, {bad} FAIL out of {len(TEAMS)} teams")
