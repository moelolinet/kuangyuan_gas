"""Resolve integration options with sensible fallbacks.

Options may live in ``entry.options`` (edited through the options flow) or in
``entry.data`` (written at first setup).  Every read therefore falls back to
the stored data, then to the constant default, so existing entries keep their
settings after an upgrade.
"""

from __future__ import annotations

from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry

from .const import (
    ALL_ENTITY_KEYS,
    CONF_ACCOUNTS,
    CONF_ENTITY_KEYS,
    CONF_LOW_BALANCE,
    CONF_RECHARGE_PAGES,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_UNOS,
    DEFAULT_ENTITY_KEYS,
    DEFAULT_LOW_BALANCE,
    DEFAULT_RECHARGE_PAGES,
    DEFAULT_SCAN_INTERVAL,
    MAX_RECHARGE_PAGES,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)


def clamp_interval(value: Any, fallback: int = DEFAULT_SCAN_INTERVAL) -> int:
    """Clamp a user supplied refresh interval into the supported range."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(fallback)
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, number))


def clamp_threshold(value: Any, fallback: float = DEFAULT_LOW_BALANCE) -> float:
    """Clamp the low balance threshold to a sane non-negative amount."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number < 0:
        return fallback
    return round(min(number, 100000.0), 2)


def clamp_pages(value: Any, fallback: int = DEFAULT_RECHARGE_PAGES) -> int:
    """Clamp the number of recharge history pages to fetch."""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = fallback
    return max(1, min(MAX_RECHARGE_PAGES, number))


def _raw_option(entry: ConfigEntry, key: str) -> Any:
    """Read a setting from options first, then from the initial data.

    ``None`` and ``""`` mean "not set"; an empty list is a meaningful value
    (e.g. no entity selected) and is therefore returned as is.
    """
    value = entry.options.get(key)
    if value is None or value == "":
        value = entry.data.get(key)
    return value


def _as_bool(value: Any, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def scan_interval(entry: ConfigEntry) -> int:
    """Effective polling interval in seconds."""
    return clamp_interval(_raw_option(entry, CONF_SCAN_INTERVAL))


def low_balance_threshold(entry: ConfigEntry) -> float:
    """Effective balance warning threshold in yuan."""
    return clamp_threshold(_raw_option(entry, CONF_LOW_BALANCE))


def recharge_pages(entry: ConfigEntry) -> int:
    """Effective number of recharge history pages to read."""
    return clamp_pages(_raw_option(entry, CONF_RECHARGE_PAGES))


def entity_keys(entry: ConfigEntry) -> list[str]:
    """Entity kinds the user chose to expose for this account.

    An explicitly empty selection is preserved (it means "only devices, no
    entities"); only a missing setting falls back to the defaults.
    """
    raw = _raw_option(entry, CONF_ENTITY_KEYS)
    if raw is None:
        return list(DEFAULT_ENTITY_KEYS)
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return list(DEFAULT_ENTITY_KEYS)
    requested = {str(item) for item in raw}
    return [key for key in ALL_ENTITY_KEYS if key in requested]


def stored_accounts(entry: ConfigEntry) -> list[dict[str, Any]]:
    """Return the meter metadata discovered at setup time."""
    raw = _raw_option(entry, CONF_ACCOUNTS)
    if isinstance(raw, list):
        return [
            dict(item) for item in raw if isinstance(item, Mapping) and item.get("uno")
        ]
    return []


def selected_unos(entry: ConfigEntry) -> list[str]:
    """Return the meters the user chose to display."""
    raw = _raw_option(entry, CONF_SELECTED_UNOS)
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []
