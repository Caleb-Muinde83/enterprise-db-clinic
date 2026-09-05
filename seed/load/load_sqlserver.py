#!/usr/bin/env python3
"""
load_sqlserver.py — Bulk-load generated CSVs into SQL Server using BULK INSERT.

IMPORTANT: SQL Server's BULK INSERT reads files from a path visible to the *server*
process, not the client. In the containerized setup this means the CSVs must be
mounted into the container (see the commented volume mount in
environments/sqlserver/docker-compose.yml) — update --container-data-dir below to
match wherever you mount them.

Assumes schema/sqlserver_schema.sql has already been applied.

Usage:
    python load_sqlserver.py --container-data-dir /data --host localhost --port 14330 \
        --user sa --password 'Clinic!2016' --dbname clinic
"""

import argparse
import pyodbc

TABLES_IN_ORDER = ["customers", "products", "orders", "order_items", "payments", "events"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--container-data-dir", required=True,
                         help="Path to the CSVs as seen INSIDE the SQL Server container")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=14330)
    parser.add_argument("--user", default="sa")
    parser.add_argument("--password", required=True)
    parser.add_argument("--dbname", default="clinic")
    args = parser.parse_args()

    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={args.host},{args.port};DATABASE={args.dbname};"
        f"UID={args.user};PWD={args.password};TrustServerCertificate=yes;"
    )
    conn = pyodbc.connect(conn_str, autocommit=False)
    cur = conn.cursor()

    try:
        for table in TABLES_IN_ORDER:
            path = f"{args.container_data_dir}\\{table}.csv"
            print(f"Loading {table} from {path} (container path) ...")
            cur.execute(f"""
                BULK INSERT {table}
                FROM '{path}'
                WITH (
                    FORMAT = 'CSV',
                    FIRSTROW = 2,
                    FIELDTERMINATOR = ',',
                    ROWTERMINATOR = '0x0d0a',
                    FIELDQUOTE = '"',
                    TABLOCK
                )
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
