#!/usr/bin/env python3
"""
load_sqlserver.py — Bulk-load generated CSVs into SQL Server via the container's own
sqlcmd (over `docker exec`), rather than pyodbc.

Why not pyodbc: pip installing the `pyodbc` package only gets you the Python bindings —
Microsoft's actual ODBC driver binary has to be installed separately, system-wide, which
is an unnecessary extra dependency on Windows/macOS/Linux alike. Going through the
container's own sqlcmd (the same tool used for schema application) needs nothing
installed on the host at all, consistent with the rest of this repo's approach.

IMPORTANT: SQL Server's BULK INSERT reads files from a path visible to the *container*,
not your host machine. environments/sqlserver/docker-compose.yml mounts your local
data/ directory to /data inside the container — --container-data-dir should match that
mount point (default: /data).

Usage:
    python load_sqlserver.py --container-data-dir /data --password 'Clinic!2017'
"""

import argparse
import subprocess
import sys
import tempfile
import os

TABLES_IN_ORDER = ["customers", "products", "orders", "order_items", "payments", "events"]

SQLCMD_PATH_IN_CONTAINER = "/opt/mssql-tools/bin/sqlcmd"


def build_load_script(container_data_dir, dbname):
    statements = [f"USE {dbname};", "GO"]
    for table in TABLES_IN_ORDER:
        path = f"{container_data_dir}/{table}.csv"
        statements.append(f"""
BULK INSERT {table}
FROM '{path}'
WITH (
    FORMAT = 'CSV',
    FIRSTROW = 2,
    FIELDTERMINATOR = ',',
    ROWTERMINATOR = '0x0d0a',
    FIELDQUOTE = '"',
    TABLOCK
);
GO
PRINT '{table}: ' + CAST((SELECT COUNT(*) FROM {table}) AS VARCHAR(20)) + ' rows';
GO
""")
    return "\n".join(statements)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container-data-dir", default="/data",
                         help="Path to the CSVs as seen INSIDE the container (default: /data)")
    parser.add_argument("--container-name", default="clinic_mssql_old")
    parser.add_argument("--user", default="sa")
    parser.add_argument("--password", required=True)
    parser.add_argument("--dbname", default="clinic")
    args = parser.parse_args()

    script = build_load_script(args.container_data_dir, args.dbname)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False,
                                      encoding="utf-8") as f:
        f.write(script)
        script_path = f.name

    try:
        with open(script_path, "rb") as script_file:
            result = subprocess.run(
                ["docker", "exec", "-i", args.container_name,
                 SQLCMD_PATH_IN_CONTAINER, "-S", "localhost",
                 "-U", args.user, "-P", args.password],
                stdin=script_file,
                capture_output=True,
                text=True,
            )

        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)

    finally:
        os.unlink(script_path)


if __name__ == "__main__":
    main()
