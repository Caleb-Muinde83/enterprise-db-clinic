#!/usr/bin/env python3
"""
concurrent_writer.py — The actual zero-downtime proof. Runs in a SEPARATE
terminal, in parallel with expand.py and contract.py, and hammers `payments`
with both writes and reads on a loop, logging per-operation latency and any
error to a CSV. If expand.py or contract.py ever actually blocks reads or
writes, it shows up here as a latency spike or a hard error — not as an
assumption from documentation.

Alternates:
  - WRITE: INSERT a synthetic payment row against a real, existing order_id
    (picked from a small pre-fetched pool so it doesn't need its own lookup
    query every iteration)
  - READ (point): SELECT a single payment by payment_id
  - READ (scan): SELECT COUNT(*) FROM payments — cheap but touches a lot of
    rows, more likely to collide with a lock than a point lookup would

Usage:
    python concurrent_writer.py --engine postgres --duration 300 --log writer_log.csv
"""

import argparse
import csv
import random
import subprocess
import sys
import time
from datetime import datetime, timezone


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

# NOTE: each iteration pays docker-exec + CLI-client startup overhead on top of
# the query itself (same as every other script in this project, which all
# shell out per-statement rather than holding an open connection). That
# overhead is roughly constant, so it doesn't hide a real lock-wait spike --
# but don't read the absolute latency numbers as "query time" in isolation,
# only relative spikes against this run's own baseline.


def fetch_order_pool(engine, run, n=500):
    if engine == "sqlserver":
        sql = f"SELECT TOP {n} order_id FROM orders ORDER BY NEWID();"
    else:
        sql = f"SELECT order_id FROM orders ORDER BY RAND() LIMIT {n};" if engine == "mysql" \
            else f"SELECT order_id FROM orders ORDER BY RANDOM() LIMIT {n};"
    result = run(sql)
    ids = [int(tok) for tok in result.stdout.split() if tok.strip().isdigit()]
    if not ids:
        print("Could not fetch an order_id pool -- aborting.", file=sys.stderr)
        sys.exit(1)
    return ids


def do_write(engine, run, order_id, write_mode):
    """write_mode='method' simulates a pre-cutover app (writes the old text
    column, needs the trigger to get payment_method_id). write_mode='fk'
    simulates a post-cutover app (writes payment_method_id directly, no
    trigger dependency, and survives method being dropped). Use 'method'
    while testing expand.py/contract.py's FK+NOT NULL steps; switch to 'fk'
    only once satisfied those are clean, to test the actual column drop
    against a writer that's already cut over -- the drop is only safe once
    every real writer has made this same switch, so this flag exists to
    let you deliberately model each side of that boundary."""
    if write_mode == "fk":
        col, val = "payment_method_id", "1"  # 1 = 'card' per expand.py's seed order
    else:
        col, val = "method", "'card'"

    if engine == "sqlserver":
        sql = (f"INSERT INTO payments (order_id, payment_date, amount, {col}, status) "
               f"VALUES ({order_id}, GETDATE(), 9.99, {val}, 'success');")
    elif engine == "mysql":
        sql = (f"INSERT INTO payments (order_id, payment_date, amount, {col}, status) "
               f"VALUES ({order_id}, NOW(), 9.99, {val}, 'success');")
    else:
        sql = (f"INSERT INTO payments (order_id, payment_date, amount, {col}, status) "
               f"VALUES ({order_id}, NOW(), 9.99, {val}, 'success');")
    return run(sql)


def do_point_read(engine, run, payment_id):
    return run(f"SELECT * FROM payments WHERE payment_id = {payment_id};")


def do_scan_read(engine, run):
    return run("SELECT COUNT(*) FROM payments;")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=RUNNERS.keys())
    parser.add_argument("--duration", type=int, default=300, help="seconds to run")
    parser.add_argument("--log", default="writer_log.csv")
    parser.add_argument("--write-mode", choices=["method", "fk"], default="method",
                         help="'method' = pre-cutover app (writes old text column, needs the "
                              "trigger). 'fk' = post-cutover app (writes payment_method_id "
                              "directly, survives method being dropped). Default 'method'.")
    args = parser.parse_args()
    run = RUNNERS[args.engine]

    print(f"=== concurrent_writer: {args.engine}, {args.duration}s, logging to {args.log} ===")
    order_pool = fetch_order_pool(args.engine, run)
    max_payment_id = None
    r = run("SELECT MAX(payment_id) FROM payments;")
    digits = [int(t) for t in r.stdout.split() if t.strip().isdigit()]
    max_payment_id = digits[-1] if digits else 1

    ops = ["write", "point_read", "point_read", "scan_read"]  # reads weighted 3:1 over writes
    end_time = time.time() + args.duration
    errors = 0
    total = 0

    with open(args.log, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "op", "latency_ms", "error"])

        while time.time() < end_time:
            op = random.choice(ops)
            t0 = time.time()
            if op == "write":
                result = do_write(args.engine, run, random.choice(order_pool), args.write_mode)
            elif op == "point_read":
                result = do_point_read(args.engine, run, random.randint(1, max_payment_id))
            else:
                result = do_scan_read(args.engine, run)
            latency_ms = (time.time() - t0) * 1000
            total += 1

            error_text = ""
            if result.returncode != 0 or "error" in result.stderr.lower():
                errors += 1
                error_text = result.stderr.strip().replace("\n", " ")[:200]

            writer.writerow([datetime.now(timezone.utc).isoformat(), op, f"{latency_ms:.1f}", error_text])
            f.flush()

            if latency_ms > 2000:
                print(f"  SPIKE: {op} took {latency_ms:.0f}ms")
            if error_text:
                print(f"  ERROR on {op}: {error_text}")

    print(f"=== Done: {total} ops, {errors} errors. Full log in {args.log} ===")
    print("Load into validate.py's summary, or eyeball the CSV directly for latency spikes "
          "clustered around a specific migration step's timestamp.")


if __name__ == "__main__":
    main()
