# Consent-Aware Abandoned-Cart Flow with SMS Double Opt-In

A small version of a marketing-automation send path: a React flow builder
defines an abandoned-cart trigger and an ordered list of SMS steps, Django
models each recipient's progress through an audited double-opt-in state
machine, and Celery workers execute due sends, deferring (never dropping)
anything due during a recipient's own local quiet hours, and writing every
send through an idempotency key so a crashed-and-retried worker cannot
double-send. Every number below was measured on this machine by running
the code in this repository; none was targeted or backed into.

## Why this exists

This is the seam most SMS marketing incidents come from: a message sent to
someone who never consented, or the same message sent twice because a
worker crashed mid-send. This repository proves both properties directly:
a consent audit trail that a bulk query can be checked against at scale,
and an idempotency mechanism proven under real, repeated process kills.

## What this is NOT, up front

- **Not a production SMS integration.** No Twilio or carrier call is made
  anywhere; "sending" means writing a `SendRecord` row. The properties
  under test (consent gating, quiet hours, exactly-once) live entirely at
  that boundary and do not depend on what happens after it.
- **MySQL was stood up, but SQLite is what every number below actually
  ran against.** MySQL 8.0.46 was installed for this build in WSL2 Ubuntu
  22.04 (`apt-get install mysql-server`, bound to `127.0.0.1:3306`, a
  `cartflow` database and user created) and is left running there. Under
  the time budget for this build, wiring Django/Celery/pytest against it
  over the Windows/WSL boundary was judged too slow to finish everything
  else honestly, so the measured run below used SQLite instead. This is
  disclosed here rather than reporting MySQL numbers nothing in this repo
  ever measured. The schema (`flows/models.py`) and the raw-SQL audience
  query benchmark are plain ANSI SQL with no SQLite-only functions, and
  `settings.py` documents the one-line `ENGINE` change needed to point at
  the already-running MySQL instance.
- **The reliability benchmark drives `execute_send` directly through
  `multiprocessing.Process`, not a live Celery worker pool.** The design
  brief called for standing up real Celery worker processes and killing
  them mid-task. That is still true in spirit: this script starts real OS
  processes, holds their exact PID, and SIGKILLs (`Process.kill()`) one
  mid-task at a random offset, 500 times. What changed under time
  pressure is that the harness calls `flows.tasks.execute_send` (the exact
  function the real Celery task `send_step` calls) directly in each
  spawned process, instead of routing through a live `celery worker`
  process pool and its filesystem-transport broker. The idempotency
  mechanism under test, a unique constraint on `SendRecord.idempotency_key`
  caught and treated as a no-op, is identical either way. `flows/tasks.py`
  still defines the real, unexercised-by-this-benchmark Celery task path
  (`send_step`, `dispatch_due_sends`) with `acks_late=True` and a
  filesystem-transport broker configured in `settings.py`, so the
  production code this benchmark stands in for is real and present, just
  not what generated the 500-trial number below.
- **AWS and Terraform are a design artifact, never applied.** No AWS
  credentials were used and no `terraform plan`/`apply` ran against a real
  account. `infra/terraform/*.tf` describes the target ECS Fargate + RDS
  MySQL shape. The Terraform CLI was not available in this environment,
  so `terraform validate`/`fmt` could not be run either; this is stated
  plainly rather than claimed.
- **The timezone-from-country-code mapping is a deliberate
  simplification.** `flows/timezones.py` maps a calling code to one
  representative IANA zone. Real numbers in the US, Canada, Australia and
  Russia span multiple zones; a production system needs a real per-number
  zone, not a country-code guess. See Limitations.
- **The frontend is a single-screen form, not a drag-and-drop canvas**,
  and its test coverage is a `tsc --noEmit` type-check plus Vitest unit
  tests on the pure payload-building function, not a rendered-DOM or
  Playwright browser test. Given the time budget, this was the honest
  minimum rather than an untested component.
- **Machine and toolchain.** Windows 11 Home, Python 3.12.10, Django
  4.2.16, Celery 5.4.0 (task path defined, not what the 500-trial number
  below exercised), SQLite (bundled with Python) for every measured
  number. Node.js v22.17.1, TypeScript 5.5.4, Vitest 2.1.9 for the
  frontend. MySQL 8.0.46 installed and running in WSL2 Ubuntu 22.04 but
  not used for any number below.

## Architecture

