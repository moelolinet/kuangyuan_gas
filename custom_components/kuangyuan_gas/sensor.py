"""Sensor platform for the Kuangyuan Gas integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GasAccount
from .const import (
    DOMAIN,
    KEY_BALANCE,
    KEY_DAILY_USAGE,
    KEY_LAST_RECHARGE_AMOUNT,
    KEY_LAST_RECHARGE_TIME,
    KEY_MONTHLY_USAGE,
    KEY_RECHARGE_COUNT,
    KEY_UPDATED_AT,
    KEY_USAGE,
)
from .coordinator import KuangyuanCoordinator
from .entity import KuangyuanMeterEntity, portal_local
from .runtime import KuangyuanRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the sensors enabled for this entry."""
    runtime: KuangyuanRuntime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    keys = runtime.entity_keys
    label = runtime.account_label
    entities: list[SensorEntity] = []

    for uno in runtime.selected_unos:
        account = runtime.accounts[uno]
        factories: list[tuple[str, type[SensorEntity]]] = [
            (KEY_BALANCE, KuangyuanBalanceSensor),
            (KEY_USAGE, KuangyuanUsageSensor),
            (KEY_DAILY_USAGE, KuangyuanDailyUsageSensor),
            (KEY_MONTHLY_USAGE, KuangyuanMonthlyUsageSensor),
            (KEY_UPDATED_AT, KuangyuanUpdatedAtSensor),
            (KEY_LAST_RECHARGE_AMOUNT, KuangyuanLastRechargeAmountSensor),
            (KEY_LAST_RECHARGE_TIME, KuangyuanLastRechargeTimeSensor),
            (KEY_RECHARGE_COUNT, KuangyuanRechargeCountSensor),
        ]
        for key, factory in factories:
            if key in keys:
                entities.append(factory(coordinator, account, label))

    async_add_entities(entities)
    # The usage sensors only exist now, so the first recorder lookup happens
    # after the platform added them.
    if coordinator.enable_consumption:
        await coordinator.async_refresh_consumption()
        coordinator.async_update_listeners()


class KuangyuanBalanceSensor(KuangyuanMeterEntity, SensorEntity):
    """Current balance of one gas meter."""

    _attr_translation_key = KEY_BALANCE
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "CNY"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{account.uno}_{KEY_BALANCE}"

    @property
    def native_value(self) -> float | None:
        balance = self._balance()
        if balance is None or balance.balance is None:
            return None
        return round(balance.balance, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        balance = self._balance()
        attrs = self.meter_attributes()
        if balance is not None:
            if balance.balance_text:
                attrs["balance_text"] = balance.balance_text
            if balance.status:
                attrs["self_supplied"] = balance.supplied
        return attrs


class KuangyuanUsageSensor(KuangyuanMeterEntity, SensorEntity):
    """Cumulative gas usage reported by the portal."""

    _attr_translation_key = KEY_USAGE
    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:gas-cylinder"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{account.uno}_{KEY_USAGE}"

    @property
    def native_value(self) -> float | None:
        balance = self._balance()
        if balance is None or balance.usage is None:
            return None
        return round(balance.usage, 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        balance = self._balance()
        attrs = self.meter_attributes()
        if balance is not None and balance.usage_text:
            attrs["usage_text"] = balance.usage_text
        return attrs


class _KuangyuanConsumptionSensor(KuangyuanMeterEntity, SensorEntity):
    """Shared implementation of the locally derived consumption sensors.

    Both values come from the recorder history of the cumulative usage sensor,
    never from the portal.
    """

    _attr_device_class = SensorDeviceClass.GAS
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-bar"

    _window_attr = "daily"
    _baseline_attr = "daily_baseline"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)

    @property
    def native_value(self) -> float | None:
        meter = self.meter
        return getattr(meter, self._window_attr, None) if meter else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        meter = self.meter
        attrs = self.meter_attributes()
        attrs["source"] = "home_assistant_recorder"
        if meter is not None:
            baseline = getattr(meter, self._baseline_attr, None)
            current = meter.balance.usage if meter.balance else None
            if baseline is not None:
                attrs["baseline_usage"] = round(baseline, 4)
            if current is not None:
                attrs["current_usage"] = round(current, 4)
            if baseline is None or current is None:
                attrs["note"] = (
                    "等待 Home Assistant 历史数据" if baseline is None else "当前读数不可用"
                )
        return attrs


class KuangyuanDailyUsageSensor(_KuangyuanConsumptionSensor):
    """Gas consumed since local midnight today."""

    _attr_translation_key = KEY_DAILY_USAGE
    _window_attr = "daily_usage"
    _baseline_attr = "daily_baseline"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{account.uno}_{KEY_DAILY_USAGE}"


class KuangyuanMonthlyUsageSensor(_KuangyuanConsumptionSensor):
    """Gas consumed since the 1st of this month at local midnight."""

    _attr_translation_key = KEY_MONTHLY_USAGE
    _window_attr = "monthly_usage"
    _baseline_attr = "monthly_baseline"
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{account.uno}_{KEY_MONTHLY_USAGE}"
        )


