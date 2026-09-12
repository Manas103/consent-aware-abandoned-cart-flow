"""
Data model for the consent-aware abandoned-cart flow.

State machine (Enrollment.state), audited by EnrollmentTransition:

    PENDING_OPTIN -> OPTED_IN -> ENROLLED -> SENT
                  (->  SUPPRESSED)          (-> SUPPRESSED)

Every state change goes through Enrollment.transition_to(), which is the
only place a state is ever written, and which always writes a matching
EnrollmentTransition row in the same database transaction. This is what
makes the state machine "audited": the current state is never the only
record of history, the transition log is.
"""

from __future__ import annotations

import hashlib

from django.db import models


class FlowDefinition(models.Model):
    """A trigger + ordered message steps, authored by the React flow builder."""

    name = models.CharField(max_length=200)
    trigger_type = models.CharField(max_length=64, default="cart_abandoned")
    # [{"step_index": 0, "delay_minutes": 60, "channel": "sms", "template": "..."}]
    steps = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - debug convenience only
        return f"FlowDefinition({self.name!r}, {len(self.steps)} steps)"

    def step(self, step_index: int) -> dict:
        for s in self.steps:
            if s["step_index"] == step_index:
                return s
        raise KeyError(f"flow {self.pk} has no step_index {step_index}")

    def max_step_index(self) -> int:
        return max(s["step_index"] for s in self.steps)


class Recipient(models.Model):
    """A phone number, its consent identity, and the timezone quiet hours use.

    ``timezone`` is an IANA zone name. It is set explicitly at creation from
    ``country_code`` via ``flows.timezones.timezone_for_country_code``, a
    small illustrative mapping (one representative zone per calling code).
    This is a disclosed simplification: several countries this maps (the
    US, Canada, Australia, Russia) genuinely span multiple zones, and a
    production system would need per-number zone data (from a carrier
    lookup or an explicit user-provided zone) rather than a country-code
    guess. See README Limitations.
    """

    phone_e164 = models.CharField(max_length=20, unique=True)
    country_code = models.CharField(max_length=4)
    timezone = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover
        return self.phone_e164


class Enrollment(models.Model):
    PENDING_OPTIN = "PENDING_OPTIN"
    OPTED_IN = "OPTED_IN"
    ENROLLED = "ENROLLED"
    SENT = "SENT"
    SUPPRESSED = "SUPPRESSED"

    STATE_CHOICES = [
        (PENDING_OPTIN, PENDING_OPTIN),
        (OPTED_IN, OPTED_IN),
        (ENROLLED, ENROLLED),
        (SENT, SENT),
        (SUPPRESSED, SUPPRESSED),
    ]

    # Legal transitions out of each state. PENDING_OPTIN and OPTED_IN can
    # also move to SUPPRESSED (recipient opts out, or a hard gate fires,
    # before ever receiving a message).
    ALLOWED_TRANSITIONS = {
        PENDING_OPTIN: {OPTED_IN, SUPPRESSED},
        OPTED_IN: {ENROLLED, SUPPRESSED},
        ENROLLED: {SENT, SUPPRESSED},
        SENT: {ENROLLED, SUPPRESSED},  # SENT -> ENROLLED: advance to the next step
        SUPPRESSED: set(),  # terminal
    }

    recipient = models.ForeignKey(Recipient, on_delete=models.CASCADE, related_name="enrollments")
    flow = models.ForeignKey(FlowDefinition, on_delete=models.CASCADE, related_name="enrollments")
    cart_id = models.CharField(max_length=64)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=PENDING_OPTIN)
    current_step_index = models.IntegerField(default=0)
    next_send_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Backs the audience-membership query: "which enrollments are
            # due right now". See docs/audience_query_before.txt /
            # audience_query_after.txt and README "Findings".
            models.Index(fields=["state", "next_send_at"], name="ix_state_next_send_at"),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Enrollment({self.pk}, {self.state})"

    def transition_to(self, new_state: str, reason: str) -> None:
        """The only sanctioned way to change ``state``. Writes the audit row."""
        if new_state not in self.ALLOWED_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"illegal transition {self.state} -> {new_state}")
        old_state = self.state
        self.state = new_state
        self.save(update_fields=["state", "updated_at"])
        EnrollmentTransition.objects.create(
            enrollment=self,
            from_state=old_state,
            to_state=new_state,
            reason=reason,
        )


class EnrollmentTransition(models.Model):
    """Append-only audit log. Never updated or deleted."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="transitions")
    from_state = models.CharField(max_length=20)
    to_state = models.CharField(max_length=20)
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.from_state} -> {self.to_state} ({self.reason})"


def compute_idempotency_key(enrollment_id: int, step_index: int) -> str:
    """sha256(enrollment_id:step_index). Deterministic: every retry of the
    same logical send recomputes the identical key, so the unique
    constraint on SendRecord.idempotency_key is what makes a send
    exactly-once regardless of how many times the task is delivered."""
    return hashlib.sha256(f"{enrollment_id}:{step_index}".encode()).hexdigest()


class SendRecord(models.Model):
    """One row per message actually sent. ``idempotency_key`` is unique, so
    a second attempt to write the same (enrollment, step) send hits an
    IntegrityError instead of a duplicate row; the task treats that as
    "already sent", not an error. This is the entire duplicate-send
    defense; see flows.tasks.send_step and the worker-kill benchmark."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="send_records")
    step_index = models.IntegerField()
    idempotency_key = models.CharField(max_length=64, unique=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    worker_pid = models.IntegerField()
    trial_id = models.CharField(max_length=64, blank=True, default="")

    def __str__(self) -> str:  # pragma: no cover
        return f"SendRecord(enrollment={self.enrollment_id}, step={self.step_index})"


class DeferredSendLog(models.Model):
    """One row every time a due send is deferred for quiet hours, instead
    of dropped. Proves the "deferred, not dropped" property has a durable
    trace, not just a return value."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="deferrals")
    step_index = models.IntegerField()
    deferred_from_utc = models.DateTimeField()
    deferred_to_utc = models.DateTimeField()
    recipient_local_time = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover
        return f"Deferred(enrollment={self.enrollment_id}, step={self.step_index})"
