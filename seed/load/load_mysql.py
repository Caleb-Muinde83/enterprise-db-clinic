#!/usr/bin/env python3
"""
load_mysql.py — Bulk-load generated CSVs into MySQL using native LOAD DATA LOCAL INFILE.

Assumes schema/mysql_schema.sql has already been applied, and that the server was
started with --local-infile=1 (already set in environments/mysql/docker-compose.yml).

Usage:
    python load_mysql.py --data-dir ../data --host localhost --port 3357 \
        --user clinic --password clinic --dbname clinic
"""

import argparse
import pymysql

TABLES_IN_ORDER = ["customers", "products", "orders", "order_items", "payments", "events"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3357)
    parser.add_argument("--user", default="clinic")
    parser.add_argument("--password", default="clinic")
    parser.add_argument("--dbname", default="clinic")
    args = parser.parse_args()

    conn = pymysql.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        database=args.dbname, local_infile=True,
    )
    cur = conn.cursor()

    try:
        for table in TABLES_IN_ORDER:
            path = f"{args.data_dir}/{table}.csv"
            print(f"Loading {table} from {path} ...")
            # metadata column in `events` holds JSON with commas — safe under CSV quoting,
            # LOAD DATA respects ENCLOSED BY for that.
            cur.execute(f"""
                LOAD DATA LOCAL INFILE '{path}'
                INTO TABLE {table}
                FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
                LINES TERMINATED BY '\\n'
                IGNORE 1 LINES
            """)
            conn.commit()
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  -> {cur.fetchone()[0]:,} rows in {table}")

        print("All tables loaded.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
