#!/usr/bin/env python3
"""
benchmark.py — Times 6 representative queries against the indexing workload,
before and after the fix, for all three engines. Same docker-exec approach as
the partitioning module's benchmark — no host DB drivers needed.

Unlike partitioning's single query, this module has multiple query "problems"
to demonstrate at once (missing indexes, wrong-order composite, redundant index,
unused index), so this runs all 6 timed queries in one call per --engine/--label.

Usage:
    python benchmark.py --engine postgres --label before
    python benchmark.py --engine postgres --label after
    python benchmark.py --report

    python benchmark.py --engine postgres --usage-stats   # unused/redundant index evidence
    python benchmark.py --engine postgres --duplicate-check  # prefix-overlap detection
"""

import argparse
import json
import os
import subprocess
import sys
import time

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.json")
RUNS_PER_MEASUREMENT = 5

RANGE_START = "2026-08-02"
RANGE_END = "2026-09-01"

# query_name -> SQL text, per engine. Kept mostly identical across engines;
# only date/quoting differences would go here if they existed (they don't, for these).
# customer_id 261876 chosen by querying actual order-count distribution — ranked
# ~5000th of 500K customers by order count (52 orders). customer_id=1 was
# originally used here but turned out to have only 3 orders total, an
# unrepresentatively rare case where docker-exec/connection overhead (a few
# hundred ms, constant across every query) swamped the real signal entirely.
QUERIES = {
    "orders_by_customer": "SELECT COUNT(*) FROM orders WHERE customer_id = 261876;",
    "orders_by_customer_status": "SELECT COUNT(*) FROM orders WHERE customer_id = 261876 AND status = 'paid';",
    "order_items_by_order": "SELECT COUNT(*) FROM order_items WHERE order_id = 1;",
    "order_items_by_product": "SELECT COUNT(*) FROM order_items WHERE product_id = 1;",
    "payments_by_order": "SELECT COUNT(*) FROM payments WHERE order_id = 1;",
    "events_by_customer_daterange": (
        f"SELECT COUNT(*) FROM events WHERE customer_id = 261876 "
        f"AND event_time >= '{RANGE_START}' AND event_time < '{RANGE_END}';"
    ),
}

PLAN_PREFIX = {
    "postgres": "EXPLAIN ANALYZE ",
    "mysql": "EXPLAIN ",
    "sqlserver": "SET STATISTICS IO ON; ",  # prefixed per-query at call time
}

USAGE_STATS_QUERY = {
    "postgres": """
        SELECT indexrelname, idx_scan, idx_tup_read
        FROM pg_stat_user_indexes
        WHERE indexrelname IN ('ix_products_category', 'ix_orders_customer_id',
                                'ix_orders_customer_date')
        ORDER BY indexrelname;
    """,
    "mysql": """
        SELECT OBJECT_NAME AS table_name, INDEX_NAME, COUNT_STAR AS usage_count
        FROM performance_schema.table_io_waits_summary_by_index_usage
        WHERE OBJECT_SCHEMA = DATABASE()
          AND INDEX_NAME IN ('ix_products_category', 'ix_orders_customer_id',
                              'ix_orders_customer_date')
        ORDER BY INDEX_NAME;
    """,
    "sqlserver": """
        SELECT i.name AS index_name, s.user_seeks, s.user_scans, s.user_lookups
        FROM sys.indexes i
        LEFT JOIN sys.dm_db_index_usage_stats s
          ON s.object_id = i.object_id AND s.index_id = i.index_id
        WHERE i.name IN ('ix_products_category', 'ix_orders_customer_id',
                          'ix_orders_customer_date')
        ORDER BY i.name;
    """,
}

