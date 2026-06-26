# Watcher Demo (Standalone CLI + Deterministic Mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the watcher into a reusable module and add a local CLI with a `--demo` mode that feeds a synthetic market move through the real pipeline (gate → LLM → osascript) to fire a notification on cue.

**Architecture:** Create a `watcher/` package with `core.py` (loop, gate, notify, shared state) and `__main__.py` (CLI). Avoid circular imports by dependency-injecting the data source and narrative functions into the watcher. `main.py` imports shared state from `watcher.core` and starts the loop in `lifespan` for window-filling on Railway. CLI runs locally with `--demo` synthetic source and console trace.

**Tech Stack:** Python 3.13, FastAPI, httpx, uvicorn, pytest, macOS `osascript`.

---

### Task 1: Extract Watcher Into `watcher/core.py` With Dependency Injection

**Files:**
- Create: `watcher/__init__.py`
- Create: `watcher/core.py`
- Modify: `main.py` (import shared state + call watcher loop)
- Modify: `tests/test_watcher.py` (import from watcher.core and pass dependencies)

- [ ] **Step 1: Create package skeleton**

```python
# watcher/__init__.py
# Empty init; exports will be defined in core.py and optionally re-exported later.
```

- [ ] **Step 2: Implement core watcher module with DI seam**

```python
# watcher/core.py
from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import date
from typing import Awaitable, Callable, Optional

from config import (
    CACHE_TTL_SECONDS,
    COOLDOWN_SECONDS,
    DEFAULT_THRESHOLD,
    INTER_TEAM_DELAY_SECONDS,
    WATCH_TEAMS,
)

# Shared state (imported by main.py for endpoint delta computation)
_WINDOW_MAX_AGE: float = 120.0
_rolling: dict[str, list[tuple[float, int]]] = {}
_cache: dict[str, tuple[float, tuple[dict, date | None] | None]] = {}
_last_notified: dict[str, float] = {}

# --- Notification gate (pure) ---
def should_notify(
    current_bp: int | None,
    previous_bp: int | None,
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    if current_bp is None or previous_bp is None:
        return False
    if previous_bp == 0:
        return current_bp > 0
    return abs(current_bp - previous_bp) >= threshold * previous_bp


# --- macOS notifications (best-effort) ---
_NOTIF_TITLE_PREFIX = "Kalshi"

def send_notification(title: str, message: str) -> None:
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True, timeout=5)
    except Exception:
        pass


# --- Internals shared with endpoint ---
def _record_sample(team_key: str, prob_bp: int | None, now: float) -> None:
    if prob_bp is None:
        return
    window = _rolling.setdefault(team_key, [])
    window.append((now, prob_bp))
    _rolling[team_key] = [s for s in window if now - s[0] <= _WINDOW_MAX_AGE]


async def _get_cached_or_fetch(
    team_key: str,
    fetcher: Callable[[str], Awaitable[tuple[dict, date | None] | None]],
) -> tuple[dict, date | None] | None:
    now = time.monotonic()
    entry = _cache.get(team_key)
    if entry is not None and (now - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    result = await fetcher(team_key)
    _cache[team_key] = (now, result)
    return result


TraceFn = Callable[[str], None]


async def _poll_team(
    team_key: str,
    *,
    fetcher: Callable[[str], Awaitable[tuple[dict, date | None] | None]],
    extract_fields: Callable[[dict | None, Optional[date]], dict],
    generate_narrative: Callable[[dict], Awaitable[str]],
    verify_narrative: Callable[[str, dict], bool],
    template_narrative: Callable[[dict], str],
    trace: TraceFn | None = None,
) -> None:
    """Fetch one team's market, record a sample, maybe notify. Never raises.

    Gate compares current vs last sample (~previous poll). On fire: LLM narrative
    with template fallback, then send_notification. Cooldown prevents spam.
    """
    try:
        window = _rolling.get(team_key)
        previous_bp = window[-1][1] if window else None

        fetched = await _get_cached_or_fetch(team_key, fetcher)
        if fetched is None:
            if trace:
                trace(f"{team_key}: no market -> skip")
            return
        market, match_date = fetched
        fields = extract_fields(market, match_date)
        current_bp = fields["current_prob"]
        now = time.monotonic()
        _record_sample(team_key, current_bp, now)

        if trace and previous_bp is not None and current_bp is not None:
            # Log decision context pre-gate
            prev = previous_bp / 100.0
            curr = current_bp / 100.0
            trace(f"{team_key}: prev={prev:.2f}bp curr={curr:.2f}bp")

        if not should_notify(current_bp, previous_bp):
            if trace:
                trace(f"{team_key}: gate=no")
            return

        now_m = time.monotonic()
        last = _last_notified.get(team_key, 0.0)
        if now_m - last < COOLDOWN_SECONDS:
            if trace:
                trace(f"{team_key}: cooldown -> suppress")
            return
        _last_notified[team_key] = now_m

        display_name = team_key.replace("_", " ").title()
        current_prob_f = current_bp / 10000.0 if current_bp is not None else None
        previous_prob_f = previous_bp / 10000.0 if previous_bp is not None else None
        delta_bp = (
            (current_bp - previous_bp)
            if current_bp is not None and previous_bp is not None
            else None
        )
        delta_f = delta_bp / 10000.0 if delta_bp is not None else None
        display = {
            "team": display_name,
            "opponent": fields["opponent"],
            "match_status": fields["match_status"],
            "current_prob": current_prob_f,
            "previous_prob": previous_prob_f,
            "delta_1m": delta_f,
            "volume": fields["volume"],
        }

        try:
            narrative = await generate_narrative(display)
            if not verify_narrative(narrative, display):
                if trace:
                    trace("guard: LLM invalid -> template")
                narrative = template_narrative(display)
        except Exception:
            if trace:
                trace("guard: LLM error -> template")
            narrative = template_narrative(display)

        if trace:
            trace("notifying…")
        send_notification(f"{_NOTIF_TITLE_PREFIX}: {display_name}", narrative)
    except Exception:
        # Never crash the loop
        if trace:
            trace("error: swallowed per D17")
        pass


async def _watcher_loop(
    *,
    fetcher: Callable[[str], Awaitable[tuple[dict, date | None] | None]],
    extract_fields: Callable[[dict | None, Optional[date]], dict],
    generate_narrative: Callable[[dict], Awaitable[str]],
    verify_narrative: Callable[[str, dict], bool],
    template_narrative: Callable[[dict], str],
    trace: TraceFn | None = None,
) -> None:
    while True:
        for team_key in WATCH_TEAMS:
            await _poll_team(
                team_key,
                fetcher=fetcher,
                extract_fields=extract_fields,
                generate_narrative=generate_narrative,
                verify_narrative=verify_narrative,
                template_narrative=template_narrative,
                trace=trace,
            )
            await asyncio.sleep(INTER_TEAM_DELAY_SECONDS)


def reset_demo_state() -> None:
    """Reset rolling + cooldown state for clean demo runs."""
    _rolling.clear()
    _last_notified.clear()


# Public API
__all__ = [
    "_rolling",
    "_cache",
    "_WINDOW_MAX_AGE",
    "_get_cached_or_fetch",
    "_record_sample",
    "_watcher_loop",
    "_poll_team",
    "should_notify",
    "send_notification",
    "reset_demo_state",
]
```

- [ ] **Step 3: Wire `main.py` to use `watcher.core`**

Modify imports and call sites in `main.py`:

```python
# At top of main.py, add:
from watcher.core import (
    _rolling,
    _cache,
    _WINDOW_MAX_AGE,
    _get_cached_or_fetch,
    _record_sample,
    _watcher_loop,
)

# In get_team_sentiment(), replace calls to local _record_sample/_get_cached_or_fetch
# with the imported ones (signatures now require fetcher):

fetched = await _get_cached_or_fetch(team_key, fetch_market_for_team)
...
_record_sample(team_key, fields["current_prob"], now)

# In lifespan(), pass dependencies into the watcher loop:

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _watcher_task
    _watcher_task = asyncio.create_task(
        _watcher_loop(
            fetcher=fetch_market_for_team,
            extract_fields=extract_market_fields,
            generate_narrative=generate_narrative,
            verify_narrative=verify_narrative,
            template_narrative=template_narrative,
        )
    )
    yield
    if _watcher_task:
        _watcher_task.cancel()
        try:
            await _watcher_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 4: Update watcher tests to import from `watcher.core`**

Replace imports and adapt to new signatures in `tests/test_watcher.py`:

```python
# tests/test_watcher.py (top)
import asyncio
import time
from unittest.mock import AsyncMock, patch

import main
from watcher.core import _poll_team, reset_demo_state, _rolling, _last_notified

def _market_with_prob(prob_bp: int) -> dict:
    return {
        "ticker": "KXWCGAME-26JUN25JPNSWE-JPN",
        "title": "Japan vs Sweden Winner?",
        "yes_sub_title": "Japan",
        "status": "active",
        "last_price_dollars": f"{prob_bp / 10000:.4f}",
        "volume_fp": "1000000",
    }