class KuangyuanUpdatedAtSensor(KuangyuanMeterEntity, SensorEntity):
    """Timestamp at which the portal produced the reading."""

    _attr_translation_key = KEY_UPDATED_AT
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{account.uno}_{KEY_UPDATED_AT}"

    @property
    def native_value(self) -> datetime | None:
        balance = self._balance()
        if balance is None:
            return None
        return portal_local(balance.operated_at)


class KuangyuanLastRechargeAmountSensor(KuangyuanMeterEntity, SensorEntity):
    """Amount of the most recent recharge."""

    _attr_translation_key = KEY_LAST_RECHARGE_AMOUNT
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "CNY"
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-plus"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{account.uno}_{KEY_LAST_RECHARGE_AMOUNT}"
        )

    def _latest(self):
        meter = self.meter
        if not meter or not meter.recharges:
            return None
        return meter.recharges[0]

    @property
    def native_value(self) -> float | None:
        latest = self._latest()
        return latest.amount if latest else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = self._latest()
        attrs = self.meter_attributes()
        if latest is not None:
            if latest.order_id:
                attrs["order_id"] = latest.order_id
            if latest.pay_method:
                attrs["pay_method"] = latest.pay_method
            if latest.pay_result:
                attrs["pay_result"] = latest.pay_result
            if latest.paid_at is not None:
                attrs["paid_at"] = latest.paid_at.isoformat(sep=" ", timespec="seconds")
        return attrs


class KuangyuanLastRechargeTimeSensor(KuangyuanMeterEntity, SensorEntity):
    """Timestamp of the most recent recharge."""

    _attr_translation_key = KEY_LAST_RECHARGE_TIME
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{account.uno}_{KEY_LAST_RECHARGE_TIME}"
        )

    @property
    def native_value(self) -> datetime | None:
        meter = self.meter
        if not meter or not meter.recharges:
            return None
        return portal_local(meter.recharges[0].paid_at)


class KuangyuanRechargeCountSensor(KuangyuanMeterEntity, SensorEntity):
    """Number of recharge records found in the fetched history pages."""

    _attr_translation_key = KEY_RECHARGE_COUNT
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:receipt-text-clock"
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: KuangyuanCoordinator, account: GasAccount, account_label: str
    ) -> None:
        super().__init__(coordinator, account, account_label)
        self._attr_unique_id = (
            f"{coordinator.entry.entry_id}_{account.uno}_{KEY_RECHARGE_COUNT}"
        )

    @property
    def native_value(self) -> int | None:
        meter = self.meter
        if not meter or not meter.recharges_loaded:
            return None
        return len(meter.recharges)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = self.meter_attributes()
        attrs["fetched_pages"] = self.coordinator.recharge_pages
        return attrs
