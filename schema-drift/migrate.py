#!/usr/bin/env python3
"""
migrate.py — The batched fix for schema drift debt.

1. Creates a proper `addresses` table, extracting distinct addresses from the
   orders.shipping_address JSON blob (real parsing, not a shortcut back
   through customer_id — addresses_staging is already gone by this point,
   same as a real migration wouldn't have a convenient staging table lying
   around months later).
2. Backfills orders.address_id in BATCHES (chunked by order_id range, with a
   commit between each) — this is the module's actual batching lesson. A
   single UPDATE touching every row would hold a lock for the whole
   operation; batching trades total wall-clock time for much shorter
   individual lock durations.
3. Drops the now-redundant shipping_address blob column.
4. Archives malformed/orphaned customer_notes rows (never silently deletes),
   fixes the column type, adds the FK constraint.

Usage:
    python migrate.py --engine postgres
"""

import argparse
import subprocess
import sys
import time

BATCH_SIZE = 250_000


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


# Per-engine JSON field extraction for the address blob.
JSON_EXTRACT = {
    "postgres": {
        "street": "shipping_address::jsonb->>'street'", "city": "shipping_address::jsonb->>'city'",
        "state": "shipping_address::jsonb->>'state'", "zip": "shipping_address::jsonb->>'zip'",
        "country": "shipping_address::jsonb->>'country'",
    },
    "mysql": {
        "street": "shipping_address->>'$.street'", "city": "shipping_address->>'$.city'",
        "state": "shipping_address->>'$.state'", "zip": "shipping_address->>'$.zip'",
        "country": "shipping_address->>'$.country'",
    },
    "sqlserver": {
        "street": "JSON_VALUE(shipping_address, '$.street')", "city": "JSON_VALUE(shipping_address, '$.city')",
        "state": "JSON_VALUE(shipping_address, '$.state')", "zip": "JSON_VALUE(shipping_address, '$.zip')",
        "country": "JSON_VALUE(shipping_address, '$.country')",
    },
}


def create_addresses_table(engine, run):
    if engine == "postgres":
        sql = """
            CREATE TABLE IF NOT EXISTS addresses (
                address_id BIGSERIAL PRIMARY KEY,
                street TEXT NOT NULL, city TEXT NOT NULL, state TEXT NOT NULL,
                zip TEXT NOT NULL, country TEXT NOT NULL,
                UNIQUE (street, city, state, zip, country)
            );
        """
    elif engine == "mysql":
        sql = """
            CREATE TABLE IF NOT EXISTS addresses (
                address_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                street VARCHAR(255) NOT NULL, city VARCHAR(255) NOT NULL, state VARCHAR(255) NOT NULL,
                zip VARCHAR(20) NOT NULL, country VARCHAR(255) NOT NULL,
                UNIQUE KEY uq_address (street, city, state, zip, country)
            ) ENGINE=InnoDB;
        """
    else:  # sqlserver
        sql = """
            IF OBJECT_ID('dbo.addresses', 'U') IS NULL
            CREATE TABLE addresses (
                address_id BIGINT IDENTITY(1,1) PRIMARY KEY,
                street NVARCHAR(255) NOT NULL, city NVARCHAR(255) NOT NULL, state NVARCHAR(255) NOT NULL,
                zip NVARCHAR(20) NOT NULL, country NVARCHAR(255) NOT NULL,
                CONSTRAINT uq_address UNIQUE (street, city, state, zip, country)
            );
        """
    check(run(sql), "create addresses table")


