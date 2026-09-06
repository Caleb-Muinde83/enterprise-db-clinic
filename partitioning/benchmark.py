#!/usr/bin/env python3
"""
benchmark.py — Times a representative bounded date-range query against `events`,
before and after partitioning, for all three engines. Runs everything through
docker exec + each engine's own CLI client — no host DB drivers needed, consistent
with the rest of this repo.

The query: "events in the last 30 days" — deliberately a small slice (~2.7%) of the
full 3-year dataset, so partition pruning has a real, visible effect.

Usage:
    python benchmark.py --engine postgres --label before
    python benchmark.py --engine postgres --label after
    python benchmark.py --report          # prints the before/after comparison table
"""

import argparse
import json
import os
import subprocess
import sys
import time

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.json")
RUNS_PER_MEASUREMENT = 5

# Last 30 days of the seeded 3-year window (window ends 2026-09-01, see
# seed/generate_data.py's END_DATE — keep these in sync if that changes).
RANGE_START = "2026-08-02"
RANGE_END = "2026-09-01"

QUERIES = {
    "postgres": (
        f"SELECT COUNT(*) FROM events WHERE event_time >= '{RANGE_START}' "
        f"AND event_time < '{RANGE_END}';"
    ),
    "mysql": (
        f"SELECT COUNT(*) FROM events WHERE event_time >= '{RANGE_START}' "
        f"AND event_time < '{RANGE_END}';"
    ),
    "sqlserver": (
        f"SELECT COUNT(*) FROM events WHERE event_time >= '{RANGE_START}' "
        f"AND event_time < '{RANGE_END}';"
    ),
}

# Plan/pruning-evidence queries, run once (not timed) to show the reader what changed.
PLAN_QUERIES = {
    "postgres": f"EXPLAIN ANALYZE {QUERIES['postgres']}",
    "mysql": f"EXPLAIN PARTITIONS {QUERIES['mysql']}",  # shows which partitions were scanned
    # SQL Server: STATISTICS IO's logical-reads count is the pruning proxy — a real
    # execution plan needs SSMS/XML; logical reads dropping is still solid evidence.
    "sqlserver": f"SET STATISTICS IO ON; {QUERIES['sqlserver']}",
}


def run_postgres(sql):
    return subprocess.run(
        ["docker", "exec", "-i", "clinic_pg_old", "psql", "-U", "clinic", "-d", "clinic",
         "-c", sql],
        capture_output=True, text=True,
    )


def run_mysql(sql):
    return subprocess.run(
        ["docker", "exec", "-i", "clinic_mysql_old", "mysql", "-u", "clinic", "-pclinic",
         "clinic", "-e", sql],
        capture_output=True, text=True,
    )


def run_sqlserver(sql):
    return subprocess.run(
        ["docker", "exec", "-i", "clinic_mssql_old", "/opt/mssql-tools/bin/sqlcmd",
         "-S", "localhost", "-U", "sa", "-P", "Clinic!2017", "-d", "clinic", "-Q", sql],
        capture_output=True, text=True,
    )


RUNNERS = {"postgres": run_postgres, "mysql": run_mysql, "sqlserver": run_sqlserver}


def time_query(engine, n=RUNS_PER_MEASUREMENT):
    runner = RUNNERS[engine]
    sql = QUERIES[engine]
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
    return {
        "runs": timings,
        "median_seconds": timings[len(timings) // 2],
        "min_seconds": timings[0],
        "max_seconds": timings[-1],
    }


def load_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return {}


def save_results(results):
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)


def measure(engine, label):
    print(f"Timing {engine} ({label}) — {RUNS_PER_MEASUREMENT} runs of the last-30-days query...")
    timing = time_query(engine)
    print(f"  median: {timing['median_seconds']*1000:.1f} ms "
          f"(min {timing['min_seconds']*1000:.1f} / max {timing['max_seconds']*1000:.1f})")

    print(f"Capturing plan/pruning evidence for {engine} ({label})...")
    plan_result = RUNNERS[engine](PLAN_QUERIES[engine])
    plan_text = plan_result.stdout

    results = load_results()
    results.setdefault(engine, {})[label] = {
        "timing": timing,
        "plan_evidence": plan_text,
    }
    save_results(results)
    print(f"Saved to {RESULTS_FILE}")


def report():
    results = load_results()
    if not results:
        print("No results yet — run with --engine <name> --label before/after first.")
        return

    print(f"{'Engine':<12} {'Before (ms)':<15} {'After (ms)':<15} {'Speedup':<10}")
    print("-" * 55)
    for engine, data in results.items():
        before = data.get("before", {}).get("timing", {}).get("median_seconds")
        after = data.get("after", {}).get("timing", {}).get("median_seconds")
        before_str = f"{before*1000:.1f}" if before else "—"
        after_str = f"{after*1000:.1f}" if after else "—"
        speedup = f"{before/after:.1f}x" if before and after and after > 0 else "—"
        print(f"{engine:<12} {before_str:<15} {after_str:<15} {speedup:<10}")

    print("\nPlan/pruning evidence is in results.json under each engine's "
          "'plan_evidence' field — inspect for partition scan counts (MySQL's "
          "EXPLAIN PARTITIONS) or logical reads (SQL Server's STATISTICS IO).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=RUNNERS.keys())
    parser.add_argument("--label", choices=["before", "after"])
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    if args.report:
        report()
    elif args.engine and args.label:
        measure(args.engine, args.label)
    else:
        parser.error("Either --report, or both --engine and --label, are required.")


if __name__ == "__main__":
    main()
