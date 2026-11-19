"""Daily and monthly gas consumption derived from Home Assistant history.

The values are computed **only** from this integration's own cumulative usage
sensor as stored by the Home Assistant recorder.  The portal is never asked for
them.

Definitions (local time, exactly as requested):

* daily   - ``usage(now) - usage(today 00:00)``
* monthly - ``usage(now) - usage(1st of this month 00:00)``

Baseline selection, in order of preference:

1. the last recorded reading at or before the boundary (exact answer),
2. the first reading shortly after the boundary (poll cadence gap),
3. the very first reading ever recorded for that sensor - this is what makes
   the value work when the integration was installed in the middle of the
   window, instead of staying unknown forever.

Whatever baseline is chosen, the consumption itself is recomputed on every
poll, so an increasing cumulative reading always increases daily/monthly usage.
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
# Default grace after the boundary, used when the caller does not know the
# polling interval.
LOOKAHEAD = timedelta(minutes=10)

INVALID_STATES = frozenset({"unknown", "unavailable", "none", ""})

# Provenance labels, exposed as entity attributes for troubleshooting.
SOURCE_BEFORE = "before_boundary"
SOURCE_AFTER = "after_boundary"
SOURCE_FIRST = "first_recorded"
SOURCE_LIVE = "first_observed"


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """One aggregation window: a local midnight boundary."""

    kind: str
    start: datetime


@dataclass(frozen=True, slots=True)
class Baseline:
    """A baseline reading and where it came from."""

    value: float
    source: str

    @property
    def exact(self) -> bool:
        """Whether the reading was taken at (or before) the boundary."""
        return self.source == SOURCE_BEFORE


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
    if isinstance(baseline, Baseline):
        baseline = baseline.value
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


def select_baseline(
    rows: Iterable[Any],
    boundary: datetime,
    *,
    grace: timedelta = LOOKAHEAD,
) -> Baseline | None:
    """Pick a baseline out of recorded rows.

    Kept separate from the database code so the ordering rules are testable
    without a recorder.
    """
    cutoff = boundary.timestamp()
    grace_end = (boundary + max(grace, LOOKAHEAD)).timestamp()
    before: tuple[float, float] | None = None
    after: tuple[float, float] | None = None
    first: tuple[float, float] | None = None

    for row in rows:
        value = _to_float(getattr(row, "state", row))
        if value is None:
            continue
        stamp = _row_timestamp(row)
        if stamp is None:
            continue
        if first is None or stamp < first[0]:
            first = (stamp, value)
        if stamp <= cutoff:
            if before is None or stamp > before[0]:
                before = (stamp, value)
        elif after is None or stamp < after[0]:
            after = (stamp, value)

    if before is not None:
        return Baseline(before[1], SOURCE_BEFORE)
    if after is not None:
        # A reading right after the boundary is the best proxy for it; a
        # reading much later means the integration was installed mid-window.
        source = SOURCE_AFTER if after[0] <= grace_end else SOURCE_FIRST
        return Baseline(after[1], source)
    if first is not None:
        return Baseline(first[1], SOURCE_FIRST)
    return None


def _row_timestamp(row: Any) -> float | None:
    """Best effort epoch timestamp of a recorded row."""
    value = getattr(row, "last_updated_ts", None)
    if isinstance(value, (int, float)):
        return float(value)
    value = getattr(row, "last_changed_ts", None)
    if isinstance(value, (int, float)):
        return float(value)
    for attribute in ("last_updated", "last_changed"):
        moment = getattr(row, attribute, None)
        if isinstance(moment, datetime):
            return moment.timestamp()
    return None


async def async_read_baselines(
    hass: HomeAssistant,
    entity_ids: Iterable[str],
    start: datetime,
    *,
    grace: timedelta = LOOKAHEAD,
) -> dict[str, Baseline]:
    """Return the baseline reading at ``start`` for each entity.

    Three queries per entity at most, all on indexed columns, executed in the
    recorder's executor so the event loop is never blocked:

    1. newest reading at or before the boundary (searched ``LOOKBACK`` back),
    2. oldest reading in ``[boundary, boundary + grace]``,
    3. oldest reading ever recorded, which covers an install that happened
       after the boundary.
    """
    wanted = [entity_id for entity_id in entity_ids if entity_id]
    if not wanted or not recorder_available(hass):
        return {}

    from homeassistant.components.recorder import db_schema, get_instance

    cutoff = start.timestamp()
    earliest = (start - LOOKBACK).timestamp()
    latest = (start + max(grace, LOOKAHEAD)).timestamp()
    invalid = tuple(INVALID_STATES)

    def _read() -> dict[str, Baseline]:
        result: dict[str, Baseline] = {}
        instance = get_instance(hass)
        session = instance.get_session()
        try:
            usable = (
                db_schema.States.state.isnot(None),
                db_schema.States.state.notin_(invalid),
            )
            for entity_id in wanted:
                meta = (
                    session.query(db_schema.StatesMeta.metadata_id)
                    .filter(db_schema.StatesMeta.entity_id == entity_id)
                    .one_or_none()
                )
                if meta is None:
                    continue
                metadata_id = meta[0]

                def _one(*conditions, descending: bool = True):
                    query = session.query(db_schema.States.state).filter(
                        db_schema.States.metadata_id == metadata_id, *conditions
                    )
                    order = (
                        db_schema.States.last_updated_ts.desc()
                        if descending
                        else db_schema.States.last_updated_ts.asc()
                    )
                    return query.order_by(order).limit(1).one_or_none()

                baselines: list[Baseline] = []

                # 1. newest reading at or before the boundary
                row = _one(
                    db_schema.States.last_updated_ts <= cutoff,
                    db_schema.States.last_updated_ts >= earliest,
                    *usable,
                )
                if row is not None and (value := _to_float(row[0])) is not None:
                    baselines.append(Baseline(value, SOURCE_BEFORE))

                # 2. oldest reading just after the boundary
                if not baselines:
                    row = _one(
                        db_schema.States.last_updated_ts > cutoff,
                        db_schema.States.last_updated_ts <= latest,
                        *usable,
                        descending=False,
                    )
                    if row is not None and (value := _to_float(row[0])) is not None:
                        baselines.append(Baseline(value, SOURCE_AFTER))

                # 3. oldest reading ever (integration installed mid-window)
                if not baselines:
                    row = _one(*usable, descending=False)
                    if row is not None and (value := _to_float(row[0])) is not None:
                        baselines.append(Baseline(value, SOURCE_FIRST))

                if baselines:
                    result[entity_id] = baselines[0]

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
