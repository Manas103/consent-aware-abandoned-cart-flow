import pytest

from flows.models import Enrollment, FlowDefinition, Recipient, SendRecord, compute_idempotency_key
from flows.tasks import execute_send

pytestmark = pytest.mark.django_db


def _enrolled():
    recipient = Recipient.objects.create(phone_e164="+15551110000", country_code="1", timezone="UTC")
    flow = FlowDefinition.objects.create(
        name="f", steps=[{"step_index": 0, "delay_minutes": 0, "channel": "sms", "template": "hi"}]
    )
    e = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id="c")
    e.transition_to(Enrollment.OPTED_IN, reason="x")
    e.transition_to(Enrollment.ENROLLED, reason="x")
    return e


def test_idempotency_key_is_deterministic_hash():
    assert compute_idempotency_key(5, 0) == compute_idempotency_key(5, 0)
    assert compute_idempotency_key(5, 0) != compute_idempotency_key(5, 1)


def test_second_send_of_same_step_is_a_noop_not_a_duplicate():
    e = _enrolled()
    result1 = execute_send(e.id, 0)
    assert result1 == "sent"
    e.refresh_from_db()
    # move back to ENROLLED at the same step to simulate a redelivered task
    e.current_step_index = 0
    if e.state != Enrollment.ENROLLED:
        e.state = Enrollment.ENROLLED
        e.save(update_fields=["state"])
    result2 = execute_send(e.id, 0)
    assert result2 == "already-sent"
    assert SendRecord.objects.filter(enrollment=e, step_index=0).count() == 1
