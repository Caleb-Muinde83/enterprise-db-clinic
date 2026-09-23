#!/usr/bin/env python3
"""
contract.py — Phase 2: adds the FK from payments.payment_method_id to
payment_methods, makes the column NOT NULL, and drops the old `method` text
column. This is the phase most likely to actually contend with live traffic,
so run concurrent_writer.py against it and check its log/spikes afterward --
don't just trust that "the script finished" means "nothing blocked."

Per-engine FK technique (see zero-downtime/README.md for the full rationale):
  - postgres:   ADD CONSTRAINT ... NOT VALID  (fast)  ->  VALIDATE CONSTRAINT (separate, slower)
  - sqlserver:  WITH NOCHECK ADD CONSTRAINT   (fast)  ->  WITH CHECK CHECK CONSTRAINT (separate, slower)
  - mysql:      ALGORITHM=INPLACE ADD FOREIGN KEY -- single statement, no separate
                unvalidated step exists on this engine. Documented asymmetry, not a bug.

Timestamps are printed around each individual step so they can be cross-referenced
against concurrent_writer.py's CSV log afterward.

Usage:
    python contract.py --engine postgres
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone


def run_postgres(sql):
    return subprocess.run(
        ["docker", "exec", "-i", "clinic_pg_old", "psql", "-U", "clinic", "-d", "clinic", "-c", sql],
        capture_output=True, text=True,
    )


def run_mysql(sql):
    return subprocess.run(
        ["docker", "exec", "-i", "clinic_mysql_old", "mysql", "-u", "clinic", "-pclinic", "clinic", "-e", sql],
        capture_output=True, text=True,
    )


def run_sqlserver(sql):
    return subprocess.run(
        ["docker", "exec", "-i", "clinic_mssql_old", "/opt/mssql-tools/bin/sqlcmd",
         "-S", "localhost", "-U", "sa", "-P", "Clinic!2017", "-d", "clinic", "-Q", sql],
        capture_output=True, text=True,
    )


RUNNERS = {"postgres": run_postgres, "mysql": run_mysql, "sqlserver": run_sqlserver}


def check(result, step):
    if result.returncode != 0:
        print(f"FAILED at: {step}\n{result.stderr}\n{result.stdout}", file=sys.stderr)
        sys.exit(1)
    if result.stdout.strip():
        print(result.stdout.strip())


def count(run, sql):
    result = run(sql)
    check(result, f"count query: {sql}")
    digits = [int(n) for n in result.stdout.split() if n.strip().isdigit()]
    return digits[-1] if digits else 0


def timed_step(label, run, sql):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] {label} -- starting")
    t0 = time.time()
    result = run(sql)
    elapsed = time.time() - t0
    check(result, label)
    print(f"[{datetime.now(timezone.utc).isoformat()}] {label} -- done in {elapsed:.2f}s")


def fk_exists(engine, run):
    if engine == "postgres":
        n = count(run, """
            SELECT COUNT(*) FROM information_schema.table_constraints
            WHERE table_name = 'payments' AND constraint_type = 'FOREIGN KEY'
              AND constraint_name = 'fk_payments_payment_method';
        """)
    elif engine == "mysql":
        n = count(run, """
            SELECT COUNT(*) FROM information_schema.table_constraints
            WHERE table_schema = 'clinic' AND table_name = 'payments'
              AND constraint_type = 'FOREIGN KEY' AND constraint_name = 'fk_payments_payment_method';
        """)
    else:
        n = count(run, "SELECT COUNT(*) FROM sys.foreign_keys WHERE name = 'fk_payments_payment_method';")
    return n > 0


def sync_trigger_exists(engine, run):
    if engine == "postgres":
        n = count(run, "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'trg_payments_sync_method_id';")
    elif engine == "mysql":
        n = count(run, "SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema = 'clinic' AND trigger_name = 'trg_payments_sync_method_id_ins';")
    else:
        n = count(run, "SELECT COUNT(*) FROM sys.triggers WHERE name = 'trg_payments_sync_method_id';")
    return n > 0


def drop_sync_trigger(engine, run):
    """Must run before drop_method_column -- the trigger's body references
    NEW.method (or the inserted pseudo-table's method column on SQL Server),
    so dropping the column first would either fail the DROP or leave a
    broken trigger silently referencing a column that no longer exists."""
    if not sync_trigger_exists(engine, run):
        print("Sync trigger already gone, skipping drop.")
        return
    print("Dropping sync trigger (payment_method_id is now NOT NULL and self-sufficient)...")
    if engine == "postgres":
        steps = [
            "DROP TRIGGER IF EXISTS trg_payments_sync_method_id ON payments;",
            "DROP FUNCTION IF EXISTS payments_sync_method_id();",
        ]
    elif engine == "mysql":
        steps = [
            "DROP TRIGGER IF EXISTS trg_payments_sync_method_id_ins;",
            "DROP TRIGGER IF EXISTS trg_payments_sync_method_id_upd;",
        ]
    else:
        steps = ["DROP TRIGGER IF EXISTS trg_payments_sync_method_id;"]
    for s in steps:
        check(run(s), "drop_sync_trigger")


def catch_up_backfill(engine, run):
    """Defensive pass right before the FK/NOT NULL steps. The expand-phase
    trigger should mean there's nothing left to catch, but this is cheap
    insurance against any row that slipped through before the trigger
    existed (e.g. a re-run where expand.py's steps happened out of order),
    and makes the pre-FK NULL count in add_and_validate_fk trustworthy."""
    remaining = count(run, "SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
    if remaining == 0:
        return
    print(f"Catch-up: {remaining} rows still NULL, re-running a single backfill pass...")
    if engine == "postgres":
        sql = """
            UPDATE payments p SET payment_method_id = pm.payment_method_id
            FROM payment_methods pm
            WHERE p.method = pm.method_name AND p.payment_method_id IS NULL;
        """
    elif engine == "mysql":
        sql = """
            UPDATE payments p
            JOIN payment_methods pm ON p.method = pm.method_name
            SET p.payment_method_id = pm.payment_method_id
            WHERE p.payment_method_id IS NULL;
        """
    else:
        sql = """
            UPDATE p SET p.payment_method_id = pm.payment_method_id
            FROM payments p
            JOIN payment_methods pm ON p.method = pm.method_name
            WHERE p.payment_method_id IS NULL;
        """
    check(run(sql), "catch_up_backfill")
    still_remaining = count(run, "SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
    if still_remaining > 0:
        print(f"NOTE: {still_remaining} rows have a method value with no match in "
              f"payment_methods at all (not a timing issue) -- inspect with:\n"
              f"  SELECT method, COUNT(*) FROM payments WHERE payment_method_id IS NULL GROUP BY method;")


def method_column_exists(engine, run):
    if engine == "postgres":
        n = count(run, "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'payments' AND column_name = 'method';")
    elif engine == "mysql":
        n = count(run, "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'clinic' AND table_name = 'payments' AND column_name = 'method';")
    else:
        n = count(run, "SELECT COUNT(*) FROM sys.columns WHERE object_id = OBJECT_ID('payments') AND name = 'method';")
    return n > 0


def add_and_validate_fk(engine, run):
    if fk_exists(engine, run):
        print("FK already exists, skipping add+validate.")
        return

    catch_up_backfill(engine, run)
    unmatched = count(run, "SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
    if unmatched > 0:
        print(f"ABORT: {unmatched} rows have a method value with no match in "
              f"payment_methods at all -- this is genuine bad data, not a timing gap "
              f"(catch_up_backfill just ran). Decide how to handle these (fix the value, "
              f"or archive+exclude like schema-drift's customer_notes) before forcing "
              f"NOT NULL / adding the FK.", file=sys.stderr)
        sys.exit(1)

    if engine == "postgres":
        timed_step(
            "ADD CONSTRAINT ... NOT VALID (fast, unvalidated)", run,
            """ALTER TABLE payments
               ADD CONSTRAINT fk_payments_payment_method
               FOREIGN KEY (payment_method_id) REFERENCES payment_methods(payment_method_id)
               NOT VALID;""",
        )
        timed_step(
            "VALIDATE CONSTRAINT (slower, documented non-blocking)", run,
            "ALTER TABLE payments VALIDATE CONSTRAINT fk_payments_payment_method;",
        )
    elif engine == "sqlserver":
        timed_step(
            "WITH NOCHECK ADD CONSTRAINT (fast, untrusted)", run,
            """ALTER TABLE payments WITH NOCHECK
               ADD CONSTRAINT fk_payments_payment_method
               FOREIGN KEY (payment_method_id) REFERENCES payment_methods(payment_method_id);""",
        )
        timed_step(
            "WITH CHECK CHECK CONSTRAINT (slower -- blocking behavior UNVERIFIED, this run is the test)", run,
            "ALTER TABLE payments WITH CHECK CHECK CONSTRAINT fk_payments_payment_method;",
        )
    else:  # mysql -- corrected: ALGORITHM=INPLACE for ADD FOREIGN KEY refuses to run
        # with foreign_key_checks ON (validating existing data isn't supported as
        # part of an in-place, non-rebuilding DDL on this engine -- confirmed via
        # ERROR 1846 on a real run, not assumed from docs). The actual fast/
        # non-blocking path is to skip validation entirely with
        # foreign_key_checks=OFF, which is a MORE extreme version of Postgres's
        # NOT VALID / SQL Server's WITH NOCHECK -- except MySQL has no built-in
        # VALIDATE CONSTRAINT equivalent afterward. If you want that same
        # assurance here, you have to write and run the check yourself, which
        # verify_no_orphans_mysql below does. SET and ALTER stay in the same -e
        # call deliberately: each run() call is a fresh mysql connection, and
        # foreign_key_checks is session-scoped, so splitting them across two
        # separate calls would silently lose the OFF setting before the ALTER ran.
        timed_step(
            "SET foreign_key_checks=0 + ADD FOREIGN KEY ALGORITHM=INPLACE (skips validation -- see comment above)", run,
            """SET foreign_key_checks=0;
               ALTER TABLE payments
               ADD CONSTRAINT fk_payments_payment_method
               FOREIGN KEY (payment_method_id) REFERENCES payment_methods(payment_method_id),
               ALGORITHM=INPLACE, LOCK=NONE;
               SET foreign_key_checks=1;""",
        )
        verify_no_orphans_mysql(run)


def verify_no_orphans_mysql(run):
    """MySQL has no native VALIDATE CONSTRAINT / CHECK CONSTRAINT step -- once
    the FK is added with foreign_key_checks=OFF, existing-data validation
    never happens unless something does it explicitly. This is that something.
    Not a formality: without this, an ADD CONSTRAINT with checks off would
    silently succeed even over orphaned/inconsistent data, unlike Postgres or
    SQL Server where the later VALIDATE/CHECK step would catch it."""
    print("MySQL has no built-in constraint validation step -- verifying manually...")
    orphans = count(run, """
        SELECT COUNT(*) FROM payments p
        LEFT JOIN payment_methods pm ON p.payment_method_id = pm.payment_method_id
        WHERE pm.payment_method_id IS NULL;
    """)
    if orphans > 0:
        print(f"WARNING: {orphans} payments rows reference a payment_method_id with no "
              f"matching row in payment_methods. The FK constraint now exists but was "
              f"added without validation (foreign_key_checks was OFF) -- it will not catch "
              f"this on existing rows, only prevent NEW violations going forward. Fix these "
              f"rows before treating this migration as complete.", file=sys.stderr)
    else:
        print(f"Verified: 0 orphaned payment_method_id values. FK is trustworthy even "
              f"though MySQL added it without built-in validation.")


def enforce_not_null(engine, run):
    if engine == "postgres":
        sql = "ALTER TABLE payments ALTER COLUMN payment_method_id SET NOT NULL;"
    elif engine == "mysql":
        sql = "ALTER TABLE payments MODIFY payment_method_id SMALLINT NOT NULL;"
    else:
        sql = "ALTER TABLE payments ALTER COLUMN payment_method_id SMALLINT NOT NULL;"
    timed_step("Enforce NOT NULL on payment_method_id", run, sql)


def relax_old_column(engine, run):
    """MUST run before the actual DROP. Discovered via a real failed run: a
    writer that has fully cut over (omits `method` from its INSERT entirely)
    was still hitting 'doesn't have a default value' errors for the whole
    ~265s MySQL drop took, because `method` was NOT NULL with no default
    from the original schema -- simply existing as a required column was
    enough to break a cutover writer, independent of whether the drop itself
    was in progress. A hard switch from 'everyone writes method' to
    'everyone writes payment_method_id' doesn't work; there has to be a
    transition window where the old column stops being required so both
    writer styles can succeed at once. This is the contract-side mirror of
    the expand-phase trigger fix: that one closed a gap for new writes on
    the new column, this one closes a gap for new writes on the old one.

    IMPORTANT, found on the SECOND real run of this fix: on MySQL 5.7, this
    ALTER is itself NOT a fast metadata change -- bracketed via writer error
    timestamps at ~229s, the same order of cost as SET NOT NULL (measured
    243s). InnoDB 5.7 apparently rewrites the table either direction for a
    nullability change; true instant nullability changes are 8.0.12+ only.
    So relaxing before dropping is still the right pattern (confirmed: once
    it completed, the following 246.9s drop ran with 0 errors against a
    cut-over writer) -- but on this engine/version it does NOT make the
    transition gap-free, only shifts where the multi-minute error window
    sits. A genuinely gap-free cutover on MySQL 5.7 would need dual-writing
    both columns during the transition instead of relying on this being
    cheap. This step is timed for exactly that reason -- don't lose that
    signal by reverting to an untimed print+check."""
    print("Relaxing old `method` column (NULL, no default requirement) before dropping it...")
    if engine == "postgres":
        sql = "ALTER TABLE payments ALTER COLUMN method DROP NOT NULL;"
    elif engine == "mysql":
        sql = "ALTER TABLE payments MODIFY method VARCHAR(20) NULL;"
    else:
        sql = "ALTER TABLE payments ALTER COLUMN method VARCHAR(20) NULL;"
    timed_step("Relax old method column (NOT NULL -> nullable)", run, sql)


def drop_method_column(engine, run):
    if not method_column_exists(engine, run):
        print("method column already gone, skipping.")
        return
    drop_sync_trigger(engine, run)
    relax_old_column(engine, run)
    timed_step("Drop old method column", run, "ALTER TABLE payments DROP COLUMN method;")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    parser.add_argument("--skip-drop", action="store_true",
                         help="add/validate FK + NOT NULL but leave the old method column in place")
    args = parser.parse_args()
    run = RUNNERS[args.engine]

    print(f"=== Contract: {args.engine} ===")
    print("If concurrent_writer.py is running against this engine right now, good -- "
          "cross-reference its log timestamps against the step timestamps below afterward.")
    add_and_validate_fk(args.engine, run)
    enforce_not_null(args.engine, run)
    if not args.skip_drop:
        drop_method_column(args.engine, run)
    print("Contract phase complete. Run validate.py to confirm final state.")


if __name__ == "__main__":
    main()