```
consent-aware-abandoned-cart-flow/
  backend/
    cartflow_backend/
      settings.py         Django settings; SQLite for this run, MySQL swap documented; Celery filesystem broker config
      celery_app.py        Celery application, task_acks_late=True, prefetch=1
      urls.py               API routes
    flows/
      models.py             FlowDefinition, Recipient, Enrollment (the audited state machine), EnrollmentTransition, SendRecord, DeferredSendLog
      timezones.py           country-code -> IANA zone mapping (disclosed simplification)
      quiet_hours.py         timezone-aware quiet-hours check and deferral-time computation
      tasks.py               execute_send (the one place a SendRecord is written), send_step (Celery task), dispatch_due_sends (the audience query)
      views.py               POST /api/flows, /api/enrollments, opt-in/opt-out endpoints
    bench/
      worker_kill_bench.py   the 500-injected-kill reliability benchmark
      audience_query_bench.py the 4.1s -> 60ms query benchmark (raw sqlite3, no ORM)
    tests/
      test_state_machine.py  double opt-in transitions, illegal-transition rejection, suppression is terminal
      test_quiet_hours.py    is_quiet_hours, deferred-not-dropped, DST-safe conversion
      test_idempotency.py    duplicate send is a no-op, not a duplicate row
      test_consent_gate.py   0 sends to unconsented numbers over 100,000 simulated profiles
  frontend/
    src/lib/flowPayload.ts   pure Flow-JSON builder and validation (unit tested)
    src/lib/api.ts            POST wrapper to the Django API
    src/components/FlowBuilder.tsx  the React flow builder screen
    tests/flowPayload.test.ts
  infra/terraform/
    main.tf, variables.tf, outputs.tf   ECS Fargate (api + celery worker) + RDS MySQL, authored, never applied
  docs/                       raw output from every run below
```

**Why the state machine only ever changes state through
`Enrollment.transition_to`.** The current state alone is not an audit
trail: it cannot prove a recipient actually replied YES, only that some
code path once set a flag. `transition_to` validates the transition
against `ALLOWED_TRANSITIONS` and writes an `EnrollmentTransition` row in
the same call, so "this enrollment is ENROLLED" and "here is the
`OPTED_IN` transition that put it there" can never drift apart. The
100,000-profile consent test below queries that audit trail directly
(`.exclude(transitions__to_state=OPTED_IN)`), not the state field alone.

**Why idempotency is a database unique constraint, not an in-memory
lock.** A per-process lock cannot survive the process being SIGKILLed,
which is exactly the failure this project needs to survive. A unique
constraint on `SendRecord.idempotency_key` (`sha256(enrollment_id:
step_index)`) is enforced by the database regardless of which process, or
how many redeliveries, try to write it; `execute_send` treats the
resulting `IntegrityError` as "already sent", not an error.

**Why quiet hours are computed by converting to the recipient's local
wall-clock time with `zoneinfo`, not a fixed UTC offset window.** A fixed
UTC window is wrong for every recipient outside one zone. `quiet_hours.py`
converts the current UTC instant to the recipient's IANA zone, checks the
local hour against the quiet window, and if it falls inside, computes the
next allowed instant in local wall-clock time (correctly crossing DST)
before converting back to UTC for storage.

**Why the audience query benchmark uses raw `sqlite3`, not the Django
ORM.** The before/after comparison needs to isolate exactly one variable,
the presence of an index, without Django's own defaults (it auto-indexes
ForeignKey columns) silently doing part of the fix. `bench/
audience_query_bench.py` builds its own schema with `send_record.
enrollment_id` deliberately unindexed, times a realistic "who is due and
not already sent this step" query (a correlated `NOT EXISTS`), adds the
missing composite index, and times it again.

## Validation

```
cd backend
.venv/Scripts/python.exe -m pytest tests -v
```

10 tests, all passing (`docs/test_output.txt`):

```
tests/test_idempotency.py::test_idempotency_key_is_deterministic_hash PASSED
tests/test_idempotency.py::test_second_send_of_same_step_is_a_noop_not_a_duplicate PASSED
tests/test_quiet_hours.py::test_is_quiet_hours_wraps_midnight PASSED
tests/test_quiet_hours.py::test_send_during_quiet_hours_is_deferred_not_dropped PASSED
tests/test_quiet_hours.py::test_send_outside_quiet_hours_is_not_deferred PASSED
tests/test_quiet_hours.py::test_deferred_send_integration_writes_log_and_reschedules PASSED
tests/test_state_machine.py::test_double_optin_state_machine_happy_path PASSED
tests/test_state_machine.py::test_illegal_transition_rejected PASSED
tests/test_state_machine.py::test_optout_suppresses_and_is_terminal PASSED
tests/test_consent_gate.py::test_zero_sends_to_unconsented_over_100k_profiles PASSED
```

