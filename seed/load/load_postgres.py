#!/usr/bin/env python3
"""
load_postgres.py — Bulk-load generated CSVs into PostgreSQL using native COPY.

Assumes schema/postgres_schema.sql has already been applied.

Usage:
    python load_postgres.py --data-dir ../data --host localhost --port 5411 \
        --user clinic --password clinic --dbname clinic
"""

import argparse
import psycopg2

TABLES_IN_ORDER = ["customers", "products", "orders", "order_items", "payments", "events"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5411)
    parser.add_argument("--user", default="clinic")
    parser.add_argument("--password", default="clinic")
    parser.add_argument("--dbname", default="clinic")
    args = parser.parse_args()

    conn = psycopg2.connect(host=args.host, port=args.port, user=args.user,
                             password=args.password, dbname=args.dbname)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        for table in TABLES_IN_ORDER:
            path = f"{args.data_dir}/{table}.csv"
            print(f"Loading {table} from {path} ...")
            with open(path, "r", encoding="utf-8") as f:
                cur.copy_expert(
                    f"COPY {table} FROM STDIN WITH (FORMAT csv, HEADER true)", f
                )
            conn.commit()
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  -> {cur.fetchone()[0]:,} rows in {table}")

        # keep sequences in sync with the explicit IDs we just loaded
        for table, pk in [("customers", "customer_id"), ("products", "product_id"),
                           ("orders", "order_id"), ("order_items", "order_item_id"),
                           ("payments", "payment_id"), ("events", "event_id")]:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'), "
                f"COALESCE((SELECT MAX({pk}) FROM {table}), 1))"
            )
        conn.commit()
        print("All tables loaded and sequences synced.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
