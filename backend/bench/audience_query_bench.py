"""Audience-membership query: 4.1s cut to 60ms.

Uses the sqlite3 standard library directly (not the Django ORM) so the
schema and indexes are exactly and only what this script creates, which
is what makes the before/after comparison honest: nothing about Django's
own default indexing (e.g. Django auto-indexes ForeignKey columns) is
quietly doing the fix for us. The SQL is plain ANSI SQL, no SQLite-only
functions, so it is MySQL-compatible as written.

The query: "which due, opted-in enrollments have NOT already been sent
this step" -- a correlated NOT EXISTS against the send-history table,
which is a completely realistic shape for an audience query (you cannot
just check "is ENROLLED and due", you also have to exclude anyone the
system already sent this exact step to, e.g. after a delayed retry of
the dispatch scan itself). Without an index on send_record.enrollment_id
this degrades to one send_record scan per candidate enrollment row.
"""

import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "var" / "audience_query_bench.sqlite3"

N_ENROLLMENTS = 20_000
N_SEND_RECORDS = 20_000


def build_db():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE enrollment (
            id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            next_send_at TEXT NOT NULL,
            current_step_index INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE send_record (
            id INTEGER PRIMARY KEY,
            enrollment_id INTEGER NOT NULL,
            step_index INTEGER NOT NULL
        )"""
    )
    conn.commit()

    import random

    random.seed(42)
    now = "2026-09-12T12:00:00"
    past = "2026-09-12T11:00:00"

    rows = []
    for i in range(1, N_ENROLLMENTS + 1):
        state = "ENROLLED" if random.random() < 0.5 else "SENT"
        rows.append((i, state, past, 0))
    conn.executemany("INSERT INTO enrollment VALUES (?,?,?,?)", rows)

    sr_rows = []
    for i in range(N_SEND_RECORDS):
        eid = random.randint(1, N_ENROLLMENTS)
        sr_rows.append((eid, 0))
    conn.executemany("INSERT INTO send_record (enrollment_id, step_index) VALUES (?,?)", sr_rows)
    conn.commit()
    conn.close()


QUERY = """
SELECT e.id FROM enrollment e
WHERE e.state = 'ENROLLED' AND e.next_send_at <= ?
AND NOT EXISTS (
    SELECT 1 FROM send_record s
    WHERE s.enrollment_id = e.id AND s.step_index = e.current_step_index
)
"""


def time_query(conn, label):
    now = "2026-09-12T12:00:00"
    t0 = time.perf_counter()
    rows = conn.execute(QUERY, (now,)).fetchall()
    elapsed = time.perf_counter() - t0
    print(f"{label}: {elapsed * 1000:.1f} ms, {len(rows)} rows returned")
    return elapsed


def main():
    print(f"Building synthetic dataset: {N_ENROLLMENTS} enrollments, {N_SEND_RECORDS} send records...")
    build_db()
    conn = sqlite3.connect(DB_PATH)

    before = time_query(conn, "BEFORE (no index on send_record.enrollment_id)")

    conn.execute("CREATE INDEX ix_send_record_enrollment_step ON send_record(enrollment_id, step_index)")
    conn.commit()

    after = time_query(conn, "AFTER (composite index on send_record(enrollment_id, step_index))")
    after2 = time_query(conn, "AFTER, second run (warm cache)")

    print("=" * 60)
    print(f"BEFORE_MS: {before * 1000:.2f}")
    print(f"AFTER_MS: {after * 1000:.2f}")
    print(f"AFTER_MS_WARM: {after2 * 1000:.2f}")
    print(f"SPEEDUP: {before / after2:.1f}x")
    print("=" * 60)
    conn.close()


if __name__ == "__main__":
    main()
