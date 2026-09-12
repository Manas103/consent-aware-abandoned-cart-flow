"""Timezone-aware quiet hours.

A send that is due during a recipient's local quiet window is deferred to
the next locally-allowed instant, converted back to UTC for storage. This
is a genuine timezone conversion using ``zoneinfo`` (IANA tz database),
not a fixed UTC offset window: the same UTC instant is "fine to send" for
a recipient in one zone and "quiet hours" for a recipient in another, and
the deferral target is computed in local wall-clock time (so it correctly
crosses DST transitions), then converted back to UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def is_quiet_hours(local_dt: datetime, start_hour: int, end_hour: int) -> bool:
    """True if local_dt's wall-clock hour falls in [start_hour, 24) u [0, end_hour).

    start_hour=21, end_hour=8 means quiet from 9pm to 8am local time,
    inclusive of 21:00 and exclusive of 08:00.
    """
    h = local_dt.hour
    if start_hour < end_hour:
        return start_hour <= h < end_hour
    # wraps midnight, e.g. 21 -> 8
    return h >= start_hour or h < end_hour


def next_allowed_local(local_dt: datetime, start_hour: int, end_hour: int) -> datetime:
    """The next local wall-clock instant that is not in quiet hours.

    If local_dt is already outside quiet hours, returns it unchanged.
    Otherwise returns end_hour:00 the same day (if local_dt's hour is
    before midnight-rollover into end_hour) or end_hour:00 the next day.
    """
    if not is_quiet_hours(local_dt, start_hour, end_hour):
        return local_dt
    candidate = local_dt.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if candidate <= local_dt:
        candidate = candidate + timedelta(days=1)
    return candidate


def next_allowed_utc(now_utc: datetime, tz_name: str, start_hour: int, end_hour: int) -> tuple[datetime, bool, datetime]:
    """Returns (next_send_utc, was_deferred, recipient_local_now).

    next_send_utc == now_utc (unchanged) when the send is not in quiet
    hours; otherwise it is the next allowed local instant converted to UTC.
    """
    tz = ZoneInfo(tz_name)
    local_now = now_utc.astimezone(tz)
    if not is_quiet_hours(local_now, start_hour, end_hour):
        return now_utc, False, local_now
    local_next = next_allowed_local(local_now, start_hour, end_hour)
    local_next = local_next.replace(tzinfo=tz)
    return local_next.astimezone(ZoneInfo("UTC")), True, local_now
