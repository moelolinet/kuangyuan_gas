"""Tests for the recorder based daily/monthly consumption logic."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "kuangyuan_gas"


def _install_stubs() -> None:
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
    _install_stubs()
    spec = importlib.util.spec_from_file_location("ky_usage", ROOT / "usage.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["ky_usage"] = module
    spec.loader.exec_module(module)
    return module


usage = _load_usage()

CST = timezone(timedelta(hours=8))


class Row:
    """Mimics a recorder row (state + timestamp)."""

    def __init__(self, state, moment: datetime):
        self.state = state
        self.last_updated_ts = moment.timestamp()


class FakeState:
    """Mimics a State object for the float helper."""

    def __init__(self, value):
        self.state = value


# --------------------------------------------------------------------- windows
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


def test_dst_style_shift_keeps_local_midnight():
    tz = timezone(timedelta(hours=9))
    now = datetime(2026, 3, 15, 6, 0, tzinfo=tz)
    assert usage.day_window(now).start == datetime(2026, 3, 15, 0, 0, tzinfo=tz)
    assert usage.day_window(now).start.hour == 0


# ----------------------------------------------------------------- consumption
def test_consumption_is_difference():
    assert usage.compute_consumption(268.46, 260.00) == 8.46
    assert usage.compute_consumption("268.4600", "260.0000") == 8.46


def test_consumption_accepts_baseline_object():
    baseline = usage.Baseline(100.0, usage.SOURCE_FIRST)
    assert usage.compute_consumption(107.5, baseline) == 7.5


def test_consumption_none_when_missing():
    assert usage.compute_consumption(None, 10.0) is None
    assert usage.compute_consumption(10.0, None) is None
    assert usage.compute_consumption("unknown", 10.0) is None
    assert usage.compute_consumption("abc", 1.0) is None


def test_consumption_never_negative_on_reset():
    assert usage.compute_consumption(5.0, 100.0) is None


def test_consumption_zero_when_unchanged():
    assert usage.compute_consumption(100.0, 100.0) == 0.0


def test_consumption_grows_with_the_meter():
    """The core user requirement: a rising meter raises daily and monthly."""
    baseline = 1000.0
    first = usage.compute_consumption(1010.0, baseline)
    later = usage.compute_consumption(1025.4, baseline)
    assert later > first
    assert later == 25.4


# -------------------------------------------------------------- baseline rules
def test_baseline_prefers_reading_before_boundary():
    boundary = datetime(2026, 3, 15, 0, 0, tzinfo=CST)
    rows = [
        Row("100.0", boundary - timedelta(hours=6)),
        Row("104.0", boundary - timedelta(minutes=20)),
        Row("110.0", boundary + timedelta(minutes=5)),
    ]
    picked = usage.select_baseline(rows, boundary)
    assert picked is not None
    assert picked.value == 104.0
    assert picked.source == usage.SOURCE_BEFORE
    assert picked.exact is True


def test_baseline_falls_back_to_reading_after_boundary():
    boundary = datetime(2026, 3, 15, 0, 0, tzinfo=CST)
    rows = [
        Row("110.0", boundary + timedelta(minutes=4)),
        Row("112.0", boundary + timedelta(hours=3)),
    ]
    picked = usage.select_baseline(rows, boundary)
    assert picked is not None
    assert picked.value == 110.0
    assert picked.source == usage.SOURCE_AFTER


def test_baseline_falls_back_to_first_record_when_installed_mid_window():
    """Installed after the boundary: the first reading becomes the baseline."""
    boundary = datetime(2026, 3, 1, 0, 0, tzinfo=CST)
    rows = [
        Row("500.0", datetime(2026, 3, 9, 15, 0, tzinfo=CST)),
        Row("520.0", datetime(2026, 3, 12, 9, 0, tzinfo=CST)),
        Row("560.0", datetime(2026, 3, 15, 9, 0, tzinfo=CST)),
    ]
    picked = usage.select_baseline(rows, boundary)
    assert picked is not None
    assert picked.value == 500.0
    assert picked.source == usage.SOURCE_FIRST


def test_baseline_none_without_rows():
    boundary = datetime(2026, 3, 15, 0, 0, tzinfo=CST)
    assert usage.select_baseline([], boundary) is None
    assert usage.select_baseline([Row("unknown", boundary)], boundary) is None
    assert usage.select_baseline([Row("unavailable", boundary)], boundary) is None


def test_baseline_ignores_non_numeric_rows():
    boundary = datetime(2026, 3, 15, 0, 0, tzinfo=CST)
    rows = [
        Row("abc", boundary - timedelta(minutes=5)),
        Row("90.0", boundary - timedelta(minutes=40)),
    ]
    picked = usage.select_baseline(rows, boundary)
    assert picked is not None and picked.value == 90.0


# --------------------------------------------------------------- helper values
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
