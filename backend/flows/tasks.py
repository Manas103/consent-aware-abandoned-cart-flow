"""Celery tasks: dispatching due sends and executing one send.

``execute_send`` is the single code path that actually writes a
SendRecord. It is deliberately not a Celery task itself, it is a plain
function called by both the production task (``send_step``) and the
worker-kill benchmark task (``bench.tasks.send_step_bench``), so the
benchmark exercises the exact same idempotency and quiet-hours logic the
production path uses, with only test-only hooks (a start marker, and two
optional sleeps that create a wider window for an injected kill to land
in) layered on top.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Optional

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

from .models import DeferredSendLog, Enrollment, SendRecord, compute_idempotency_key
from .quiet_hours import next_allowed_utc


def write_marker(marker_path: Optional[Path], stage: str) -> None:
    if marker_path is None:
        return
    marker_path.write_text(json.dumps({"pid": os.getpid(), "stage": stage, "ts": time.time()}))


def execute_send(
    enrollment_id: int,
    step_index: int,
    *,
    trial_id: str = "",
    marker_path: Optional[Path] = None,
    pre_commit_delay_s: float = 0.0,
    post_commit_delay_s: float = 0.0,
) -> str:
    """Returns one of "sent", "already-sent", "deferred", "skipped"."""
    write_marker(marker_path, "started")

    enrollment = Enrollment.objects.select_related("recipient", "flow").get(id=enrollment_id)
    if enrollment.state != Enrollment.ENROLLED:
        write_marker(marker_path, "skipped")
        return "skipped"

    now_utc = dj_timezone.now()
    next_send_utc, deferred, local_now = next_allowed_utc(
        now_utc,
        enrollment.recipient.timezone,
        settings.QUIET_HOURS_START_LOCAL,
        settings.QUIET_HOURS_END_LOCAL,
    )
    if deferred:
        enrollment.next_send_at = next_send_utc
        enrollment.save(update_fields=["next_send_at", "updated_at"])
        DeferredSendLog.objects.create(
            enrollment=enrollment,
            step_index=step_index,
            deferred_from_utc=now_utc,
            deferred_to_utc=next_send_utc,
            recipient_local_time=local_now.isoformat(),
        )
        write_marker(marker_path, "deferred")
        return "deferred"

    idempotency_key = compute_idempotency_key(enrollment_id, step_index)

    if pre_commit_delay_s:
        time.sleep(pre_commit_delay_s)

    created = True
    try:
        with transaction.atomic():
            SendRecord.objects.create(
                enrollment=enrollment,
                step_index=step_index,
                idempotency_key=idempotency_key,
                worker_pid=os.getpid(),
                trial_id=trial_id,
            )
    except IntegrityError:
        created = False

    write_marker(marker_path, "committed")

    if post_commit_delay_s:
        time.sleep(post_commit_delay_s)

    if created:
        # Advance the state machine: SENT, then immediately back to
        # ENROLLED for the next step, or stay SENT if this was the last one.
        enrollment.refresh_from_db()
        if enrollment.state == Enrollment.ENROLLED:
            enrollment.transition_to(Enrollment.SENT, reason=f"step {step_index} sent")
            if step_index < enrollment.flow.max_step_index():
                enrollment.current_step_index = step_index + 1
                enrollment.save(update_fields=["current_step_index"])
                enrollment.transition_to(Enrollment.ENROLLED, reason=f"advanced to step {step_index + 1}")
        return "sent"
    return "already-sent"


@shared_task(bind=True, acks_late=True)
def send_step(self, enrollment_id: int, step_index: int):
    return execute_send(enrollment_id, step_index)


@shared_task
def dispatch_due_sends(limit: int = 1000):
    """The audience-membership query: which ENROLLED enrollments are due
    right now. See docs/audience_query_before.txt / _after.txt for the
    before/after timing of exactly this query shape."""
    now = dj_timezone.now()
    due = list(
        Enrollment.objects.filter(state=Enrollment.ENROLLED, next_send_at__lte=now)
        .order_by("next_send_at")[:limit]
        .values_list("id", "current_step_index")
    )
    for enrollment_id, step_index in due:
        send_step.delay(enrollment_id, step_index)
    return len(due)
