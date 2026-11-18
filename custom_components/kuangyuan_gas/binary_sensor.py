"""Binary sensor platform for the Kuangyuan Gas integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GasAccount
from .const import DOMAIN, KEY_LOW_BALANCE, KEY_SUPPLY
from .coordinator import KuangyuanCoordinator
from .entity import KuangyuanMeterEntity
from .runtime import KuangyuanRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the binary sensors enabled for this entry."""
    runtime: KuangyuanRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    keys = runtime.entity_keys
    entities: list[BinarySensorEntity] = []

    for uno in runtime.selected_unos:
        account = runtime.accounts[uno]
        if KEY_SUPPLY in keys:
            entities.append(KuangyuanSupplySensor(coordinator, account, runtime.account_label))
        if KEY_LOW_BALANCE in keys:
            entities.append(
                KuangyuanLowBalanceSensor(
                    coordinator, account, runtime.account_label, runtime.low_balance_threshold
                )
            )

    async_add_entities(entities)


class KuangyuanSupplySensor(KuangyuanMeterEntity, BinarySensorEntity):
    """Whether the meter is currently connected to gas."""

    _attr_translation_key = KEY_SUPPLY
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:gas-burner"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{account.uno}_{KEY_SUPPLY}"

    @property
    def is_on(self) -> bool | None:
        balance = self._balance()
        if balance is None or not balance.status:
            return None
        return balance.supplied

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        balance = self._balance()
        attrs = self.meter_attributes()
        if balance is not None and balance.status:
            attrs["status_text"] = balance.status
        return attrs


class KuangyuanLowBalanceSensor(KuangyuanMeterEntity, BinarySensorEntity):
    """Turns on when the balance falls below the configured threshold."""

    _attr_translation_key = KEY_LOW_BALANCE
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(
        self,
        coordinator: KuangyuanCoordinator,
        account: GasAccount,
        account_label: str,
        threshold: float,
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{account.uno}_{KEY_LOW_BALANCE}"
        self._threshold = threshold

    def update_threshold(self, threshold: float) -> None:
        """Refresh the threshold without recreating the entity."""
        self._threshold = threshold

    @property
    def is_on(self) -> bool | None:
        balance = self._balance()
        if balance is None or balance.balance is None:
            return None
        return balance.balance < self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        balance = self._balance()
        attrs = self.meter_attributes()
        attrs["threshold"] = self._threshold
        if balance is not None and balance.balance is not None:
            attrs["balance"] = round(balance.balance, 4)
        return attrs
