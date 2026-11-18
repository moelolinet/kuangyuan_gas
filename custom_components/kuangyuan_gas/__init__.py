"""Set up the Kuangyuan Gas (旷远燃气) integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from . import settings
from .api import (
    GasAccount,
    KuangyuanAPI,
    create_session,
    KuangyuanAuthError,
    KuangyuanConnectionError,
    KuangyuanDataError,
    KuangyuanError,
    KuangyuanWafError,
)
from .const import (
    ALL_ENTITY_KEYS,
    CONF_ACCOUNTS,
    CONF_BASE_URL,
    CONF_ENTITY_KEYS,
    DEFAULT_BASE_URL,
    DEFAULT_ENTITY_KEYS,
    DOMAIN,
    RECHARGE_KEYS,
)
from .coordinator import KuangyuanCoordinator
from .entity import mask_phone
from .runtime import KuangyuanRuntime

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


def account_label(accounts: dict[str, GasAccount], username: str) -> str:
    """Build a label that identifies the account without showing the number."""
    for account in accounts.values():
        if account.tel:
            return mask_phone(account.tel)
    return mask_phone(username)


def accounts_from_stored(stored: list[dict]) -> dict[str, GasAccount]:
    """Rebuild meter metadata from the form stored in the config entry."""
    accounts: dict[str, GasAccount] = {}
    for item in stored:
        uno = str(item.get("uno") or "").strip()
        if not uno:
            continue
        accounts[uno] = GasAccount(
            uno=uno,
            tel=str(item.get("tel") or ""),
            owner=str(item.get("owner") or ""),
            address=str(item.get("address") or ""),
            category=str(item.get("category") or ""),
        )
    return accounts


def prune_removed(
    hass: HomeAssistant,
    entry: ConfigEntry,
    keep: set[str],
    keys: set[str],
) -> None:
    """Delete registry entries that the current selection no longer creates.

    Reloading an entry does not clean the registries.  Without this, a meter
    that was deselected - or an entity kind that was unticked - keeps an
    ``unavailable`` leftover in the entity list forever.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    keep_devices = {f"{entry.entry_id}_{uno}" for uno in keep}
    keep_devices.add(f"{entry.entry_id}_account")
    account_prefix = f"{entry.entry_id}_account_"

    for item in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        unique_id = item.unique_id or ""
        if item.platform != DOMAIN:
            continue

        if unique_id.startswith(account_prefix):
            # Account level entities end with their key.
            key = unique_id[len(account_prefix) :]
            wanted = key in keys
        else:
            meter = next(
                (
                    uno
                    for uno in keep
                    if unique_id.startswith(f"{entry.entry_id}_{uno}_")
                ),
                None,
            )
            if meter is None:
                wanted = False
            else:
                key = unique_id[len(f"{entry.entry_id}_{meter}_") :]
                wanted = key in keys

        if not wanted:
            _LOGGER.debug("Removing stale entity: %s", item.entity_id)
            ent_reg.async_remove(item.entity_id)

    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        identifiers = {
            identifier[1]
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        }
        if identifiers and not (identifiers & keep_devices):
            _LOGGER.debug("Removing device of a deselected meter: %s", device.id)
            dev_reg.async_remove_device(device.id)


def resolve_entity_keys(entry: ConfigEntry) -> set[str]:
    """Return the enabled entity kinds.

    ``settings.entity_keys`` already distinguishes "never configured" (all
    defaults) from "configured as empty" (no entity), so nothing is forced here.
    """
    return set(settings.entity_keys(entry)) & set(ALL_ENTITY_KEYS)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one customer account from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

    # A private session is required: the login state is a cookie, so several
    # entries (e.g. family members) must never share one jar.
    session = create_session()
    api = KuangyuanAPI(session, username, password, base_url)

    accounts = accounts_from_stored(settings.stored_accounts(entry))
    if not accounts:
        try:
            fetched = await api.async_get_accounts()
        except KuangyuanAuthError as err:
            await api.async_close()
            raise ConfigEntryAuthFailed(str(err)) from err
        except (
            KuangyuanConnectionError,
            KuangyuanWafError,
            KuangyuanDataError,
            KuangyuanError,
        ) as err:
            await api.async_close()
            raise ConfigEntryNotReady(str(err)) from err
        except Exception as err:  # noqa: BLE001 - retried by Home Assistant
            await api.async_close()
            raise ConfigEntryNotReady(str(err)) from err
        accounts = {account.uno: account for account in fetched}

    selected = [uno for uno in settings.selected_unos(entry) if uno in accounts]
    if not selected:
        selected = list(accounts)
    if not selected:
        await api.async_close()
        raise ConfigEntryNotReady("No gas meter available for this account")

    entity_keys = resolve_entity_keys(entry)
    coordinator = KuangyuanCoordinator(
        hass,
        entry,
        api,
        accounts,
        selected,
        scan_interval=settings.scan_interval(entry),
        recharge_pages=settings.recharge_pages(entry),
        enable_recharges=bool(entity_keys & RECHARGE_KEYS),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        await api.async_close()
        raise
    except Exception as err:  # noqa: BLE001
        await api.async_close()
        raise ConfigEntryNotReady(str(err)) from err

    runtime = KuangyuanRuntime(
        coordinator=coordinator,
        accounts=accounts,
        selected_unos=selected,
        entity_keys=entity_keys,
        account_label=account_label(accounts, username),
        low_balance_threshold=settings.low_balance_threshold(entry),
        instance_name=entry.title,
    )
    hass.data[DOMAIN][entry.entry_id] = runtime

    # Remove entities of meters that were deselected in the options flow.
    prune_removed(hass, entry, set(selected), entity_keys)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_listener))
    return True


async def async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and release its private session."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: KuangyuanRuntime | None = hass.data[DOMAIN].pop(entry.entry_id, None)
        if runtime is not None:
            await runtime.coordinator.api.async_close()
    return unload_ok



