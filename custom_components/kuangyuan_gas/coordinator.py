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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
from .const import DOMAIN, KEY_USAGE
from .usage import (
    async_read_baselines,
    compute_consumption,
    day_window,
    month_window,
    recorder_available,
)

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
    daily_usage: float | None = None
    monthly_usage: float | None = None
    daily_baseline: float | None = None
    monthly_baseline: float | None = None
    daily_baseline_source: str | None = None
    monthly_baseline_source: str | None = None


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

    Daily/monthly consumption is **not** taken from the portal.  It is computed
    from this integration's own cumulative usage sensor as stored by the Home
    Assistant recorder, comparing the current reading with the reading at the
    relevant local midnight.
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
        enable_consumption: bool = False,
    ) -> None:
        self.api = api
        self.entry = entry
        self.accounts = accounts
        self.selected = [uno for uno in selected if uno in accounts]
        self.recharge_pages = recharge_pages
        self.enable_recharges = enable_recharges
        self.enable_consumption = enable_consumption
        # Recorded baseline per window, so the recorder is queried at most once
        # per day/month instead of on every poll.
        self._baselines: dict[tuple[str, str], tuple[datetime, Any]] = {}
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
            previous = self.data.meters.get(uno) if self.data else None
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
                _LOGGER.warning("Meter %s query failed, keeping previous data: %s", uno, err)
            except Exception as err:  # noqa: BLE001 - never kill the whole poll
                meter.available = previous.available if previous else False
                meter.error = str(err)
                failures.append(f"{uno}: {err}")
                _LOGGER.exception("Unexpected error while querying meter %s", uno)
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

        if self.enable_consumption:
            await self.async_refresh_consumption(data)

        data.last_refresh = dt_util.now()
        return data

    # ------------------------------------------------------------ consumption
    def usage_entity_ids(self) -> dict[str, str]:
        """Resolve each meter's cumulative usage sensor entity id."""
        registry = er.async_get(self.hass)
        resolved: dict[str, str] = {}
        for uno in self.selected:
            unique_id = f"{self.entry.entry_id}_{uno}_{KEY_USAGE}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                resolved[uno] = entity_id
        return resolved

    async def async_refresh_consumption(self, data: GasData | None = None) -> None:
        """Recompute daily/monthly consumption from the recorder history.

        The *baseline* (the reading at the window boundary) is cached per meter
        and per window, because it does not change during that window.  The
        *consumption* is recomputed from the freshly polled cumulative reading
        on every call, so an increasing meter immediately increases the daily
        and monthly values.
        """
        if not self.enable_consumption:
            return
        target = data if data is not None else self.data
        if target is None:
            return

        entity_ids = self.usage_entity_ids()
        if not entity_ids:
            return

        now = dt_util.now()
        windows = {"day": day_window(now), "month": month_window(now)}

        if recorder_available(self.hass):
            for kind, window in windows.items():
                # Only meters without a baseline for this exact window need a
                # database read.  A meter whose lookup previously failed is
                # retried on the next poll instead of staying unknown forever.
                # Only an exact baseline (a reading at or before the
                # boundary) is cached for the whole window.  A provisional one
                # (post-boundary or first-recorded) is retried on every poll,
                # because the recorder may fill in better history later - e.g.
                # right after the integration was installed mid-window.
                missing = [
                    entity_id
                    for entity_id in entity_ids.values()
                    if not self._baseline_is_final(kind, entity_id, window.start)
                ]
                if not missing:
                    continue
                values = await async_read_baselines(self.hass, missing, window.start)
                for entity_id, baseline in values.items():
                    self._baselines[(kind, entity_id)] = (window.start, baseline)
                _LOGGER.debug(
                    "Usage baseline (%s) resolved for %d of %d pending meters",
                    kind,
                    len(values),
                    len(missing),
                )

        for uno, meter in target.meters.items():
            entity_id = entity_ids.get(uno)
            if not entity_id:
                continue
            current = meter.balance.usage if meter.balance else None
            day_baseline = self._cached_baseline("day", entity_id, windows["day"].start)
            month_baseline = self._cached_baseline(
                "month", entity_id, windows["month"].start
            )
            meter.daily_baseline = day_baseline.value if day_baseline else None
            meter.monthly_baseline = month_baseline.value if month_baseline else None
            meter.daily_baseline_source = day_baseline.source if day_baseline else None
            meter.monthly_baseline_source = (
                month_baseline.source if month_baseline else None
            )
            meter.daily_usage = compute_consumption(current, day_baseline)
            meter.monthly_usage = compute_consumption(current, month_baseline)

    def _cached_baseline(self, kind: str, entity_id: str, window_start: datetime):
        """Return the cached baseline of one meter for the current window."""
        cached = self._baselines.get((kind, entity_id))
        if cached is None or cached[0] != window_start:
            return None
        return cached[1]

    def _baseline_is_final(
        self, kind: str, entity_id: str, window_start: datetime
    ) -> bool:
        """Whether the cached baseline is exact and needs no more lookups."""
        baseline = self._cached_baseline(kind, entity_id, window_start)
        return baseline is not None and baseline.exact

    # ------------------------------------------------------------------ meter
    async def _async_read_balance(self, uno: str) -> BalanceInfo:
        """Read one meter, re-logging in once when the session expired."""
        for attempt in range(MAX_RELOGIN_ATTEMPTS + 1):
            try:
                return await self.api.async_query_balance(uno)
            except (KuangyuanSessionExpired, KuangyuanWafError) as err:
                if attempt >= MAX_RELOGIN_ATTEMPTS:
                    raise
                _LOGGER.info("Session expired (%s), logging in again for %s", err, uno)
                await self.api.async_invalidate()
                try:
                    await self.api.async_login()
                except KuangyuanConnectionError:
                    raise
        raise KuangyuanError("unknown query failure")

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

