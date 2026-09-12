from datetime import datetime, timezone as dt_timezone

import pytest

from flows.quiet_hours import is_quiet_hours, next_allowed_utc

pytestmark = pytest.mark.django_db


def test_is_quiet_hours_wraps_midnight():
    late = datetime(2026, 1, 1, 23, 0)
    early = datetime(2026, 1, 1, 3, 0)
    daytime = datetime(2026, 1, 1, 14, 0)
    assert is_quiet_hours(late, 21, 8) is True
    assert is_quiet_hours(early, 21, 8) is True
    assert is_quiet_hours(daytime, 21, 8) is False


def test_send_during_quiet_hours_is_deferred_not_dropped():
    # 02:00 in America/New_York in January is 07:00 UTC (EST, UTC-5).
    now_utc = datetime(2026, 1, 15, 7, 0, tzinfo=dt_timezone.utc)
    next_utc, deferred, local_now = next_allowed_utc(now_utc, "America/New_York", 21, 8)
    assert deferred is True
    assert local_now.hour == 2
    # deferred to 08:00 local = 13:00 UTC the same day
    assert next_utc.hour == 13
    assert next_utc.date() == now_utc.date()


def test_send_outside_quiet_hours_is_not_deferred():
    now_utc = datetime(2026, 1, 15, 18, 0, tzinfo=dt_timezone.utc)  # 13:00 local NY
    next_utc, deferred, _ = next_allowed_utc(now_utc, "America/New_York", 21, 8)
    assert deferred is False
    assert next_utc == now_utc


def test_deferred_send_integration_writes_log_and_reschedules():
    from flows.models import DeferredSendLog, Enrollment, FlowDefinition, Recipient
    from flows.tasks import execute_send

    recipient = Recipient.objects.create(phone_e164="+15559990000", country_code="1", timezone="America/New_York")
    flow = FlowDefinition.objects.create(
        name="f", steps=[{"step_index": 0, "delay_minutes": 0, "channel": "sms", "template": "hi"}]
    )
    enrollment = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id="c1")
    enrollment.transition_to(Enrollment.OPTED_IN, reason="x")
    enrollment.transition_to(Enrollment.ENROLLED, reason="x")

    import django.utils.timezone as dj_tz
    from unittest import mock

    quiet_instant = datetime(2026, 1, 15, 7, 0, tzinfo=dt_timezone.utc)  # 02:00 local NY
    with mock.patch.object(dj_tz, "now", return_value=quiet_instant):
        result = execute_send(enrollment.id, 0)

    assert result == "deferred"
    assert enrollment.send_records.count() == 0  # not dropped silently as "sent", and not lost
    log = DeferredSendLog.objects.get(enrollment=enrollment)
    assert log.deferred_to_utc.hour == 13
    enrollment.refresh_from_db()
    assert enrollment.next_send_at.hour == 13
    assert enrollment.state == Enrollment.ENROLLED  # still enrolled, will be retried, not suppressed
