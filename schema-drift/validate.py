#!/usr/bin/env python3
"""
validate.py — Quantifies schema drift debt: malformed and orphaned customer_id
values in customer_notes, and the redundancy in orders.shipping_address. Run
before the fix to establish the baseline, and after to prove it worked.

Usage:
    python validate.py --engine postgres
"""

import argparse
import subprocess
import sys

# Per-engine syntax differences: regex flavor for "is this numeric-looking?"
# and how to safely cast a validated numeric string to an integer.
QUERIES = {
    "postgres": {
        "malformed": "SELECT COUNT(*) FROM customer_notes WHERE customer_id !~ '^[0-9]+$';",
        "orphaned": """
            SELECT COUNT(*) FROM customer_notes cn
            WHERE cn.customer_id ~ '^[0-9]+$'
              AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = cn.customer_id::bigint);
        """,
        "redundancy": """
            SELECT COUNT(*) AS total_orders, COUNT(DISTINCT shipping_address) AS distinct_addresses
            FROM orders WHERE shipping_address IS NOT NULL;
        """,
        "fk_check": """
            SELECT tc.constraint_type FROM information_schema.table_constraints tc
            WHERE tc.table_name = 'customer_notes' AND tc.constraint_type = 'FOREIGN KEY';
        """,
        "type_check": """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'customer_notes' AND column_name = 'customer_id';
        """,
    },
    "mysql": {
        "malformed": "SELECT COUNT(*) FROM customer_notes WHERE customer_id NOT REGEXP '^[0-9]+$';",
        "orphaned": """
            SELECT COUNT(*) FROM customer_notes cn
            WHERE cn.customer_id REGEXP '^[0-9]+$'
              AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = CAST(cn.customer_id AS UNSIGNED));
        """,
        "redundancy": """
            SELECT COUNT(*) AS total_orders, COUNT(DISTINCT shipping_address) AS distinct_addresses
            FROM orders WHERE shipping_address IS NOT NULL;
        """,
        "fk_check": """
            SELECT CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_notes' AND CONSTRAINT_TYPE = 'FOREIGN KEY';
        """,
        "type_check": """
            SELECT DATA_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'customer_notes' AND COLUMN_NAME = 'customer_id';
        """,
    },
    "sqlserver": {
        "malformed": "SELECT COUNT(*) FROM customer_notes WHERE customer_id LIKE '%[^0-9]%';",
        "orphaned": """
            SELECT COUNT(*) FROM customer_notes cn
            WHERE cn.customer_id NOT LIKE '%[^0-9]%'
              AND NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id = TRY_CAST(cn.customer_id AS BIGINT));
        """,
        "redundancy": """
            SELECT COUNT(*) AS total_orders, COUNT(DISTINCT shipping_address) AS distinct_addresses
            FROM orders WHERE shipping_address IS NOT NULL;
        """,
        "fk_check": """
            SELECT CONSTRAINT_TYPE FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_NAME = 'customer_notes' AND CONSTRAINT_TYPE = 'FOREIGN KEY';
        """,
        "type_check": """
            SELECT DATA_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_NAME = 'customer_notes' AND COLUMN_NAME = 'customer_id';
        """,
    },
}


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    args = parser.parse_args()

    runner = RUNNERS[args.engine]
    q = QUERIES[args.engine]

    print(f"=== Schema drift report: {args.engine} ===\n")

    print("customer_notes.customer_id current type:")
    print(runner(q["type_check"]).stdout)

    print("Foreign key on customer_notes.customer_id:")
    fk_result = runner(q["fk_check"]).stdout
    print(fk_result if fk_result.strip() else "  (none — no FK constraint exists)")

    print("Malformed customer_id (non-numeric):")
    print(runner(q["malformed"]).stdout)

    print("Orphaned customer_id (numeric, but no matching customer):")
    print(runner(q["orphaned"]).stdout)

    print("Address redundancy in orders.shipping_address:")
    print(runner(q["redundancy"]).stdout)


if __name__ == "__main__":
    main()