# Finds indexes whose key-column list is a strict prefix of another index's key
# columns on the same table — the general form of "redundant index" detection.
DUPLICATE_CHECK_QUERY = {
    "postgres": """
        SELECT a.indexrelid::regclass AS possibly_redundant,
               b.indexrelid::regclass AS covers_it
        FROM pg_index a JOIN pg_index b
          ON a.indrelid = b.indrelid AND a.indexrelid != b.indexrelid
        WHERE a.indkey::text = substring(b.indkey::text, 1, length(a.indkey::text))
          AND array_length(a.indkey, 1) < array_length(b.indkey, 1);
    """,
    "mysql": """
        SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME
        FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders'
        ORDER BY INDEX_NAME, SEQ_IN_INDEX;
    """,  # MySQL: no clean single-query prefix check; inspect column order manually from this listing
    "sqlserver": """
        SELECT i.name AS index_name, ic.key_ordinal, c.name AS column_name
        FROM sys.indexes i
        JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
        JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
        WHERE i.object_id = OBJECT_ID('orders') AND ic.key_ordinal > 0
        ORDER BY i.name, ic.key_ordinal;
    """,  # Same approach as MySQL: list key column order per index, compare by eye
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


def time_query(engine, sql, n=RUNS_PER_MEASUREMENT):
    runner = RUNNERS[engine]
    timings = []
    for _ in range(n):
        start = time.perf_counter()
        result = runner(sql)
        elapsed = time.perf_counter() - start
        if result.returncode != 0:
            print(f"Query failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
        timings.append(elapsed)
    timings.sort()
    return {"median_seconds": timings[len(timings) // 2],
            "min_seconds": timings[0], "max_seconds": timings[-1]}


def load_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return {}


def save_results(results):
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)


def measure(engine, label, only_query=None):
    results = load_results()
    results.setdefault(engine, {}).setdefault(label, {})

    queries_to_run = {only_query: QUERIES[only_query]} if only_query else QUERIES

    for query_name, sql in queries_to_run.items():
        print(f"Timing {engine} ({label}) — {query_name}...")
        timing = time_query(engine, sql)
        print(f"  median: {timing['median_seconds']*1000:.1f} ms")

        plan_sql = PLAN_PREFIX[engine] + sql
        plan_result = RUNNERS[engine](plan_sql)
        results[engine][label][query_name] = {
            "timing": timing, "plan_evidence": plan_result.stdout,
        }

    save_results(results)
    print(f"Saved to {RESULTS_FILE}")


def usage_stats(engine):
    print(f"Index usage stats for {engine} (unused/redundant index evidence):")
    result = RUNNERS[engine](USAGE_STATS_QUERY[engine])
    print(result.stdout)


def duplicate_check(engine):
    print(f"Index key-column listing for {engine} (compare prefixes by eye for MySQL/SQL Server):")
    result = RUNNERS[engine](DUPLICATE_CHECK_QUERY[engine])
    print(result.stdout)


def _parse_mysql_explain(text):
    """MySQL's EXPLAIN output is tab-separated with a header row — 'type' and 'key'
    are column NAMES, so a regex search for the literal word finds the header row
    itself, not the data below it. Parse by column position instead."""
    lines = [l for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return "—"
    header = lines[0].split("\t")
    data = lines[1].split("\t")
    row = dict(zip(header, data))
    return f"{row.get('type', '?')}/{row.get('key', '?')}"


def evidence_report(engine):
    """Extracts real, overhead-free evidence per query: Postgres's EXPLAIN ANALYZE
    Execution Time, MySQL's plan type/key (structural proof, since 5.7 has no
    EXPLAIN ANALYZE timing), SQL Server's STATISTICS IO logical reads. Wall-clock
    docker-exec timing is unreliable for sub-millisecond queries (confirmed
    repeatedly during this module's development) — this is the trustworthy number."""
    import re
    results = load_results()
    data = results.get(engine, {})

    print(f"{'Query':<32} {'Before':<20} {'After':<20}")
    print("-" * 76)

    for q in QUERIES:
        before_plan = data.get("before", {}).get(q, {}).get("plan_evidence", "")
        after_plan = data.get("after", {}).get(q, {}).get("plan_evidence", "")

        if engine == "postgres":
            bm = re.search(r"Execution Time: ([\d.]+) ms", before_plan)
            am = re.search(r"Execution Time: ([\d.]+) ms", after_plan)
            before_val = f"{bm.group(1)}ms" if bm else "—"
            after_val = f"{am.group(1)}ms" if am else "—"
        elif engine == "mysql":
            before_val = _parse_mysql_explain(before_plan)
            after_val = _parse_mysql_explain(after_plan)
        else:  # sqlserver
            bm = re.search(r"logical reads (\d+)", before_plan)
            am = re.search(r"logical reads (\d+)", after_plan)
            before_val = f"{bm.group(1)} reads" if bm else "—"
            after_val = f"{am.group(1)} reads" if am else "—"

        print(f"{q:<32} {before_val:<20} {after_val:<20}")


def report():
    results = load_results()
    if not results:
        print("No results yet — run with --engine <name> --label before/after first.")
        return

    for engine, data in results.items():
        print(f"\n=== {engine} ===")
        print(f"{'Query':<32} {'Before (ms)':<15} {'After (ms)':<15} {'Speedup':<10}")
        print("-" * 72)
        before_data = data.get("before", {})
        after_data = data.get("after", {})
        for query_name in QUERIES:
            before = before_data.get(query_name, {}).get("timing", {}).get("median_seconds")
            after = after_data.get(query_name, {}).get("timing", {}).get("median_seconds")
            before_str = f"{before*1000:.1f}" if before else "—"
            after_str = f"{after*1000:.1f}" if after else "—"
            speedup = f"{before/after:.1f}x" if before and after and after > 0 else "—"
            print(f"{query_name:<32} {before_str:<15} {after_str:<15} {speedup:<10}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=RUNNERS.keys())
    parser.add_argument("--label", choices=["before", "after"])
    parser.add_argument("--query", choices=QUERIES.keys(), default=None,
                         help="Benchmark only this one query instead of all 6")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--evidence-report", action="store_true",
                         help="Real mechanism-level evidence per query (execution time / "
                              "index usage / logical reads), not noisy wall-clock timing")
    parser.add_argument("--usage-stats", action="store_true")
    parser.add_argument("--duplicate-check", action="store_true")
    args = parser.parse_args()

    if args.report:
        report()
    elif args.evidence_report:
        if not args.engine:
            parser.error("--evidence-report requires --engine")
        evidence_report(args.engine)
    elif args.usage_stats:
        if not args.engine:
            parser.error("--usage-stats requires --engine")
        usage_stats(args.engine)
    elif args.duplicate_check:
        if not args.engine:
            parser.error("--duplicate-check requires --engine")
        duplicate_check(args.engine)
    elif args.engine and args.label:
        measure(args.engine, args.label, only_query=args.query)
    else:
        parser.error("Either --report, --evidence-report, --usage-stats, "
                      "--duplicate-check, or both --engine and --label, are required.")


if __name__ == "__main__":
    main()
