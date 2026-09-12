#!/usr/bin/env python3
"""
load_debt_data.py — Bulk-loads addresses.csv and customer_notes.csv, then
populates orders.shipping_address from the staged addresses (one UPDATE,
joined on customer_id — not batched, since this is setup, not the module's
batching lesson; that's reserved for migrate.py's fix). Drops the staging
table afterward since it's just an ETL intermediate, not part of the debt
being diagnosed.

Usage:
    python load_debt_data.py --engine postgres
    python load_debt_data.py --engine mysql
    python load_debt_data.py --engine sqlserver
"""

import argparse
import subprocess
import sys


def run_postgres(data_dir):
    import psycopg2

    conn = psycopg2.connect(host="localhost", port=5411, user="clinic",
                             password="clinic", dbname="clinic")
    conn.autocommit = False
    cur = conn.cursor()
    try:
        print("Loading addresses_staging...")
        with open(f"{data_dir}/addresses.csv") as f:
            cur.copy_expert("COPY addresses_staging FROM STDIN WITH (FORMAT csv, HEADER true)", f)
        print("Loading customer_notes...")
        with open(f"{data_dir}/customer_notes.csv") as f:
            cur.copy_expert("COPY customer_notes FROM STDIN WITH (FORMAT csv, HEADER true)", f)
        conn.commit()

        print("Populating orders.shipping_address from staged addresses...")
        cur.execute("""
            UPDATE orders o
            SET shipping_address = json_build_object(
                'street', a.street, 'city', a.city, 'state', a.state,
                'zip', a.zip, 'country', a.country
            )::text
            FROM addresses_staging a
            WHERE a.customer_id = o.customer_id;
        """)
        conn.commit()

        cur.execute("DROP TABLE addresses_staging;")
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM orders WHERE shipping_address IS NOT NULL;")
        print(f"orders.shipping_address populated: {cur.fetchone()[0]:,} rows")
        cur.execute("SELECT COUNT(*) FROM customer_notes;")
        print(f"customer_notes loaded: {cur.fetchone()[0]:,} rows")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def run_mysql(data_dir):
    import pymysql

    conn = pymysql.connect(host="localhost", port=3357, user="clinic",
                            password="clinic", database="clinic", local_infile=True)
    cur = conn.cursor()
    try:
        print("Loading addresses_staging...")
        cur.execute(f"""
            LOAD DATA LOCAL INFILE '{data_dir}/addresses.csv'
            INTO TABLE addresses_staging
            FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '"'
            LINES TERMINATED BY '\\r\\n'
            IGNORE 1 LINES
        """)
        print("Loading customer_notes...")
        cur.execute(f"""
            LOAD DATA LOCAL INFILE '{data_dir}/customer_notes.csv'
            INTO TABLE customer_notes
            FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '"'
            LINES TERMINATED BY '\\r\\n'
            IGNORE 1 LINES
        """)
        conn.commit()

        print("Populating orders.shipping_address from staged addresses...")
        cur.execute("""
            UPDATE orders o
            JOIN addresses_staging a ON a.customer_id = o.customer_id
            SET o.shipping_address = JSON_OBJECT(
                'street', a.street, 'city', a.city, 'state', a.state,
                'zip', a.zip, 'country', a.country
            );
        """)
        conn.commit()

        cur.execute("DROP TABLE addresses_staging;")
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM orders WHERE shipping_address IS NOT NULL;")
        print(f"orders.shipping_address populated: {cur.fetchone()[0]:,} rows")
        cur.execute("SELECT COUNT(*) FROM customer_notes;")
        print(f"customer_notes loaded: {cur.fetchone()[0]:,} rows")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def run_sqlserver(container_data_dir):
    load_sql = f"""
USE clinic;
GO
BULK INSERT addresses_staging
FROM '{container_data_dir}/addresses.csv'
WITH (FORMAT='CSV', FIRSTROW=2, FIELDTERMINATOR=',', ROWTERMINATOR='0x0d0a', FIELDQUOTE='"', TABLOCK);
GO
BULK INSERT customer_notes
FROM '{container_data_dir}/customer_notes.csv'
WITH (FORMAT='CSV', FIRSTROW=2, FIELDTERMINATOR=',', ROWTERMINATOR='0x0d0a', FIELDQUOTE='"', TABLOCK);
GO
UPDATE o
SET o.shipping_address = CONCAT(
    '{{"street":"', REPLACE(a.street, '"', '\\"'),
    '","city":"', REPLACE(a.city, '"', '\\"'),
    '","state":"', REPLACE(a.state, '"', '\\"'),
    '","zip":"', REPLACE(a.zip, '"', '\\"'),
    '","country":"', REPLACE(a.country, '"', '\\"'), '"}}'
)
FROM orders o
JOIN addresses_staging a ON a.customer_id = o.customer_id;
GO
DROP TABLE addresses_staging;
GO
SELECT COUNT(*) AS shipping_address_populated FROM orders WHERE shipping_address IS NOT NULL;
GO
SELECT COUNT(*) AS customer_notes_loaded FROM customer_notes;
GO
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "clinic_mssql_old", "/opt/mssql-tools/bin/sqlcmd",
         "-S", "localhost", "-U", "sa", "-P", "Clinic!2017"],
        input=load_sql, capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", required=True, choices=["postgres", "mysql", "sqlserver"])
    parser.add_argument("--data-dir", default="../data",
                         help="Path to the CSVs as seen from THIS script (postgres/mysql)")
    parser.add_argument("--container-data-dir", default="/data",
                         help="Path to the CSVs as seen INSIDE the container (sqlserver only)")
    args = parser.parse_args()

    if args.engine == "postgres":
        run_postgres(args.data_dir)
    elif args.engine == "mysql":
        run_mysql(args.data_dir)
    else:
        run_sqlserver(args.container_data_dir)


if __name__ == "__main__":
    main()
