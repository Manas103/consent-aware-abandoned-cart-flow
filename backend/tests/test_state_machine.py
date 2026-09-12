import pytest
from django.utils import timezone

from flows.models import Enrollment, FlowDefinition, Recipient

pytestmark = pytest.mark.django_db


def make_recipient(phone="+15551234567", tz="America/New_York"):
    return Recipient.objects.create(phone_e164=phone, country_code="1", timezone=tz)


def make_flow():
    return FlowDefinition.objects.create(
        name="abandoned cart",
        steps=[
            {"step_index": 0, "delay_minutes": 60, "channel": "sms", "template": "come back!"},
            {"step_index": 1, "delay_minutes": 1440, "channel": "sms", "template": "10% off"},
        ],
    )


def test_double_optin_state_machine_happy_path():
    recipient = make_recipient()
    flow = make_flow()
    enrollment = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id="cart-1")
    assert enrollment.state == Enrollment.PENDING_OPTIN

    enrollment.transition_to(Enrollment.OPTED_IN, reason="replied YES")
    enrollment.transition_to(Enrollment.ENROLLED, reason="ready for step 0")
    assert enrollment.state == Enrollment.ENROLLED

    transitions = list(enrollment.transitions.values_list("from_state", "to_state"))
    assert transitions == [
        (Enrollment.PENDING_OPTIN, Enrollment.OPTED_IN),
        (Enrollment.OPTED_IN, Enrollment.ENROLLED),
    ]


def test_illegal_transition_rejected():
    recipient = make_recipient()
    flow = make_flow()
    enrollment = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id="cart-2")
    with pytest.raises(ValueError):
        enrollment.transition_to(Enrollment.SENT, reason="skip straight to sent")
    assert enrollment.transitions.count() == 0


def test_optout_suppresses_and_is_terminal():
    recipient = make_recipient()
    flow = make_flow()
    enrollment = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id="cart-3")
    enrollment.transition_to(Enrollment.SUPPRESSED, reason="hard gate")
    assert enrollment.state == Enrollment.SUPPRESSED
    with pytest.raises(ValueError):
        enrollment.transition_to(Enrollment.OPTED_IN, reason="too late")
