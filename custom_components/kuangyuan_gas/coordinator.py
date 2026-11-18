"""Data coordinator for the Kuangyuan Gas integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BalanceInfo,
    GasAccount,
    KuangyuanAPI,
    KuangyuanAuthError,
    KuangyuanConnectionError,
    KuangyuanError,
    KuangyuanSessionExpired,
    KuangyuanWafError,
    RechargeRecord,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# How many times a single poll may re-login and retry after the session dies.
MAX_RELOGIN_ATTEMPTS = 1


@dataclass(slots=True)
class MeterData:
    """Polled state of one meter."""

    account: GasAccount
    balance: BalanceInfo | None = None
    recharges: list[RechargeRecord] = field(default_factory=list)
    available: bool = True
    error: str | None = None
    recharges_loaded: bool = False


@dataclass(slots=True)
class GasData:
    """Polled state of one config entry."""

    meters: dict[str, MeterData] = field(default_factory=dict)
    last_refresh: datetime | None = None


class KuangyuanCoordinator(DataUpdateCoordinator[GasData]):
    """Poll every selected meter of one customer account.

    Meters are queried sequentially with a short delay: the portal sits behind
    a WAF that throttles parallel clients, and a single failure must not make
    the other meters unavailable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: KuangyuanAPI,
        accounts: dict[str, GasAccount],
        selected: list[str],
        *,
        scan_interval: int,
        recharge_pages: int,
        enable_recharges: bool,
    ) -> None:
        self.api = api
        self.entry = entry
        self.accounts = accounts
        self.selected = [uno for uno in selected if uno in accounts]
        self.recharge_pages = recharge_pages
        self.enable_recharges = enable_recharges
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> GasData:
        """Read every selected meter, isolating per-meter failures."""
        data = GasData()
        failures: list[str] = []
        auth_failed = False

        for index, uno in enumerate(self.selected):
            account = self.accounts[uno]
            previous = (self.data.meters.get(uno) if self.data else None)
            meter = MeterData(
                account=account,
                balance=previous.balance if previous else None,
                recharges=list(previous.recharges) if previous else [],
                recharges_loaded=previous.recharges_loaded if previous else False,
            )
            try:
                meter.balance = await self._async_read_balance(uno)
                if self.enable_recharges:
                    meter.recharges = await self.api.async_get_recharges(
                        uno, self.recharge_pages
                    )
                    meter.recharges_loaded = True
            except KuangyuanAuthError as err:
                auth_failed = True
                meter.available = previous.available if previous else False
                meter.error = str(err)
                failures.append(f"{uno}: {err}")
            except KuangyuanError as err:
                meter.available = previous.available if previous else False
                meter.error = str(err)
                failures.append(f"{uno}: {err}")
                _LOGGER.warning("户号 %s 查询失败，保留上次数据: %s", uno, err)
            except Exception as err:  # noqa: BLE001 - never kill the whole poll
                meter.available = previous.available if previous else False
                meter.error = str(err)
                failures.append(f"{uno}: {err}")
                _LOGGER.exception("户号 %s 查询时发生未预期错误", uno)
            data.meters[uno] = meter

            if index + 1 < len(self.selected):
                await asyncio.sleep(0.2)

        if auth_failed:
            # Surface this as an auth failure so Home Assistant offers the
            # re-authenticate flow instead of retrying a wrong password
            # forever.
            raise ConfigEntryAuthFailed("登录状态失效，请重新输入密码")

        if failures and len(failures) == len(self.selected):
            raise UpdateFailed("；".join(failures))

        data.last_refresh = datetime.now()
        return data

    async def _async_read_balance(self, uno: str) -> BalanceInfo:
        """Read one meter, re-logging in once when the session expired."""
        for attempt in range(MAX_RELOGIN_ATTEMPTS + 1):
            try:
                return await self.api.async_query_balance(uno)
            except (KuangyuanSessionExpired, KuangyuanWafError) as err:
                if attempt >= MAX_RELOGIN_ATTEMPTS:
                    raise
                _LOGGER.info("会话失效（%s），重新登录后重试 %s", err, uno)
                await self.api.async_invalidate()
                try:
                    await self.api.async_login()
                except KuangyuanConnectionError:
                    raise
        raise KuangyuanError("未知的查询失败")

    def meter(self, uno: str) -> MeterData | None:
        """Return the polled data of one meter."""
        if not self.data:
            return None
        return self.data.meters.get(uno)

    @property
    def total_balance(self) -> float | None:
        """Sum of every readable balance."""
        balances = [
            meter.balance.balance
            for meter in (self.data.meters.values() if self.data else [])
            if meter.balance and meter.balance.balance is not None
        ]
        return round(sum(balances), 2) if balances else None

