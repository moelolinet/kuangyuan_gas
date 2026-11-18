"""Daily and monthly gas consumption derived from Home Assistant history.

The values are computed **only** from this integration's own cumulative usage
sensor as stored by the Home Assistant recorder.  The portal is never asked for
them.

Definitions (local time, exactly as requested):

* daily   - ``usage(now) - usage(today 00:00)``
* monthly - ``usage(now) - usage(1st of this month 00:00)``

The baseline is the last recorded reading at or before that midnight, so a poll
that runs at 09:00 still compares against midnight instead of against the first
poll of the day.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# How far before the boundary the last reading is searched for.  Polls are
# usually 30 minutes apart; three days leaves room for a paused integration
# while staying an indexed range scan.
LOOKBACK = timedelta(days=3)
# How far after the boundary to look if nothing was recorded before it.
LOOKAHEAD = timedelta(minutes=10)

INVALID_STATES = frozenset({"unknown", "unavailable", "none", ""})


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """One aggregation window: a local midnight boundary."""

    kind: str
    start: datetime


def day_window(now: datetime) -> UsageWindow:
    """Window covering today, starting at local midnight."""
    return UsageWindow(
        kind="day",
        start=now.replace(hour=0, minute=0, second=0, microsecond=0),
    )


def month_window(now: datetime) -> UsageWindow:
    """Window covering the current month, starting on its 1st at midnight."""
    return UsageWindow(
        kind="month",
        start=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    )


def compute_consumption(current: Any, baseline: Any) -> float | None:
    """Difference between the current and the baseline reading.

    ``None`` is returned for missing data and for a negative difference
    (meter replacement / reset), so a reset never shows up as a huge negative
    consumption.
    """
    if current is None or baseline is None:
        return None
    try:
        delta = float(current) - float(baseline)
    except (TypeError, ValueError):
        return None
    if delta < 0:
        return None
    return round(delta, 3)


def _to_float(value: Any) -> float | None:
    """Read a numeric value out of a recorded state or a raw string."""
    value = getattr(value, "state", value)
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in INVALID_STATES:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def recorder_available(hass: HomeAssistant) -> bool:
    """Whether the recorder component is loaded in this instance."""
    return "recorder" in hass.config.components


async def async_read_baselines(
    hass: HomeAssistant,
    entity_ids: Iterable[str],
    start: datetime,
) -> dict[str, float | None]:
    """Return the recorded usage value at ``start`` for each entity.

    The recorder database is queried directly through the recorder's own
    session: one indexed lookup per entity for the newest state at or before
    the boundary.  Everything runs in the recorder executor, so the event loop
    is never blocked.
    """
    wanted = [entity_id for entity_id in entity_ids if entity_id]
    if not wanted or not recorder_available(hass):
        return {}

    from homeassistant.components.recorder import db_schema, get_instance

    cutoff = start.timestamp()
    earliest = (start - LOOKBACK).timestamp()
    latest = (start + LOOKAHEAD).timestamp()

    def _read() -> dict[str, float | None]:
        result: dict[str, float | None] = {}
        instance = get_instance(hass)
        session = instance.get_session()
        try:
            for entity_id in wanted:
                meta = (
                    session.query(db_schema.StatesMeta.metadata_id)
                    .filter(db_schema.StatesMeta.entity_id == entity_id)
                    .one_or_none()
                )
                if meta is None:
                    continue

                row = (
                    session.query(db_schema.States.state)
                    .filter(
                        db_schema.States.metadata_id == meta[0],
                        db_schema.States.last_updated_ts <= cutoff,
                        db_schema.States.last_updated_ts >= earliest,
                    )
                    .order_by(db_schema.States.last_updated_ts.desc())
                    .limit(1)
                    .one_or_none()
                )
                if row is None:
                    # Nothing before the boundary: take the first reading after
                    # it, which is the closest approximation available.
                    row = (
                        session.query(db_schema.States.state)
                        .filter(
                            db_schema.States.metadata_id == meta[0],
                            db_schema.States.last_updated_ts > cutoff,
                            db_schema.States.last_updated_ts <= latest,
                        )
                        .order_by(db_schema.States.last_updated_ts.asc())
                        .limit(1)
                        .one_or_none()
                    )
                if row is None:
                    continue
                value = _to_float(row[0])
                if value is not None:
                    result[entity_id] = value
            session.rollback()
            session.close()
        except Exception as err:  # noqa: BLE001 - history must never break polling
            _LOGGER.debug("Reading usage history failed: %s", err)
            try:
                session.rollback()
                session.close()
            except Exception:  # noqa: BLE001
                pass
            return {}
        return result

    try:
        return await get_instance(hass).async_add_executor_job(_read)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Recorder unavailable for usage history: %s", err)
        return {}
