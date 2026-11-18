"""Button platform for the Kuangyuan Gas integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, KEY_REFRESH_BUTTON
from .coordinator import KuangyuanCoordinator
from .entity import KuangyuanAccountEntity
from .runtime import KuangyuanRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the refresh button when it is enabled."""
    runtime: KuangyuanRuntime = hass.data[DOMAIN][entry.entry_id]
    if KEY_REFRESH_BUTTON not in runtime.entity_keys:
        return
    async_add_entities([KuangyuanRefreshButton(runtime.coordinator, runtime.account_label)])


class KuangyuanRefreshButton(KuangyuanAccountEntity, ButtonEntity):
    """Trigger an immediate poll of every selected meter."""

    _attr_translation_key = KEY_REFRESH_BUTTON
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: KuangyuanCoordinator, account_label: str) -> None:
        super().__init__(coordinator, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_account_{KEY_REFRESH_BUTTON}"

    async def async_press(self) -> None:
        """Ask the coordinator to refresh now."""
        await self.coordinator.async_request_refresh()
