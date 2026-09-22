#!/usr/bin/env python3
"""
validate.py — Reports payments/payment_methods state: backfill completeness,
FK presence, and whether the old `method` column is gone. Run before expand.py
(sanity check baseline), after expand.py (confirm backfill), and after
contract.py (confirm final state) -- same three-checkpoint pattern as
schema-drift's validate.py.

Usage:
    python validate.py --engine postgres
"""

import argparse
import subprocess
import sys


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


def show(run, label, sql):
    print(f"\n{label}:")
    result = run(sql)
    if result.returncode != 0:
        print(f"  (query failed -- likely means the thing being checked doesn't exist yet: {result.stderr.strip()[:150]})")
        return
    print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    args = parser.parse_args()
    run = RUNNERS[args.engine]
    e = args.engine

    print(f"=== Zero-downtime module report: {e} ===")

    show(run, "payment_methods row count (expect 4)", "SELECT COUNT(*) FROM payment_methods;")

    show(run, "Total payments", "SELECT COUNT(*) FROM payments;")

    show(run, "payments with payment_method_id still NULL (expect 0 once backfilled)",
         "SELECT COUNT(*) FROM payments WHERE payment_method_id IS NULL;")

    if e == "postgres":
        show(run, "FK on payments.payment_method_id", """
            SELECT constraint_name, constraint_type FROM information_schema.table_constraints
            WHERE table_name = 'payments' AND constraint_type = 'FOREIGN KEY';
        """)
        show(run, "Is the FK validated (convalidated=true means yes)", """
            SELECT conname, convalidated FROM pg_constraint WHERE conname = 'fk_payments_payment_method';
        """)
        show(run, "Does payments.method still exist", """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'payments' AND column_name = 'method';
        """)
    elif e == "mysql":
        show(run, "FK on payments.payment_method_id", """
            SELECT constraint_name, constraint_type FROM information_schema.table_constraints
            WHERE table_schema = 'clinic' AND table_name = 'payments' AND constraint_type = 'FOREIGN KEY';
        """)
        show(run, "Does payments.method still exist", """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'clinic' AND table_name = 'payments' AND column_name = 'method';
        """)
    else:
        show(run, "FK on payments.payment_method_id", """
            SELECT name, is_not_trusted FROM sys.foreign_keys WHERE name = 'fk_payments_payment_method';
        """)
        show(run, "Does payments.method still exist", """
            SELECT name FROM sys.columns WHERE object_id = OBJECT_ID('payments') AND name = 'method';
        """)

    print("\n(is_not_trusted=0 / convalidated=t both mean 'FK fully validated' -- "
          "confirm this reads 'trusted/validated' before calling contract.py complete.)")


if __name__ == "__main__":
    main()
