"""Daily/monthly consumption end-to-end tests with a real recorder.

The integration derives both values from recorder history of its own
cumulative usage sensor.  The tests seed that history directly (with proper
metadata rows) and then check the computed sensors.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.recorder import get_instance
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from conftest import BASE_URL
from custom_components.kuangyuan_gas.const import (
    CONF_ACCOUNTS,
    CONF_BASE_URL,
    CONF_ENTITY_KEYS,
    CONF_LOW_BALANCE,
    CONF_RECHARGE_PAGES,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_UNOS,
    DOMAIN,
)

ACCOUNTS = [
    {
        "uno": "UNO000001",
        "tel": "13800000001",
        "owner": "张**三",
        "address": "某某区某某路1号101室",
        "category": "我家",
    }
]

USAGE_KEYS = ["balance", "usage", "daily_usage", "monthly_usage"]


@pytest.fixture(autouse=True)
def _recorder_first(recorder_mock, enable_custom_integrations):
    """Start the recorder before hass: the plugin requires that order."""
    yield


def build_entry(portal_url, entry_id="usage_entry"):
    return MockConfigEntry(
        domain=DOMAIN,
        title="旷远燃气 usage",
        data={
            CONF_USERNAME: "13800000001",
            CONF_PASSWORD: "secret",
            CONF_BASE_URL: portal_url,
            CONF_ACCOUNTS: ACCOUNTS,
            CONF_SELECTED_UNOS: ["UNO000001"],
            CONF_ENTITY_KEYS: USAGE_KEYS,
            CONF_SCAN_INTERVAL: 1800,
            CONF_LOW_BALANCE: 20.0,
            CONF_RECHARGE_PAGES: 1,
        },
        source="user",
        entry_id=entry_id,
        unique_id=f"{portal_url}|usage|{entry_id}",
    )


def ids_for(hass, entry_id):
    registry = er.async_get(hass)

    def _id(key):
        return registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry_id}_UNO000001_{key}"
        )

    return {
        "usage": _id("usage"),
        "daily": _id("daily_usage"),
        "monthly": _id("monthly_usage"),
    }


async def seed_history(hass, recorder, entity_id, points):
    """Insert recorded states for ``entity_id`` at the given timestamps.

    The integration only looks at the recorder, so this is exactly the data a
    real installation would have accumulated over time.
    """
    from homeassistant.components.recorder import db_schema

    def _write() -> None:
        session = recorder.get_session()
        try:
            meta = (
                session.query(db_schema.StatesMeta)
                .filter(db_schema.StatesMeta.entity_id == entity_id)
                .one_or_none()
            )
            if meta is None:
                meta = db_schema.StatesMeta(entity_id=entity_id)
                session.add(meta)
                session.flush()
            for when, value in points:
                ts = when.timestamp()
                session.add(
                    db_schema.States(
                        entity_id=entity_id,
                        state=str(value),
                        attributes=None,
                        last_changed=when,
                        last_changed_ts=ts,
                        last_reported_ts=ts,
                        last_updated=when,
                        last_updated_ts=ts,
                        old_state_id=None,
                        attributes_id=None,
                        context_id=None,
                        context_user_id=None,
                        context_parent_id=None,
                        origin_idx=0,
                        context_id_bin=b"",
                        context_user_id_bin=None,
                        context_parent_id_bin=None,
                        metadata_id=meta.metadata_id,
                    )
                )
            session.commit()
        finally:
            recorder._close_event_session()
            recorder._end_session()

    await get_instance(hass).async_add_executor_job(_write)
    await hass.async_block_till_done()


async def test_daily_and_monthly_computed_from_history(hass, fake_portal, recorder_mock):
    """Daily = since local midnight, monthly = since the 1st at midnight."""
    now = dt_util.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    entry = build_entry(BASE_URL)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ids = ids_for(hass, entry.entry_id)
    assert all(ids.values()), ids

    # History: 28.00 before the month started, 40.00 before midnight today.
    points = [(month_start - timedelta(days=1, hours=2), 28.0)]
    if midnight - timedelta(minutes=30) > month_start:
        points.append((midnight - timedelta(minutes=30), 40.0))
    else:
        # The month started today: use the month baseline as the daily one too.
        points.append((midnight - timedelta(seconds=5), 28.0))
    await seed_history(hass, recorder_mock, ids["usage"], points)

    # Current portal reading is 45.67 (see conftest).
    coord = hass.data[DOMAIN][entry.entry_id].coordinator
    await coord.async_refresh()
    await hass.async_block_till_done()


    daily = hass.states.get(ids["daily"])
    monthly = hass.states.get(ids["monthly"])
    assert daily is not None and monthly is not None
    expected_daily = 45.67 - (40.0 if len(points) == 2 and points[1][1] == 40.0 else 28.0)
    assert float(daily.state) == pytest.approx(expected_daily, abs=0.02), daily.state
    assert float(monthly.state) == pytest.approx(45.67 - 28.0, abs=0.02), monthly.state
    assert daily.attributes["source"] == "home_assistant_recorder"
    assert monthly.attributes["source"] == "home_assistant_recorder"
    assert daily.attributes["baseline_usage"] == pytest.approx(
        40.0 if expected_daily == pytest.approx(5.67) else 28.0
    )


async def test_missing_history_yields_unknown(hass, fake_portal, recorder_mock):
    """Without history the sensors stay unknown instead of reporting 0."""
    entry = build_entry(BASE_URL, entry_id="no_history")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ids = ids_for(hass, entry.entry_id)
    state = hass.states.get(ids["daily"])
    assert state is not None
    assert state.state in (STATE_UNAVAILABLE, "unknown")
    assert state.attributes.get("source") == "home_assistant_recorder"


async def test_consumption_ignores_portal_fields(hass, fake_portal, recorder_mock):
    """A portal payload without usage data must not break the recorder values."""
    now = dt_util.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    entry = build_entry(BASE_URL, entry_id="no_portal_usage")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ids = ids_for(hass, entry.entry_id)
    await seed_history(
        hass, recorder_mock, ids["usage"], [(midnight - timedelta(minutes=10), 10.0)]
    )
    coord = hass.data[DOMAIN][entry.entry_id].coordinator
    await coord.async_refresh()
    await hass.async_block_till_done()

    daily = hass.states.get(ids["daily"])
    assert daily is not None
    # 45.67 - 10.00 from history; nothing came from the portal for this value.
    assert float(daily.state) == pytest.approx(35.67, abs=0.02), daily.state