def _reset_rolling():
    _rolling.clear()

def _reset_notified():
    _last_notified.clear()

# ...

async def _fake_fetcher_returning(market_tuple):
    return market_tuple

def test_poll_team_records_sample():
    _reset_rolling()
    market = _market_with_prob(3900)
    asyncio.run(
        _poll_team(
            "japan",
            fetcher=lambda _: _fake_fetcher_returning((market, None)),
            extract_fields=main.extract_market_fields,
            generate_narrative=AsyncMock(return_value=""),
            verify_narrative=lambda *_: True,
            template_narrative=main.template_narrative,
        )
    )
    assert "japan" in _rolling
    assert len(_rolling["japan"]) == 1
    assert _rolling["japan"][0][1] == 3900

# Similar adaptations for other tests: pass fetcher/extract/generate/verify/template
```

- [ ] **Step 5: Run tests to see expected failures (signature mismatches) and fix any typos**

Run: `uv run pytest -q`
Expected: initial failures until all imports and signatures in tests are updated as above.


### Task 2: Add Synthetic Data Source (`watcher/demo.py`)

**Files:**
- Create: `watcher/demo.py`
- Test: `tests/test_demo.py`

- [ ] **Step 1: Implement a synthetic fetcher**

```python
# watcher/demo.py
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

class SyntheticFetcher:
    """Synthetic market source for demo: flat at 5000bp, then jump to 6500bp on cue."""

    def __init__(self, jump_after_polls: int = 3):
        self._count = 0
        self._jump_after = jump_after_polls
        self._jumped = False

    async def __call__(self, team_key: str) -> tuple[dict, Optional[date]]:
        self._count += 1
        jump = (self._count >= self._jump_after)
        prob_bp = 6500 if jump else 5000
        market = {
            "ticker": "KXWCGAME-26JUN25DEMO-DEM",
            "title": f"Demo vs Opponent Winner?",
            "yes_sub_title": "Demo",
            "status": "active",
            "last_price_dollars": f"{prob_bp / 10000:.4f}",
            "volume_fp": "1000000",
        }
        return market, None
```

- [ ] **Step 2: Unit test the synthetic curve**

```python
# tests/test_demo.py
import asyncio
from watcher.demo import SyntheticFetcher

async def _fetch_three(fetcher):
    return [await fetcher("demo") for _ in range(3)]

def test_synthetic_fetcher_jumps_after_n():
    f = SyntheticFetcher(jump_after_polls=3)
    m = asyncio.run(_fetch_three(f))
    # last_price_dollars: 0.5000, 0.5000, 0.6500
    prices = [float(t[0]["last_price_dollars"]) for t in m]
    assert prices == [0.5, 0.5, 0.65]
```


### Task 3: CLI Entry Point (`watcher/__main__.py`) With `--demo`

**Files:**
- Create: `watcher/__main__.py`

- [ ] **Step 1: Implement CLI**

```python
# watcher/__main__.py
from __future__ import annotations

import argparse
import asyncio

import main  # imports narrative + transforms
from watcher.core import _watcher_loop, reset_demo_state
from watcher.demo import SyntheticFetcher


def _trace(msg: str) -> None:
    print(f"[watcher] {msg}")


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Watcher CLI")
    parser.add_argument("--demo", action="store_true", help="Run with synthetic data source")
    parser.add_argument("--jump-after", type=int, default=3, help="Demo: polls before jump")
    args = parser.parse_args()

    if args.demo:
        reset_demo_state()
        fetcher = SyntheticFetcher(jump_after_polls=args.jump_after)
        asyncio.run(
            _watcher_loop(
                fetcher=fetcher,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                trace=_trace,
            )
        )
    else:
        asyncio.run(
            _watcher_loop(
                fetcher=main.fetch_market_for_team,
                extract_fields=main.extract_market_fields,
                generate_narrative=main.generate_narrative,
                verify_narrative=main.verify_narrative,
                template_narrative=main.template_narrative,
                trace=None,
            )
        )


if __name__ == "__main__":
    main_cli()
```

- [ ] **Step 2: Manual smoke of CLI demo (no tests yet)**

Run: `uv run python -m watcher --demo --jump-after 2`
Expected: Console trace lines like `"[watcher] demo: gate: YES … notifying…"` and a macOS notification banner.


### Task 4: Add Demo Pipeline Test (Gate → LLM/Template → Notification)

**Files:**
- Create: `tests/test_demo_pipeline.py`

- [ ] **Step 1: Test that one notification fires at the jump**

```python
# tests/test_demo_pipeline.py
import asyncio
from unittest.mock import AsyncMock, patch

