"""Logic tests for settings, coordinator behaviour and entity keys.

These run without Home Assistant by stubbing the few HA symbols that the
modules import at module level.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "kuangyuan_gas"


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def _stub_homeassistant() -> None:
    """Minimal Home Assistant surface used by settings/coordinator/const."""
    if "homeassistant.config_entries" in sys.modules:
        return

    _module("homeassistant")
    _module("homeassistant.config_entries", ConfigEntry=object)
    _module("homeassistant.core", HomeAssistant=object)

    update_coordinator = _module(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=object,
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
        CoordinatorEntity=object,
    )
    _module("homeassistant.helpers", update_coordinator=update_coordinator)
    _module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    _module("homeassistant.exceptions")

    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    aiohttp.ClientError = ClientError
    aiohttp.ClientSession = object
    aiohttp.ClientTimeout = object
    sys.modules["aiohttp"] = aiohttp


def _install_package() -> None:
    """Register the integration folder as a package so relative imports work."""
    _stub_homeassistant()
    if "ky" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "ky", ROOT / "const.py", submodule_search_locations=[str(ROOT)]
    )
    pkg = importlib.util.module_from_spec(spec)
    pkg.__path__ = [str(ROOT)]
    sys.modules["ky"] = pkg


_install_package()
const = importlib.import_module("ky.const")
settings = importlib.import_module("ky.settings")


class FakeEntry:
    """Stand-in for a ConfigEntry holding data/options."""

    def __init__(self, data=None, options=None):
        self.data = data or {}
        self.options = options or {}


def test_entity_catalogue_matches_user_request():
    """Only the 10 approved kinds exist, 8 per meter and 2 per account."""
    keys = set(const.ALL_ENTITY_KEYS)
    assert keys == {
        "balance",
        "usage",
        "daily_usage",
        "monthly_usage",
        "supply",
        "low_balance",
        "data_updated_at",
        "last_recharge_amount",
        "last_recharge_time",
        "recharge_count",
        "refresh",
    }, keys
    assert len(const.METER_LEVEL_KEYS) == 11
    assert const.CONSUMPTION_KEYS == {"daily_usage", "monthly_usage"}
    # There is no account level device any more: every entity belongs to a meter.
    assert not hasattr(const, "ACCOUNT_LEVEL_KEYS")
    # No privacy entity survives the user's selection.
    for leaked in ("owner", "address", "phone", "category"):
        assert leaked not in keys


def test_scan_interval_clamped():
    assert settings.clamp_interval(30) == 300
    assert settings.clamp_interval("600") == 600
    assert settings.clamp_interval(None) == 1800
    assert settings.clamp_interval(10**9) == 86400
    assert settings.clamp_interval("abc") == 1800
    assert settings.clamp_interval(3600.7) == 3600


def test_threshold_and_pages_clamped():
    assert settings.clamp_threshold(-5) == 20.0
    assert settings.clamp_threshold("0") == 0.0
    assert settings.clamp_threshold(33.333) == 33.33
    assert settings.clamp_pages(0) == 1
    assert settings.clamp_pages(99) == 5


def test_entity_keys_distinguish_empty_from_missing():
    assert settings.entity_keys(FakeEntry()) == const.DEFAULT_ENTITY_KEYS
    assert settings.entity_keys(FakeEntry({}, {"entity_keys": []})) == []
    assert settings.entity_keys(
        FakeEntry({}, {"entity_keys": ["balance", "bogus"]})
    ) == ["balance"]


def test_options_override_data():
    entry = FakeEntry(
        data={"scan_interval": 600, "low_balance_threshold": 10},
        options={"scan_interval": 900},
    )
    assert settings.scan_interval(entry) == 900
    assert settings.low_balance_threshold(entry) == 10.0


def test_selected_unos_accepts_list_and_csv():
    assert settings.selected_unos(FakeEntry({}, {"selected_unos": ["A", "B"]})) == [
        "A",
        "B",
    ]
    assert settings.selected_unos(FakeEntry({}, {"selected_unos": "A, B"})) == ["A", "B"]


def test_selected_unos_missing_is_empty():
    assert settings.selected_unos(FakeEntry()) == []


def test_stored_accounts_filters_invalid_rows():
    entry = FakeEntry(
        {
            "accounts": [
                {"uno": "A", "tel": "1"},
                {"tel": "2"},
                "junk",
                {"uno": ""},
            ]
        }
    )
    stored = settings.stored_accounts(entry)
    assert len(stored) == 1 and stored[0]["uno"] == "A"