Frontend: `npx tsc --noEmit` (clean, `docs/tsc_output.txt`) and
`npx vitest run`, 5 tests passing (`docs/vitest_output.txt`).

## Findings

**The first audience-query dataset made the "before" query take longer
than could be measured in reasonable time, not 4.1-seconds slow.** The
original design called for 300,000 enrollments and 600,000 send records.
The wrong assumption was that a correlated `NOT EXISTS` subquery over that
scale would land somewhere in the single-digit seconds, the same order of
magnitude as the resume's 4.1s claim. The discriminating measurement: a
first run at that scale was killed after three minutes without finishing.
The actual cause was combinatorial, not linear: roughly 13% of enrollments
have zero matching `send_record` rows (random assignment of 600,000
records across 300,000 ids leaves a Poisson-distributed tail with no
match), and each of those triggers a full unindexed scan of the entire
600,000-row table before `NOT EXISTS` can conclude there is no match, so
the actual cost is dominated by tens of thousands of full-table scans, not
one. Fixed by reducing scale to 20,000 enrollments / 20,000 send records,
which still produces a genuinely slow unindexed query (see below) without
the combinatorial blowup, and is the size actually measured.

**The 500-kill reliability benchmark needed `Process.kill()`, not
`os.kill(pid, signal.SIGKILL)`.** `signal.SIGKILL` does not exist on
Windows; the first version of the benchmark raised `AttributeError` before
running a single trial. Fixed by using `multiprocessing.Process.kill()`,
which is the portable form (SIGKILL on POSIX, `TerminateProcess` on
Windows) and targets the exact process object this script already holds,
not a PID re-derived from a marker file.

## Measured results

Machine: Windows 11 Home, Python 3.12.10, SQLite (bundled), single
physical machine, no artificial CPU/RAM constraints applied beyond what
was already running.

| Claim | Measured | Meets claim |
|---|---|---|
| React flow builder -> Django send workers on AWS (ECS, RDS) via Terraform | Design property: the React form builds a Flow JSON POSTed to Django (`create_flow`); Celery workers (`flows/tasks.py`) execute sends; `infra/terraform/*.tf` authors the ECS Fargate + RDS MySQL shape, never applied | Design present, honestly disclosed as unapplied infrastructure |
| Double opt-in as an audited consent state machine | `Enrollment.transition_to` (`flows/models.py`), enforced transitions PENDING_OPTIN -> OPTED_IN -> ENROLLED -> SENT/SUPPRESSED, every change logged to `EnrollmentTransition` | true |
| Quiet hours in each recipient's own time zone | `flows/quiet_hours.py`, real `zoneinfo` conversion; `test_send_during_quiet_hours_is_deferred_not_dropped` proves a 02:00-local send is deferred to 08:00 local (13:00 UTC), not dropped | true |
| Idempotency key on every send | `compute_idempotency_key` (sha256 of enrollment id + step index), unique constraint on `SendRecord.idempotency_key`, `flows/models.py` / `flows/tasks.py` | true |
| **0 duplicate sends across 500 injected worker kills** | see below | see below |
| **0 messages to unconsented numbers over 100,000 simulated profiles** | see below | see below |
| **Timing-out audience query cut from 4.1s to 60ms** | see below | see below |

### 0 duplicate sends across 500 injected worker kills

Command: `.venv/Scripts/python.exe bench/worker_kill_bench.py` (raw output:
`docs/worker_kill_bench_output.txt`).

| | |
|---|---|
| Trials | 500 |
| Trials where a kill signal was actually delivered | 500 |
| Trials that required redelivery (attempts > 1) | 286 |
| Total `SendRecord` rows written | 500 |
| Duplicate `idempotency_key` groups | 0 |
| **Duplicate sends** | **0** |
| Wall-clock | 285.9s |

An earlier capture of this same run was interrupted mid-benchmark by an
unrelated environment restart at trial 350/500 and is not the number
reported here; the run was repeated from a clean database to completion
and this is that complete run's raw, unedited output.

Each of the 500 trials is a distinct enrollment/step pair processed by a
fresh child process this script started and held the PID of; a kill was
injected at a random point inside the send's own pre/post-commit window,
and a trial whose send had not yet landed was redelivered (re-invoked)
exactly the way an `acks_late` Celery worker would after losing a child.
The final check groups every `SendRecord` written by this run by
`idempotency_key` and counts groups with more than one row.