def populate_addresses(engine, run):
    e = JSON_EXTRACT[engine]
    if engine == "postgres":
        sql = f"""
            INSERT INTO addresses (street, city, state, zip, country)
            SELECT DISTINCT {e['street']}, {e['city']}, {e['state']}, {e['zip']}, {e['country']}
            FROM orders WHERE shipping_address IS NOT NULL
            ON CONFLICT DO NOTHING;
        """
    elif engine == "mysql":
        sql = f"""
            INSERT IGNORE INTO addresses (street, city, state, zip, country)
            SELECT DISTINCT {e['street']}, {e['city']}, {e['state']}, {e['zip']}, {e['country']}
            FROM orders WHERE shipping_address IS NOT NULL;
        """
    else:  # sqlserver — no INSERT IGNORE/ON CONFLICT; use NOT EXISTS
        sql = f"""
            INSERT INTO addresses (street, city, state, zip, country)
            SELECT DISTINCT {e['street']}, {e['city']}, {e['state']}, {e['zip']}, {e['country']}
            FROM orders o
            WHERE shipping_address IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM addresses a
                  WHERE a.street = {e['street']} AND a.city = {e['city']}
                    AND a.state = {e['state']} AND a.zip = {e['zip']} AND a.country = {e['country']}
              );
        """
    check(run(sql), "populate addresses (distinct extraction from blob)")


def get_order_id_bounds(engine, run):
    result = run("SELECT MIN(order_id), MAX(order_id) FROM orders;")
    check(result, "get order_id bounds")
    # Parse the min/max out of whichever engine's client formatted the output
    nums = [int(n) for n in result.stdout.replace("|", " ").split() if n.strip().lstrip("-").isdigit()]
    return min(nums), max(nums)


def add_address_id_column(engine, run):
    if engine == "postgres":
        sql = "ALTER TABLE orders ADD COLUMN IF NOT EXISTS address_id BIGINT;"
    elif engine == "mysql":
        sql = """
            SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders' AND COLUMN_NAME='address_id');
            SET @sql = IF(@col_exists=0, 'ALTER TABLE orders ADD COLUMN address_id BIGINT', 'SELECT 1');
            PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
        """
    else:
        sql = "IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('orders') AND name='address_id') ALTER TABLE orders ADD address_id BIGINT;"
    check(run(sql), "add orders.address_id column")


def backfill_batch(engine, run, lo, hi):
    e = JSON_EXTRACT[engine]
    if engine == "sqlserver":
        sql = f"""
            UPDATE o SET o.address_id = a.address_id
            FROM orders o JOIN addresses a
              ON a.street = {e['street']} AND a.city = {e['city']} AND a.state = {e['state']}
             AND a.zip = {e['zip']} AND a.country = {e['country']}
            WHERE o.order_id BETWEEN {lo} AND {hi} AND o.shipping_address IS NOT NULL;
        """
    elif engine == "mysql":
        sql = f"""
            UPDATE orders o JOIN addresses a
              ON a.street = {e['street']} AND a.city = {e['city']} AND a.state = {e['state']}
             AND a.zip = {e['zip']} AND a.country = {e['country']}
            SET o.address_id = a.address_id
            WHERE o.order_id BETWEEN {lo} AND {hi} AND o.shipping_address IS NOT NULL;
        """
    else:  # postgres
        sql = f"""
            UPDATE orders o SET address_id = a.address_id
            FROM addresses a
            WHERE a.street = {e['street']} AND a.city = {e['city']} AND a.state = {e['state']}
              AND a.zip = {e['zip']} AND a.country = {e['country']}
              AND o.order_id BETWEEN {lo} AND {hi} AND o.shipping_address IS NOT NULL;
        """
    return run(sql)


def backfill_address_ids(engine, run):
    lo_bound, hi_bound = get_order_id_bounds(engine, run)
    print(f"Backfilling address_id for order_id {lo_bound:,}-{hi_bound:,} "
          f"in batches of {BATCH_SIZE:,}...")
    lo = lo_bound
    batch_num = 0
    while lo <= hi_bound:
        hi = min(lo + BATCH_SIZE - 1, hi_bound)
        batch_num += 1
        start = time.perf_counter()
        result = backfill_batch(engine, run, lo, hi)
        check(result, f"backfill batch {batch_num} (order_id {lo}-{hi})")
        elapsed = time.perf_counter() - start
        print(f"  batch {batch_num}: order_id {lo:,}-{hi:,} done in {elapsed:.2f}s")
        lo = hi + 1
    print("Backfill complete.")


