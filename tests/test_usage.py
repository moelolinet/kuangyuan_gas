"""Tests for the recorder based daily/monthly consumption logic.

Loaded standalone: the module is imported directly by file path, so the test
does not depend on any other test module having created the ``ky`` package.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "kuangyuan_gas"


def _install_stubs() -> None:
    """Create only the Home Assistant symbols usage.py imports.

    Other test modules may have registered partial stubs for the same package,
    so every attribute is checked individually instead of returning early.
    """
    ha = sys.modules.setdefault("homeassistant", types.ModuleType("homeassistant"))
    if not hasattr(ha, "__path__"):
        ha.__path__ = []

    if "homeassistant.core" not in sys.modules:
        core = types.ModuleType("homeassistant.core")

        class HomeAssistant:  # pragma: no cover
            pass

        core.HomeAssistant = HomeAssistant
        sys.modules["homeassistant.core"] = core

    util = sys.modules.get("homeassistant.util")
    if util is None:
        util = types.ModuleType("homeassistant.util")
        util.__path__ = []
        sys.modules["homeassistant.util"] = util
    if not hasattr(ha, "util"):
        ha.util = util
    if "homeassistant.util.dt" not in sys.modules:
        dt_mod = types.ModuleType("homeassistant.util.dt")
        dt_mod.now = lambda tz=None: datetime.now(tz)
        dt_mod.as_utc = lambda value: value.astimezone(timezone.utc)
        sys.modules["homeassistant.util.dt"] = dt_mod
    util.dt = sys.modules["homeassistant.util.dt"]


def _load_usage():
    """Load usage.py by path, stubbing its only Home Assistant imports."""
    _install_stubs()
    spec = importlib.util.spec_from_file_location("ky_usage", ROOT / "usage.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before executing: dataclasses resolve __module__ via sys.modules.
    sys.modules["ky_usage"] = module
    spec.loader.exec_module(module)
    return module


usage = _load_usage()

CST = timezone(timedelta(hours=8))


class FakeState:
    """Mimics a recorder State for the float extraction helper."""

    def __init__(self, value):
        self.state = value


def test_day_window_starts_at_local_midnight():
    now = datetime(2026, 3, 15, 23, 47, 12, tzinfo=CST)
    window = usage.day_window(now)
    assert window.start == datetime(2026, 3, 15, 0, 0, tzinfo=CST)
    assert window.kind == "day"


def test_month_window_starts_on_first():
    now = datetime(2026, 3, 15, 23, 47, 12, tzinfo=CST)
    window = usage.month_window(now)
    assert window.start == datetime(2026, 3, 1, 0, 0, tzinfo=CST)
    assert window.kind == "month"


def test_month_boundary_is_previous_month():
    now = datetime(2026, 3, 1, 0, 5, tzinfo=CST)
    assert usage.month_window(now).start == datetime(2026, 3, 1, 0, 0, tzinfo=CST)
    before = now - timedelta(minutes=6)
    assert usage.month_window(before).start == datetime(2026, 2, 1, 0, 0, tzinfo=CST)


def test_day_boundary_at_midnight():
    now = datetime(2026, 3, 16, 0, 0, 0, tzinfo=CST)
    assert usage.day_window(now).start == datetime(2026, 3, 16, 0, 0, tzinfo=CST)
    before = now - timedelta(seconds=1)
    assert usage.day_window(before).start == datetime(2026, 3, 15, 0, 0, tzinfo=CST)


def test_consumption_is_difference():
    assert usage.compute_consumption(268.46, 260.00) == 8.46
    assert usage.compute_consumption("268.4600", "260.0000") == 8.46


def test_consumption_none_when_missing():
    assert usage.compute_consumption(None, 10.0) is None
    assert usage.compute_consumption(10.0, None) is None
    assert usage.compute_consumption("unknown", 10.0) is None
    assert usage.compute_consumption("abc", 1.0) is None


def test_consumption_never_negative_on_reset():
    assert usage.compute_consumption(5.0, 100.0) is None


def test_consumption_zero_when_unchanged():
    assert usage.compute_consumption(100.0, 100.0) == 0.0


def test_float_extraction_from_state_objects():
    assert usage._to_float(FakeState("12.5")) == 12.5
    assert usage._to_float(FakeState("unavailable")) is None
    assert usage._to_float(FakeState("")) is None
    assert usage._to_float("7") == 7.0
    assert usage._to_float(None) is None


def test_recorder_availability_detection():
    class FakeHass:
        config = types.SimpleNamespace(components=set())

    assert usage.recorder_available(FakeHass()) is False

    class WithRecorder:
        config = types.SimpleNamespace(components={"recorder"})

    assert usage.recorder_available(WithRecorder()) is True


def test_daily_and_monthly_use_different_windows():
    now = datetime(2026, 3, 15, 9, 30, tzinfo=CST)
    assert usage.day_window(now).start != usage.month_window(now).start
    assert usage.day_window(now).start > usage.month_window(now).start


def test_dst_style_shift_keeps_local_midnight():
    """A +1h shift must still produce 00:00 local, not 23:00."""
    tz = timezone(timedelta(hours=9))
    now = datetime(2026, 3, 15, 6, 0, tzinfo=tz)
    assert usage.day_window(now).start == datetime(2026, 3, 15, 0, 0, tzinfo=tz)
    assert usage.day_window(now).start.hour == 0
