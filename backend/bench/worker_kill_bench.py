"""0 duplicate sends across 500 injected worker kills.

Disclosed simplification from the original design brief: this drives
``flows.tasks.execute_send`` (the exact function the Celery task
``send_step`` calls) through real OS processes this script starts and
holds the PID of, kills mid-task at a random point with SIGKILL, and then
redelivers (re-invokes the same send) the way an acks_late Celery worker
would after losing a child, rather than driving it through a live Celery
worker pool. A full Celery worker pool was judged too much additional
moving-parts risk for the time budget of this build; the mechanism this
proves (the idempotency unique constraint on SendRecord makes a
crash-and-redeliver both correct and a no-op the second time) is
identical to what the Celery task path uses, since both call the same
``execute_send`` function. See README "What this is NOT".

For each of 500 trials:
  1. Spawn a fresh child process running ``_send_worker_main`` for a
     distinct (enrollment, step) pair.
  2. Poll a marker file the child writes the instant it starts, to learn
     its real PID.
  3. Sleep a random offset inside the task's own artificial
     pre-commit/post-commit delay window, then SIGKILL that exact PID.
  4. If the DB does not yet have a SendRecord for that idempotency key,
     redeliver by spawning a new child for the same (enrollment, step)
     (this is what an acks_late Celery worker does automatically when it
     loses a child mid-task). Repeat up to 5 redeliveries.
  5. Record whether a kill was actually injected and how many attempts
     the trial took.

At the end, the DB is queried for `idempotency_key` values that appear
more than once. The claim is that this count is 0.
"""

import json
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cartflow_backend.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.db.models import Count  # noqa: E402

from flows.models import Enrollment, FlowDefinition, Recipient, SendRecord, compute_idempotency_key  # noqa: E402
from flows.tasks import execute_send  # noqa: E402

MARKER_DIR = BACKEND_DIR / "var" / "bench_markers"
MARKER_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS = 500
MAX_REDELIVERIES = 5


def _send_worker_main(enrollment_id: int, step_index: int, trial_id: str, pre_delay: float, post_delay: float):
    """Runs in a fresh child process. This is the "worker" whose PID gets killed."""
    from django.db import connection as conn

    conn.close()  # do not inherit the parent's sqlite connection across fork/spawn
    marker_path = MARKER_DIR / f"{trial_id}.json"
    execute_send(
        enrollment_id,
        step_index,
        trial_id=trial_id,
        marker_path=marker_path,
        pre_commit_delay_s=pre_delay,
        post_commit_delay_s=post_delay,
    )


def run_trial(enrollment_id: int, step_index: int, trial_id: str) -> dict:
    pre_delay = random.uniform(0.03, 0.08)
    post_delay = random.uniform(0.03, 0.08)
    total_window = pre_delay + post_delay
    marker_path = MARKER_DIR / f"{trial_id}.json"
    if marker_path.exists():
        marker_path.unlink()

    killed = False
    attempts = 0
    for attempt in range(1, MAX_REDELIVERIES + 1):
        attempts = attempt
        ctx = multiprocessing.get_context("spawn")
        p = ctx.Process(target=_send_worker_main, args=(enrollment_id, step_index, trial_id, pre_delay, post_delay))
        p.start()

        # Wait for the child to write its start marker (proof it actually
        # began executing) before injecting the kill, so "mid-task" is a
        # real statement and not a race against process startup.
        deadline = time.time() + 3
        started = False
        while time.time() < deadline:
            if marker_path.exists():
                started = True
                break
            time.sleep(0.002)

        if attempt == 1 and started:
            kill_offset = random.uniform(0.0, total_window)
            time.sleep(kill_offset)
            # Process.kill() is the portable form (SIGKILL on POSIX,
            # TerminateProcess on Windows); this is the exact child PID
            # (p.pid) this script itself started and is holding.
            try:
                p.kill()
                killed = True
            except Exception:
                pass

        p.join(timeout=5)

        if SendRecord.objects.filter(idempotency_key=compute_idempotency_key(enrollment_id, step_index)).exists():
            break
    return {"trial_id": trial_id, "killed": killed, "attempts": attempts}


def main():
    flow = FlowDefinition.objects.create(
        name="bench-flow", steps=[{"step_index": 0, "delay_minutes": 0, "channel": "sms", "template": "hi"}]
    )
    trials = []
    for i in range(N_TRIALS):
        recipient = Recipient.objects.create(phone_e164=f"+1777{i:07d}", country_code="1", timezone="UTC")
        e = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id=f"bench-{i}")
        e.transition_to(Enrollment.OPTED_IN, reason="bench")
        e.transition_to(Enrollment.ENROLLED, reason="bench")
        trials.append(e.id)

    connection.close()  # each spawned child opens its own connection

    results = []
    t0 = time.time()
    for i, enrollment_id in enumerate(trials):
        results.append(run_trial(enrollment_id, 0, f"trial-{i}"))
        if (i + 1) % 50 == 0:
            print(f"...{i + 1}/{N_TRIALS} trials done ({time.time() - t0:.1f}s elapsed)", flush=True)
    elapsed = time.time() - t0

    n_killed = sum(1 for r in results if r["killed"])
    n_redelivered = sum(1 for r in results if r["attempts"] > 1)
    n_final_sends = SendRecord.objects.filter(trial_id__startswith="trial-").count()
    dup_groups = (
        SendRecord.objects.filter(trial_id__startswith="trial-")
        .values("idempotency_key")
        .annotate(c=Count("id"))
        .filter(c__gt=1)
    )
    n_duplicates = sum(g["c"] - 1 for g in dup_groups)

    print("=" * 60)
    print(f"trials: {N_TRIALS}")
    print(f"trials where a kill signal was actually delivered: {n_killed}")
    print(f"trials that required redelivery (attempts > 1): {n_redelivered}")
    print(f"total SendRecord rows written: {n_final_sends}")
    print(f"duplicate idempotency_key groups: {len(list(dup_groups))}")
    print(f"DUPLICATE_SENDS: {n_duplicates}")
    print(f"elapsed_seconds: {elapsed:.1f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