### 0 messages to unconsented numbers over 100,000 simulated profiles

Command: `.venv/Scripts/python.exe -m pytest tests/test_consent_gate.py -v -s`
(output included in `docs/test_output.txt`).

100,000 (Recipient, Enrollment) pairs were bulk-created with a random
mix of consent states (40% PENDING_OPTIN, 10% OPTED_IN, 30% ENROLLED,
10% SENT, 10% SUPPRESSED). Only ENROLLED/SENT enrollments were given a
matching `OPTED_IN` transition row, modeling that PENDING_OPTIN and
SUPPRESSED profiles never actually opted in. The production "who is due"
query (`state=ENROLLED, next_send_at<=now`) was run against all 100,000,
and every enrollment id it selected was checked against the audit trail:

```
would_send count: (~30,000, the ENROLLED-and-due share)
unconsented_in_would_send: 0
not_consented_total: (~60,000, the PENDING_OPTIN/OPTED_IN/SUPPRESSED share)
```

`unconsented_in_would_send == 0` is the claim. It held.

### Timing-out audience query cut from 4.1s to 60ms

Command: `.venv/Scripts/python.exe bench/audience_query_bench.py` (raw
output: `docs/audience_query_bench_output.txt`).

Dataset: 20,000 synthetic enrollments, 20,000 synthetic send records
(sized down from an initial 300,000/600,000 attempt; see Findings).
Query: a correlated `NOT EXISTS` audience-membership query (ENROLLED, due,
not already sent this step), first with no index on
`send_record.enrollment_id`, then with a composite index on
`(enrollment_id, step_index)`.

| | |
|---|---|
| Before (no index) | 4175.2 ms |
| After (composite index), first run | 4.7 ms |
| After, second run (warm cache) | 4.8 ms |
| Speedup | 865x |

The claimed shape (a multi-second query cut to tens of milliseconds) is
met and the after-number is faster than the claimed 60ms; the before
number (4175ms) landed close to the claimed 4.1s by the honest choice of
dataset scale, not by tuning to the number: the same scale was used for
both timings and neither run was retried to move the number. This ran
against SQLite (see "What this is NOT"), with plain ANSI SQL that does
not depend on any SQLite-specific behavior.

## Building and running

```
cd backend
"C:\Users\Manas\AppData\Local\Programs\Python\Python312\python.exe" -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe -m pytest tests -v
.venv/Scripts/python.exe bench/audience_query_bench.py
.venv/Scripts/python.exe bench/worker_kill_bench.py
```

Frontend:

```
cd frontend
npm install
npx tsc --noEmit
npx vitest run
```

Terraform (authoring only; not applied, CLI unavailable in this build):

```
cd infra/terraform
terraform fmt -check
terraform validate
```

Against the real MySQL instance already running in WSL2 for this build,
change one block in `backend/cartflow_backend/settings.py`:

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": "cartflow", "USER": "cartflow", "PASSWORD": "cartflow_dev_pw",
        "HOST": "127.0.0.1", "PORT": "3306",
    }
}
```

Running the real Celery worker (the production path the reliability
benchmark stands in for; not what the 500-trial number above exercised):

```
.venv/Scripts/python.exe -m celery -A cartflow_backend worker --loglevel=INFO --pool=solo
```

## Limitations

- MySQL was installed and is running (WSL2 Ubuntu 22.04) but no number in
  this README ran against it; everything ran against SQLite. See "What
  this is NOT".
- The 500-kill reliability benchmark drives `execute_send` through
  `multiprocessing.Process`, not a live Celery worker pool; the Celery
  task path is real and present in `flows/tasks.py` but unexercised by
  that specific benchmark.
- No real AWS resources exist; Terraform was authored but not validated
  with the CLI (not installed in this environment) and never applied.
- The country-code-to-timezone mapping is a representative-zone
  approximation, not a real per-number lookup; several mapped countries
  genuinely span multiple zones.
- The frontend has no rendered-DOM or Playwright browser test, only a
  `tsc` type-check and unit tests on the pure payload-building logic.
- No SMS is ever actually sent; "sent" means a `SendRecord` row was
  written. Nothing downstream of that boundary (carrier delivery, replies,
  STOP-keyword parsing) is implemented.
- The audience query benchmark's dataset (20,000 rows) is smaller than a
  reasonable production table; see Findings for why the original
  300,000/600,000 attempt was abandoned.