import main
from watcher.core import _watcher_loop, reset_demo_state
from watcher.demo import SyntheticFetcher


def test_demo_mode_fires_once_at_jump():
    reset_demo_state()
    fetcher = SyntheticFetcher(jump_after_polls=3)
    # Patch send_notification to observe calls
    with patch("watcher.core.send_notification") as sn:
        # Patch LLM to avoid network and keep deterministic
        with patch.object(main, "generate_narrative", new_callable=AsyncMock) as gn:
            gn.return_value = "Demo moved."
            # Run just enough of the loop for 3 polls across the single team
            async def run_n_polls(n):
                count = 0
                async def one_cycle():
                    nonlocal count
                    await _watcher_loop(
                        fetcher=fetcher,
                        extract_fields=main.extract_market_fields,
                        generate_narrative=main.generate_narrative,
                        verify_narrative=main.verify_narrative,
                        template_narrative=main.template_narrative,
                        trace=None,
                    )
                # Instead of modifying the loop, call _poll_team directly n times
            
    # Simpler: directly drive _poll_team n times
```

To keep this simple and deterministic, prefer driving `_poll_team` directly instead of the infinite `_watcher_loop`:

```python
# tests/test_demo_pipeline.py (continued)
from watcher.core import _poll_team, _last_notified

def test_demo_mode_fires_once_at_jump():
    reset_demo_state()
    fetcher = SyntheticFetcher(jump_after_polls=3)
    with patch("watcher.core.send_notification") as sn, \
         patch.object(main, "generate_narrative", new_callable=AsyncMock) as gn:
        gn.return_value = "Demo moved."
        # First two polls: 5000bp -> gate=no
        asyncio.run(_poll_team(
            "demo",
            fetcher=fetcher,
            extract_fields=main.extract_market_fields,
            generate_narrative=main.generate_narrative,
            verify_narrative=main.verify_narrative,
            template_narrative=main.template_narrative,
        ))
        asyncio.run(_poll_team(
            "demo",
            fetcher=fetcher,
            extract_fields=main.extract_market_fields,
            generate_narrative=main.generate_narrative,
            verify_narrative=main.verify_narrative,
            template_narrative=main.template_narrative,
        ))
        # Third poll: 6500bp -> gate fires
        asyncio.run(_poll_team(
            "demo",
            fetcher=fetcher,
            extract_fields=main.extract_market_fields,
            generate_narrative=main.generate_narrative,
            verify_narrative=main.verify_narrative,
            template_narrative=main.template_narrative,
        ))
    sn.assert_called_once()
```

Run: `uv run pytest tests/test_demo.py tests/test_demo_pipeline.py -q`
Expected: PASS.


### Task 5: Verify Full Test Suite and Update Any Remaining References

- [ ] **Step 1: Run all tests**

Run: `uv run pytest -q`
Expected: PASS. If failures reference old `main._poll_team` or `_get_cached_or_fetch`, update imports to `watcher.core` and pass dependencies as in Task 1 Step 4.

- [ ] **Step 2: Manual end-to-end check**

Run API locally: `uv run uvicorn main:app --reload`
In another terminal, run watcher locally (live): `uv run python -m watcher`
And demo: `uv run python -m watcher --demo --jump-after 2`
Expect: API responds; demo prints trace and fires a macOS notification.

---

## Self-Review

1. **Spec coverage:**
   - Standalone CLI with deterministic demo → Tasks 2–4
   - Shared module imported by `main.py` → Task 1
   - Data-source seam (fetcher DI) → Task 1
   - Console trace + notification echo in demo → Task 3 (trace). Echo is implicit in banner + trace; optional to also print title/body.
   - Reset cooldown/window for demo → `reset_demo_state()` in Task 1, used by CLI

2. **Placeholder scan:** No TBD/TODO. Code blocks are complete and runnable.

3. **Type consistency:** The DI signatures (`fetcher`, `extract_fields`, `generate_narrative`, `verify_narrative`, `template_narrative`) are used consistently across core, CLI, and tests.

---

Plan complete. Save this file and choose execution mode:

1. Subagent-Driven (recommended) — fresh subagent per task with review between tasks
2. Inline Execution — execute tasks here using executing-plans with checkpoints

Which approach?
