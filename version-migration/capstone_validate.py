#!/usr/bin/env python3
"""
capstone_validate.py — the strongest check in this module: re-runs one real
verification from EACH earlier module (partitioning, indexing, schema-drift,
zero-downtime) against the migrated *_new instance. Proves version migration
didn't silently undo anything the rest of the curriculum built -- not just
that row counts match.

Usage:
    python capstone_validate.py --engine postgres
"""

import argparse
import subprocess


def run_postgres(sql, container="clinic_pg_new"):
    r = subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", "clinic", "-d", "clinic", "-c", sql],
        capture_output=True, text=True,
    )
    _diagnose(r)
    return r


def run_mysql(sql, container="clinic_mysql_new"):
    r = subprocess.run(
        ["docker", "exec", "-i", container, "mysql", "-u", "clinic", "-pclinic", "clinic", "-e", sql],
        capture_output=True, text=True,
    )
    _diagnose(r)
    return r


def run_sqlserver(sql, container="clinic_mssql_new"):
    r = subprocess.run(
        ["docker", "exec", "-i", container, "/opt/mssql-tools18/bin/sqlcmd", "-C",
         "-S", "localhost", "-U", "sa", "-P", "Clinic!2022", "-d", "clinic", "-Q", sql],
        capture_output=True, text=True,
    )
    _diagnose(r)
    return r


def _diagnose(r):
    """The bug this fixes: every check function below just printed r.stdout
    and moved on, so a command that failed outright (bad container state,
    connection refused, wrong flags) produced silent blank output instead of
    a visible error -- indistinguishable from a genuine empty/zero result.
    Surface it loudly instead."""
    if r.returncode != 0 or (not r.stdout.strip() and r.stderr.strip()):
        print(f"  [WARNING: command failed or returned nothing -- returncode={r.returncode}]")
        if r.stderr.strip():
            print(f"  STDERR: {r.stderr.strip()[:500]}")


RUNNERS = {"postgres": run_postgres, "mysql": run_mysql, "sqlserver": run_sqlserver}


def header(n, label):
    print(f"\n{'=' * 70}\nModule {n} capstone check: {label}\n{'=' * 70}")


def check_module1_partitioning(engine, run):
    header(1, "partitioning -- 37 monthly partitions on events still exist and still prune")
    if engine == "postgres":
        r = run("SELECT count(*) FROM pg_inherits WHERE inhparent = 'events'::regclass;")
        print(f"Partition count: {r.stdout.strip()}")
        r2 = run("EXPLAIN ANALYZE SELECT * FROM events "
                 "WHERE event_time >= '2024-06-01' AND event_time < '2024-07-01';")
        pruned = "Subplans Removed" in r2.stdout or r2.stdout.count("Seq Scan on events_") <= 2
        print(f"Pruning still works: {'YES' if pruned else 'CHECK MANUALLY'}")
        print(r2.stdout[-600:])
    elif engine == "mysql":
        r = run("SELECT COUNT(*) FROM information_schema.PARTITIONS "
                "WHERE TABLE_SCHEMA='clinic' AND TABLE_NAME='events' AND PARTITION_NAME IS NOT NULL;")
        print(f"Partition count: {r.stdout.strip()}")
        r2 = run("EXPLAIN PARTITIONS SELECT * FROM events "
                 "WHERE event_time >= '2024-06-01' AND event_time < '2024-07-01';")
        print(r2.stdout.strip())
        print("Pruning still works if the partitions column above lists only 1-2 partitions, "
              "not all 37.")
    else:
        r = run("SELECT COUNT(*) FROM sys.partitions WHERE object_id = OBJECT_ID('events') "
                "AND index_id IN (0,1);")
        print(f"Partition count: {r.stdout.strip()}")
        r2 = run("SET STATISTICS IO ON; SELECT * FROM events "
                 "WHERE event_time >= '2024-06-01' AND event_time < '2024-07-01';")
        print(r2.stdout.strip()[-600:])
        print("Pruning still works if 'logical reads' above is small relative to the full "
              "15M-row table, not a near-full-table scan's worth.")


def check_module2_indexing(engine, run):
    header(2, "indexing -- the fix indexes from module 2 still exist post-migration")
    expected = {
        "order_items": ["order_id", "product_id"],
        "payments": ["order_id"],
        "events": ["customer_id"],  # part of the (customer_id, event_time) composite
        "orders": ["customer_id"],  # part of the (customer_id, order_date) composite
    }
    if engine == "postgres":
        r = run("SELECT tablename, indexname, indexdef FROM pg_indexes "
                "WHERE tablename IN ('order_items','payments','events','orders') "
                "AND schemaname='public' ORDER BY tablename, indexname;")
        print(r.stdout)
    elif engine == "mysql":
        r = run("SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA='clinic' AND TABLE_NAME IN "
                "('order_items','payments','events','orders') ORDER BY TABLE_NAME, INDEX_NAME;")
        print(r.stdout)
    else:
        r = run("SELECT t.name AS table_name, i.name AS index_name, c.name AS column_name "
                "FROM sys.indexes i "
                "JOIN sys.tables t ON i.object_id = t.object_id "
                "JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
                "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
                "WHERE t.name IN ('order_items','payments','events','orders') "
                "ORDER BY t.name, i.name;")
        print(r.stdout)
    print(f"\nExpect to see indexes covering: {expected} "
          f"(plus PKs/FK-auto-indexes) -- eyeball the output above against this.")


