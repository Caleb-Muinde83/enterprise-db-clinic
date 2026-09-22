#!/usr/bin/env python3
"""
expand.py — Phase 1 of the zero-downtime FK extraction: creates the
`payment_methods` lookup table, adds a NULLABLE `payments.payment_method_id`
column, and batch-backfills it by joining on the existing `method` text.

Nullable + no constraint means this phase alone can never block on existing
traffic — that's the point of doing it as its own step before contract.py
touches anything that could lock. Run concurrent_writer.py against `payments`
in a separate terminal WHILE this runs; the batched backfill across 2.5M rows
is real sustained work and is exactly what needs proving non-blocking, not
just the later FK-add.

Usage:
    python expand.py --engine postgres
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

BATCH_SIZE = 250_000
METHODS = ["card", "paypal", "bank_transfer", "wallet"]


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
    """Runs a SELECT COUNT(*)-style query and extracts the integer result.
    Same pattern as schema-drift's idempotency checks: COUNT(*) always
    returns exactly one row, unlike SELECT 1 which returns zero rows (and
    therefore no parseable "1") when the thing being checked doesn't exist."""
    result = run(sql)
    check(result, f"count query: {sql}")
    digits = [int(n) for n in result.stdout.split() if n.strip().isdigit()]
    return digits[-1] if digits else 0


