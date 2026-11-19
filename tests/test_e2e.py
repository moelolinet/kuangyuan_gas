"""End-to-end tests: real Home Assistant core with a fake portal."""

from __future__ import annotations

import pytest

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.helpers import entity_registry as er

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
    },
    {
        "uno": "UNO000002",
        "tel": "13900000002",
        "owner": "李**四",
        "address": "某某区某某路2号202室",
        "category": "亲友家",
    },
]

ALL_KEYS = [
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
]


@pytest.fixture(autouse=True)
def _enable_custom(enable_custom_integrations):
    """Allow loading of this custom integration."""
    yield


def build_entry(BASE_URL, *, unos=None, keys=None, interval=1800, low=20.0,
                entry_id="entry1", password="secret"):
    return MockConfigEntry(
        domain=DOMAIN,
        title="旷远燃气 test",
        data={
            CONF_USERNAME: "13800000001",
            CONF_PASSWORD: password,
            CONF_BASE_URL: BASE_URL,
            CONF_ACCOUNTS: ACCOUNTS,
            CONF_SELECTED_UNOS: ["UNO000001", "UNO000002"] if unos is None else unos,
            CONF_ENTITY_KEYS: ALL_KEYS if keys is None else keys,
            CONF_SCAN_INTERVAL: interval,
            CONF_LOW_BALANCE: low,
            CONF_RECHARGE_PAGES: 1,
        },
        source="user",
        entry_id=entry_id,
        unique_id=f"{BASE_URL}|13800000001|{entry_id}",
    )


async def setup_entry(hass, entry):
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def registry_map(hass, entry_id):
    """unique_id -> entity_id for one config entry."""
    registry = er.async_get(hass)
    return {
        item.unique_id: item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry_id)
    }


def by_suffix(mapping, suffix):
    return next(
        (entity_id for unique_id, entity_id in mapping.items() if unique_id.endswith(suffix)),
        None,
    )


async def test_setup_creates_all_entities(hass, fake_portal):
    """Every approved entity of both meters is created with correct values."""
    entry = await setup_entry(hass, build_entry(BASE_URL))
    assert entry.state is ConfigEntryState.LOADED

    mapping = registry_map(hass, entry.entry_id)
    # 11 entities per meter x 2 meters, and no account level device.
    assert len(mapping) == 22, sorted(mapping)

    balance_id = by_suffix(mapping, "UNO000001_balance")
    assert balance_id is not None
    state = hass.states.get(balance_id)
    assert state is not None and state.state == "123.45"

    usage_id = by_suffix(mapping, "UNO000001_usage")
    assert float(hass.states.get(usage_id).state) == 45.67

    supply_id = by_suffix(mapping, "UNO000001_supply")
    assert hass.states.get(supply_id).state == STATE_ON

    low_id = by_suffix(mapping, "UNO000001_low_balance")
    assert hass.states.get(low_id).state == STATE_OFF

    updated_id = by_suffix(mapping, "UNO000001_data_updated_at")
    assert hass.states.get(updated_id).state not in (None, STATE_UNAVAILABLE)

    amount_id = by_suffix(mapping, "UNO000001_last_recharge_amount")
    assert float(hass.states.get(amount_id).state) == 200.0

    count_id = by_suffix(mapping, "UNO000001_recharge_count")
    assert hass.states.get(count_id).state == "2"

    # The refresh button belongs to each meter, not to an account device.
    assert by_suffix(mapping, "UNO000001_refresh")
    assert by_suffix(mapping, "UNO000002_refresh")
    assert not any("account" in unique for unique in mapping), sorted(mapping)


async def test_low_balance_threshold(hass, fake_portal):
    """A high threshold flips the problem sensor on."""
    entry = await setup_entry(hass, build_entry(BASE_URL, low=100.0))
    mapping = registry_map(hass, entry.entry_id)
    low_id = by_suffix(mapping, "UNO000002_low_balance")
    assert hass.states.get(low_id).state == STATE_ON


async def test_selected_unos_only(hass, fake_portal):
    """Deselecting a meter removes all of its entities."""
    entry = await setup_entry(hass, build_entry(BASE_URL, unos=["UNO000002"]))
    mapping = registry_map(hass, entry.entry_id)
    assert not any("UNO000001" in unique for unique in mapping), sorted(mapping)
    assert len(mapping) == 11, sorted(mapping)


async def test_entity_keys_subset(hass, fake_portal):
    """Only the ticked entity kinds are created."""
    entry = await setup_entry(
        hass, build_entry(BASE_URL, keys=["balance", "supply", "refresh"])
    )
    mapping = registry_map(hass, entry.entry_id)
    # 3 keys per meter x 2 meters
    assert len(mapping) == 6, sorted(mapping)
    assert by_suffix(mapping, "UNO000001_balance")
    assert by_suffix(mapping, "UNO000001_supply")
    assert by_suffix(mapping, "UNO000001_refresh")