def check_module3_schema_drift(engine, run):
    header(3, "schema-drift -- customer_notes/addresses FKs still enforced, 0 malformed/orphaned")
    if engine == "postgres":
        r = run("SELECT COUNT(*) FROM customer_notes WHERE customer_id IS NULL;")
        r2 = run("SELECT conname, convalidated FROM pg_constraint WHERE conrelid = 'customer_notes'::regclass "
                 "AND contype = 'f';")
        r3 = run("SELECT COUNT(*) FROM orders WHERE address_id IS NULL;")
    elif engine == "mysql":
        r = run("SELECT COUNT(*) FROM customer_notes WHERE customer_id IS NULL;")
        r2 = run("SELECT constraint_name, constraint_type FROM information_schema.table_constraints "
                 "WHERE table_schema='clinic' AND table_name='customer_notes' AND constraint_type='FOREIGN KEY';")
        r3 = run("SELECT COUNT(*) FROM orders WHERE address_id IS NULL;")
    else:
        r = run("SELECT COUNT(*) FROM customer_notes WHERE customer_id IS NULL;")
        r2 = run("SELECT name, is_not_trusted FROM sys.foreign_keys WHERE parent_object_id = OBJECT_ID('customer_notes');")
        r3 = run("SELECT COUNT(*) FROM orders WHERE address_id IS NULL;")
    print(f"customer_notes.customer_id NULL count (expect 0): {r.stdout.strip()}")
    print(f"FK on customer_notes:\n{r2.stdout.strip()}")
    print(f"orders.address_id NULL count (expect 0): {r3.stdout.strip()}")


def check_module4_zero_downtime(engine, run):
    header(4, "zero-downtime -- payments.payment_method_id FK still present and validated")
    if engine == "postgres":
        r = run("SELECT conname, convalidated FROM pg_constraint "
                "WHERE conname = 'fk_payments_payment_method';")
        r2 = run("SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
        r3 = run("SELECT COUNT(*) FROM information_schema.columns "
                 "WHERE table_name='payments' AND column_name='method';")
    elif engine == "mysql":
        r = run("SELECT constraint_name, constraint_type FROM information_schema.table_constraints "
                "WHERE table_schema='clinic' AND table_name='payments' AND constraint_name='fk_payments_payment_method';")
        r2 = run("SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
        r3 = run("SELECT COUNT(*) FROM information_schema.columns "
                 "WHERE table_schema='clinic' AND table_name='payments' AND column_name='method';")
    else:
        r = run("SELECT name, is_not_trusted FROM sys.foreign_keys WHERE name = 'fk_payments_payment_method';")
        r2 = run("SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")
        r3 = run("SELECT COUNT(*) FROM sys.columns WHERE object_id = OBJECT_ID('payments') AND name = 'method';")

    print(f"FK status: {r.stdout.strip()}")
    print(f"payment_method_id NULL count (expect 0): {r2.stdout.strip()}")
    # COUNT(*)-based, not raw-text presence -- psql/sqlcmd print headers and a
    # "(0 rows)" footer even for a genuinely empty result, so checking whether
    # stdout has ANY text (what this used to do) always looks non-empty and
    # was giving a false "still present" on every run regardless of the real
    # answer. This is the same class of bug schema-drift already documented
    # and fixed once (SELECT 1 returning zero rows isn't a parseable false) --
    # re-learned the hard way here instead of applying it the first time.
    digits = [int(n) for n in r3.stdout.split() if n.strip().isdigit()]
    method_count = digits[0] if digits else None
    if method_count == 0:
        print("method column: confirmed gone")
    elif method_count is None:
        print("method column: CANNOT CONFIRM -- query itself failed, see warning above")
    else:
        print(f"method column: still present?! (count={method_count})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    args = parser.parse_args()
    run = RUNNERS[args.engine]

    print(f"=== Capstone validation: {args.engine} (checking *_new) ===")
    check_module1_partitioning(args.engine, run)
    check_module2_indexing(args.engine, run)
    check_module3_schema_drift(args.engine, run)
    check_module4_zero_downtime(args.engine, run)
    print("\n\nAll four checks printed above -- eyeball each against its 'expect' line. "
          "This script reports, it doesn't auto-pass/fail, since several of these "
          "(partition pruning, index presence) need a human judgment call on the output "
          "shape rather than a single boolean.")


if __name__ == "__main__":
    main()