def payment_methods_table_exists(engine, run):
    if engine == "postgres":
        n = count(run, "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'payment_methods';")
    elif engine == "mysql":
        n = count(run, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'clinic' AND table_name = 'payment_methods';")
    else:
        n = count(run, "SELECT COUNT(*) FROM sys.tables WHERE name = 'payment_methods';")
    return n > 0


def payment_method_id_column_exists(engine, run):
    if engine == "postgres":
        n = count(run, "SELECT COUNT(*) FROM information_schema.columns WHERE table_name = 'payments' AND column_name = 'payment_method_id';")
    elif engine == "mysql":
        n = count(run, "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'clinic' AND table_name = 'payments' AND column_name = 'payment_method_id';")
    else:
        n = count(run, "SELECT COUNT(*) FROM sys.columns WHERE object_id = OBJECT_ID('payments') AND name = 'payment_method_id';")
    return n > 0


def create_payment_methods(engine, run):
    if payment_methods_table_exists(engine, run):
        print("payment_methods already exists, skipping create+seed.")
        return
    print("Creating and seeding payment_methods...")
    if engine == "postgres":
        steps = [
            """CREATE TABLE payment_methods (
                   payment_method_id SMALLINT PRIMARY KEY,
                   method_name TEXT NOT NULL UNIQUE
               );""",
        ]
    elif engine == "mysql":
        steps = [
            """CREATE TABLE payment_methods (
                   payment_method_id SMALLINT PRIMARY KEY,
                   method_name VARCHAR(20) NOT NULL UNIQUE
               ) ENGINE=InnoDB;""",
        ]
    else:
        steps = [
            """CREATE TABLE payment_methods (
                   payment_method_id SMALLINT PRIMARY KEY,
                   method_name VARCHAR(20) NOT NULL UNIQUE
               );""",
        ]
    for i, m in enumerate(METHODS, start=1):
        steps.append(f"INSERT INTO payment_methods (payment_method_id, method_name) VALUES ({i}, '{m}');")
    for s in steps:
        check(run(s), "create_payment_methods")


def add_nullable_column(engine, run):
    if payment_method_id_column_exists(engine, run):
        print("payments.payment_method_id already exists, skipping ADD COLUMN.")
        return
    print("Adding nullable payments.payment_method_id...")
    if engine == "postgres":
        sql = "ALTER TABLE payments ADD COLUMN payment_method_id SMALLINT;"
    elif engine == "mysql":
        sql = "ALTER TABLE payments ADD COLUMN payment_method_id SMALLINT NULL;"
    else:
        sql = "ALTER TABLE payments ADD payment_method_id SMALLINT NULL;"
    check(run(sql), "add_nullable_column")


def sync_trigger_exists(engine, run):
    if engine == "postgres":
        n = count(run, "SELECT COUNT(*) FROM pg_trigger WHERE tgname = 'trg_payments_sync_method_id';")
    elif engine == "mysql":
        n = count(run, "SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema = 'clinic' AND trigger_name = 'trg_payments_sync_method_id_ins';")
    else:
        n = count(run, "SELECT COUNT(*) FROM sys.triggers WHERE name = 'trg_payments_sync_method_id';")
    return n > 0


def create_sync_trigger(engine, run):
    """MUST run before the backfill loop starts, not after. Without this, any
    row written concurrently during the migration (by a real app, or by
    concurrent_writer.py simulating one) lands with payment_method_id NULL and
    never gets caught by the batched backfill's fixed id range -- observed
    directly in the first real run of this module: 64 unmatched rows right
    after backfill grew to 320 by the time the writer stopped, all valid
    'card' values that simply arrived after their batch had already passed.
    A one-shot backfill alone is not sufficient for a live table; this
    trigger closes that gap going forward, so post-backfill NULLs then
    genuinely mean 'no matching method' rather than 'arrived too late'."""
    if sync_trigger_exists(engine, run):
        print("Sync trigger already exists, skipping.")
        return
    print("Creating sync trigger so new writes during migration self-populate payment_method_id...")

    if engine == "postgres":
        steps = [
            """CREATE OR REPLACE FUNCTION payments_sync_method_id() RETURNS TRIGGER AS $$
               BEGIN
                   IF NEW.payment_method_id IS NULL THEN
                       SELECT payment_method_id INTO NEW.payment_method_id
                       FROM payment_methods WHERE method_name = NEW.method;
                   END IF;
                   RETURN NEW;
               END;
               $$ LANGUAGE plpgsql;""",
            """CREATE TRIGGER trg_payments_sync_method_id
               BEFORE INSERT OR UPDATE ON payments
               FOR EACH ROW EXECUTE FUNCTION payments_sync_method_id();""",
        ]
    elif engine == "mysql":
        # MySQL requires separate triggers per event (no combined INSERT OR UPDATE).
        steps = [
            "DROP TRIGGER IF EXISTS trg_payments_sync_method_id_ins;",
            """CREATE TRIGGER trg_payments_sync_method_id_ins BEFORE INSERT ON payments
               FOR EACH ROW
               BEGIN
                   IF NEW.payment_method_id IS NULL THEN
                       SET NEW.payment_method_id = (
                           SELECT payment_method_id FROM payment_methods
                           WHERE method_name = NEW.method LIMIT 1);
                   END IF;
               END;""",
            "DROP TRIGGER IF EXISTS trg_payments_sync_method_id_upd;",
            """CREATE TRIGGER trg_payments_sync_method_id_upd BEFORE UPDATE ON payments
               FOR EACH ROW
               BEGIN
                   IF NEW.payment_method_id IS NULL THEN
                       SET NEW.payment_method_id = (
                           SELECT payment_method_id FROM payment_methods
                           WHERE method_name = NEW.method LIMIT 1);
                   END IF;
               END;""",
        ]
    else:
        # SQL Server triggers are AFTER-only here (no BEFORE); update via the
        # `inserted` pseudo-table, restricted to rows still NULL to stay cheap.
        steps = [
            """CREATE TRIGGER trg_payments_sync_method_id ON payments
               AFTER INSERT, UPDATE AS
               BEGIN
                   SET NOCOUNT ON;
                   UPDATE p SET p.payment_method_id = pm.payment_method_id
                   FROM payments p
                   JOIN inserted i ON p.payment_id = i.payment_id
                   JOIN payment_methods pm ON pm.method_name = i.method
                   WHERE p.payment_method_id IS NULL;
               END;""",
        ]
    for s in steps:
        check(run(s), "create_sync_trigger")


def backfill(engine, run):
    remaining = count(run, "SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
    if remaining == 0:
        print("payment_method_id already fully backfilled, nothing to do.")
        return
    print(f"Backfilling payment_method_id for {remaining:,} rows in batches of {BATCH_SIZE:,}...")

    min_id = count(run, "SELECT COALESCE(MIN(payment_id), 0) FROM payments WHERE payment_method_id IS NULL;")
    max_id = count(run, "SELECT COALESCE(MAX(payment_id), 0) FROM payments;")

    lo = min_id
    batch_num = 0
    while lo <= max_id:
        hi = min(lo + BATCH_SIZE - 1, max_id)
        batch_num += 1
        start_ts = datetime.now(timezone.utc).isoformat()
        t0 = time.time()
        if engine == "postgres":
            sql = f"""
                UPDATE payments p SET payment_method_id = pm.payment_method_id
                FROM payment_methods pm
                WHERE p.method = pm.method_name
                  AND p.payment_id BETWEEN {lo} AND {hi}
                  AND p.payment_method_id IS NULL;
            """
        elif engine == "mysql":
            sql = f"""
                UPDATE payments p
                JOIN payment_methods pm ON p.method = pm.method_name
                SET p.payment_method_id = pm.payment_method_id
                WHERE p.payment_id BETWEEN {lo} AND {hi}
                  AND p.payment_method_id IS NULL;
            """
        else:
            sql = f"""
                UPDATE p SET p.payment_method_id = pm.payment_method_id
                FROM payments p
                JOIN payment_methods pm ON p.method = pm.method_name
                WHERE p.payment_id BETWEEN {lo} AND {hi}
                  AND p.payment_method_id IS NULL;
            """
        check(run(sql), f"backfill batch {batch_num}")
        end_ts = datetime.now(timezone.utc).isoformat()
        print(f"  batch {batch_num}: payment_id {lo:,}-{hi:,} | {start_ts} -> {end_ts} "
              f"| {time.time() - t0:.2f}s")
        lo = hi + 1

    print("Backfill complete.")

    unmatched = count(run, "SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
    if unmatched > 0:
        print(f"WARNING: {unmatched} rows still NULL after backfill — method values with no "
              f"match in payment_methods. Inspect before running contract.py; do not force "
              f"NOT NULL over unmatched rows.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    args = parser.parse_args()
    run = RUNNERS[args.engine]

    print(f"=== Expand: {args.engine} ===")
    create_payment_methods(args.engine, run)
    add_nullable_column(args.engine, run)
    create_sync_trigger(args.engine, run)  # MUST precede backfill -- see docstring
    backfill(args.engine, run)
    print("Expand phase complete. Any NULLs remaining now mean a genuinely unmatched "
          "method value (not a race with concurrent writes, since the trigger covers "
          "those going forward) -- inspect with:\n"
          "  SELECT method, COUNT(*) FROM payments WHERE payment_method_id IS NULL GROUP BY method;\n"
          "Then re-run validate.py, keep concurrent_writer.py running, and move to contract.py.")


if __name__ == "__main__":
    main()