def drop_shipping_address_column(engine, run):
    if engine == "postgres":
        sql = "ALTER TABLE orders DROP COLUMN IF EXISTS shipping_address;"
    elif engine == "mysql":
        sql = """
            SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='orders' AND COLUMN_NAME='shipping_address');
            SET @sql = IF(@col_exists>0, 'ALTER TABLE orders DROP COLUMN shipping_address', 'SELECT 1');
            PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
        """
    else:
        sql = "IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id=OBJECT_ID('orders') AND name='shipping_address') ALTER TABLE orders DROP COLUMN shipping_address;"
    check(run(sql), "drop orders.shipping_address")


def fix_customer_notes(engine, run):
    print("Archiving malformed/orphaned customer_notes rows...")
    if engine == "postgres":
        steps = [
            """CREATE TABLE IF NOT EXISTS customer_notes_orphaned_archive (LIKE customer_notes INCLUDING ALL);""",
            """INSERT INTO customer_notes_orphaned_archive
               SELECT * FROM customer_notes cn
               WHERE cn.customer_id !~ '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id::text = cn.customer_id);""",
            """DELETE FROM customer_notes cn
               WHERE cn.customer_id !~ '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE c.customer_id::text = cn.customer_id);""",
            """ALTER TABLE customer_notes ALTER COLUMN customer_id TYPE BIGINT USING customer_id::bigint;""",
            """ALTER TABLE customer_notes ADD CONSTRAINT fk_customer_notes_customer
               FOREIGN KEY (customer_id) REFERENCES customers(customer_id);""",
        ]
    elif engine == "mysql":
        steps = [
            """CREATE TABLE IF NOT EXISTS customer_notes_orphaned_archive LIKE customer_notes;""",
            """INSERT INTO customer_notes_orphaned_archive
               SELECT * FROM customer_notes cn
               WHERE cn.customer_id NOT REGEXP '^[0-9]+$'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE CAST(c.customer_id AS CHAR) = cn.customer_id);""",
            """DELETE FROM customer_notes
               WHERE customer_id NOT REGEXP '^[0-9]+$'
                  OR customer_id NOT IN (SELECT CAST(customer_id AS CHAR) FROM customers);""",
            """ALTER TABLE customer_notes MODIFY COLUMN customer_id BIGINT NOT NULL;""",
            """ALTER TABLE customer_notes ADD CONSTRAINT fk_customer_notes_customer
               FOREIGN KEY (customer_id) REFERENCES customers(customer_id);""",
        ]
    else:  # sqlserver
        steps = [
            """IF OBJECT_ID('dbo.customer_notes_orphaned_archive','U') IS NULL
               SELECT * INTO customer_notes_orphaned_archive FROM customer_notes WHERE 1=0;""",
            """INSERT INTO customer_notes_orphaned_archive
               SELECT * FROM customer_notes cn
               WHERE cn.customer_id LIKE '%[^0-9]%'
                  OR NOT EXISTS (SELECT 1 FROM customers c WHERE CAST(c.customer_id AS NVARCHAR(20)) = cn.customer_id);""",
            """DELETE FROM customer_notes
               WHERE customer_id LIKE '%[^0-9]%'
                  OR customer_id NOT IN (SELECT CAST(customer_id AS NVARCHAR(20)) FROM customers);""",
            """ALTER TABLE customer_notes ALTER COLUMN customer_id BIGINT NOT NULL;""",
            """ALTER TABLE customer_notes ADD CONSTRAINT fk_customer_notes_customer
               FOREIGN KEY (customer_id) REFERENCES customers(customer_id);""",
        ]

    for i, sql in enumerate(steps, 1):
        check(run(sql), f"customer_notes fix step {i}")
    print("customer_notes fixed: archived, retyped, FK added.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    args = parser.parse_args()
    run = RUNNERS[args.engine]

    print(f"=== Migrating {args.engine} ===\n")
    create_addresses_table(args.engine, run)
    populate_addresses(args.engine, run)
    add_address_id_column(args.engine, run)
    backfill_address_ids(args.engine, run)
    drop_shipping_address_column(args.engine, run)
    fix_customer_notes(args.engine, run)
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
