"""Shared entity helpers for the Kuangyuan Gas integration."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import GasAccount
from .const import DEVICE_PREFIX, DOMAIN, MANUFACTURER
from .coordinator import KuangyuanCoordinator, MeterData

# The portal reports local wall clock time and its customers are served by the
# Putian (Fujian) business unit, so naive timestamps are interpreted as UTC+8.
PORTAL_TZ = timezone(timedelta(hours=8))


def slug(value: str) -> str:
    """Return a lowercase ascii token usable inside an entity id."""
    token = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return token or "meter"


def mask_phone(phone: str) -> str:
    """Mask a mobile number for display (138****0001)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) >= 7:
        return f"{digits[:3]}****{digits[-4:]}"
    return digits or "account"


def portal_local(value: datetime | None) -> datetime | None:
    """Attach the portal timezone to a naive portal timestamp."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=PORTAL_TZ)
    return value


class KuangyuanMeterEntity(CoordinatorEntity[KuangyuanCoordinator]):
    """Base class for entities of a single gas meter (用户编号).

    Each meter is a standalone device: the integration does not create a
    separate "account" device, so the integration page only lists the meters
    themselves and every entity lives on one of them.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: KuangyuanCoordinator,
        account: GasAccount,
        account_label: str,
    ) -> None:
        super().__init__(coordinator)
        self._uno = account.uno
        self._account = account
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.entry_id}_{account.uno}")},
            name=f"{DEVICE_PREFIX} {account.uno}",
            manufacturer=MANUFACTURER,
            model="燃气表",
        )

    @property
    def meter(self) -> MeterData | None:
        """Polled data of this meter (``None`` before the first refresh)."""
        return self.coordinator.meter(self._uno)

    @property
    def available(self) -> bool:
        """Availability follows the poll and this meter's own result."""
        if not self.coordinator.last_update_success:
            return False
        meter = self.meter
        return bool(meter and meter.available)

    def _balance(self):
        meter = self.meter
        return meter.balance if meter else None

    def meter_attributes(self) -> dict[str, Any]:
        """Common diagnostic attributes shared by every meter entity."""
        meter = self.meter
        balance = meter.balance if meter else None
        attrs: dict[str, Any] = {"uno": self._uno}
        if self._account.category:
            attrs["category"] = self._account.category
        if balance is not None:
            if balance.status:
                attrs["status_text"] = balance.status
            if balance.operated_at is not None:
                attrs["portal_updated_at"] = balance.operated_at.isoformat(
                    sep=" ", timespec="seconds"
                )
        if meter is not None and meter.error:
            attrs["last_error"] = meter.error
        return attrs
