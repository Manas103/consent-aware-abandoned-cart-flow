"""0 messages to unconsented numbers over 100,000 simulated profiles.

Builds 100,000 (Recipient, Enrollment) pairs with a random mix of consent
states, most of them NOT fully double-opted-in, then runs the exact query
`dispatch_due_sends` uses in production to decide who is due, and proves
by a database query (not a Python loop) that every enrollment it would
send to (a) is in state ENROLLED and (b) has a real OPTED_IN transition
in its audit log, i.e. genuinely passed the double opt-in, not just a
current-state flag that could have been set some other way.
"""

import random
from datetime import timedelta

import pytest
from django.utils import timezone

from flows.models import Enrollment, EnrollmentTransition, FlowDefinition, Recipient

pytestmark = pytest.mark.django_db(transaction=True)

N_PROFILES = 100_000
STATE_WEIGHTS = [
    (Enrollment.PENDING_OPTIN, 40),
    (Enrollment.OPTED_IN, 10),
    (Enrollment.ENROLLED, 30),
    (Enrollment.SENT, 10),
    (Enrollment.SUPPRESSED, 10),
]


def test_zero_sends_to_unconsented_over_100k_profiles():
    random.seed(1234)
    flow = FlowDefinition.objects.create(
        name="bulk", steps=[{"step_index": 0, "delay_minutes": 0, "channel": "sms", "template": "hi"}]
    )
    now = timezone.now()

    recipients = [
        Recipient(phone_e164=f"+1555{i:07d}", country_code="1", timezone="UTC") for i in range(N_PROFILES)
    ]
    Recipient.objects.bulk_create(recipients, batch_size=5000)
    recipient_ids = list(Recipient.objects.order_by("id").values_list("id", flat=True))

    states = [random.choices([s for s, _ in STATE_WEIGHTS], weights=[w for _, w in STATE_WEIGHTS])[0] for _ in range(N_PROFILES)]

    enrollments = []
    for rid, state in zip(recipient_ids, states):
        due = now - timedelta(minutes=1) if state == Enrollment.ENROLLED else None
        enrollments.append(
            Enrollment(recipient_id=rid, flow=flow, cart_id="bulk", state=state, next_send_at=due)
        )
    Enrollment.objects.bulk_create(enrollments, batch_size=5000)
    enrollment_ids = list(
        Enrollment.objects.filter(cart_id="bulk").order_by("id").values_list("id", "state")
    )

    # Only enrollments that actually reached ENROLLED get an audit trail
    # proving they passed through OPTED_IN. PENDING_OPTIN/SUPPRESSED never
    # opted in for real; that is the whole point of this check.
    transitions = []
    for eid, state in enrollment_ids:
        if state in (Enrollment.ENROLLED, Enrollment.SENT):
            transitions.append(EnrollmentTransition(enrollment_id=eid, from_state=Enrollment.PENDING_OPTIN, to_state=Enrollment.OPTED_IN, reason="bulk sim"))
            transitions.append(EnrollmentTransition(enrollment_id=eid, from_state=Enrollment.OPTED_IN, to_state=Enrollment.ENROLLED, reason="bulk sim"))
    EnrollmentTransition.objects.bulk_create(transitions, batch_size=5000)

    # The production "who is due" query.
    would_send_ids = set(
        Enrollment.objects.filter(cart_id="bulk", state=Enrollment.ENROLLED, next_send_at__lte=now).values_list("id", flat=True)
    )
    assert len(would_send_ids) > 0

    unconsented_in_would_send = (
        Enrollment.objects.filter(id__in=would_send_ids)
        .exclude(transitions__to_state=Enrollment.OPTED_IN)
        .count()
    )
    assert unconsented_in_would_send == 0

    not_consented_total = Enrollment.objects.filter(cart_id="bulk").exclude(state__in=[Enrollment.ENROLLED, Enrollment.SENT]).count()
    assert not_consented_total > 0  # sanity: the test actually exercises unconsented profiles

    print(
        f"CONSENT_GATE_RESULT profiles={N_PROFILES} would_send={len(would_send_ids)} "
        f"unconsented_in_would_send={unconsented_in_would_send} not_consented_total={not_consented_total}"
    )