async def test_empty_entity_selection_creates_nothing(hass, fake_portal):
    """An explicitly empty selection must not be replaced by the defaults."""
    entry = await setup_entry(hass, build_entry(BASE_URL, keys=[]))
    assert entry.state is ConfigEntryState.LOADED
    assert registry_map(hass, entry.entry_id) == {}


async def test_per_meter_failure_keeps_other_meter(hass, fake_portal):
    """One broken meter must not take the other one down."""
    fake_portal.fail_uno = "UNO000001"
    entry = await setup_entry(hass, build_entry(BASE_URL))
    mapping = registry_map(hass, entry.entry_id)

    broken = hass.states.get(by_suffix(mapping, "UNO000001_balance"))
    assert broken is not None and broken.state == STATE_UNAVAILABLE

    healthy = hass.states.get(by_suffix(mapping, "UNO000002_balance"))
    assert healthy is not None and healthy.state == "67.89"


async def test_auth_failure_marks_entry(hass, fake_portal):
    """A rejected password surfaces as an authentication error."""
    fake_portal.login_ok = False
    entry = build_entry(BASE_URL)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_session_expiry_relogin(hass, fake_portal):
    """An expired cookie triggers exactly one transparent re-login."""
    entry = await setup_entry(hass, build_entry(BASE_URL))
    fake_portal.expire()
    calls_before = fake_portal.login_calls()

    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert fake_portal.login_calls() == calls_before + 1
    mapping = registry_map(hass, entry.entry_id)
    assert hass.states.get(by_suffix(mapping, "UNO000001_balance")).state == "123.45"


async def test_options_change_reloads_entities(hass, fake_portal):
    """Changing the options replaces the exposed entity set."""
    entry = await setup_entry(hass, build_entry(BASE_URL))

    hass.config_entries.async_update_entry(
        entry,
        options={
            CONF_SELECTED_UNOS: ["UNO000001"],
            CONF_ENTITY_KEYS: ["balance"],
            CONF_SCAN_INTERVAL: 900,
            CONF_LOW_BALANCE: 20.0,
            CONF_RECHARGE_PAGES: 1,
        },
    )
    await hass.async_block_till_done()

    mapping = registry_map(hass, entry.entry_id)
    assert len(mapping) == 1, sorted(mapping)
    balance_id = by_suffix(mapping, "UNO000001_balance")
    assert hass.states.get(balance_id).state == "123.45"


async def test_refresh_button(hass, fake_portal):
    """The button triggers a poll."""
    entry = await setup_entry(hass, build_entry(BASE_URL))
    mapping = registry_map(hass, entry.entry_id)
    button_id = by_suffix(mapping, "UNO000001_refresh")

    before = fake_portal.gas_calls()
    await hass.services.async_call(
        "button", "press", {"entity_id": button_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert fake_portal.gas_calls() > before


async def test_scan_interval_applied(hass, fake_portal):
    """The configured interval reaches the coordinator."""
    entry = await setup_entry(hass, build_entry(BASE_URL, interval=900))
    coordinator = hass.data[DOMAIN][entry.entry_id].coordinator
    assert coordinator.update_interval.total_seconds() == 900


async def test_two_accounts_are_independent(hass, fake_portal):
    """Two entries keep separate selections and poll independently."""
    first = await setup_entry(hass, build_entry(BASE_URL, unos=["UNO000001"], entry_id="a"))
    second = await setup_entry(
        hass, build_entry(BASE_URL, unos=["UNO000002"], keys=["usage"], entry_id="b")
    )
    assert len(registry_map(hass, first.entry_id)) == 11
    assert len(registry_map(hass, second.entry_id)) == 1




async def test_only_meter_devices_and_each_has_refresh(hass, fake_portal):
    """The hub lists only the meters, and each meter has a refresh button."""
    from homeassistant.helpers import device_registry as dr

    entry = await setup_entry(hass, build_entry(BASE_URL))

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    names = sorted(device.name for device in devices)
    assert names == [
        "旷远燃气 UNO000001",
        "旷远燃气 UNO000002",
    ], names
    # No account level device may exist.
    assert not any("account" in (d.name or "").lower() for d in devices), names

    for device in devices:
        entity_ids = [
            item.entity_id
            for item in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
            if item.device_id == device.id
        ]
        assert len(entity_ids) == 11, (device.name, entity_ids)
        buttons = [eid for eid in entity_ids if eid.startswith("button.")]
        assert len(buttons) == 1, (device.name, buttons)


async def test_entry_title_defaults_to_username(hass, fake_portal):
    """The config flow names the hub after the login account."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "13800000001",
            CONF_PASSWORD: "secret",
            CONF_BASE_URL: BASE_URL,
        },
    )
    assert result["step_id"] == "meters"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_SELECTED_UNOS: ["UNO000001", "UNO000002"], CONF_ENTITY_KEYS: ALL_KEYS},
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "13800000001", result["title"]
    await hass.async_block_till_done()
