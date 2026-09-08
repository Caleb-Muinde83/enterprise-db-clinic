# Unpartitioned Tables at Scale

Goal: diagnose and fix a large, unpartitioned, time-ordered `events` table causing
slow scans and expensive maintenance — then prove the fix with real before/after
numbers, not just "it should be faster."

## Files here

- `generate_partition_ddl.py` — generates the three per-engine migration scripts below
  from the same date-range logic `seed/generate_data.py` uses, so partitions actually
  align with the seeded data.
- `postgres_partition.sql`, `mysql_partition.sql`, `sqlserver_partition.sql` — generated
  migration scripts (36 monthly partitions each, spanning the 3-year seed window).
- `benchmark.py` — times a representative "last 30 days" query before and after,
  via `docker exec` (no host DB drivers needed).
- `results.json` — written by `benchmark.py`; holds timing + plan evidence per engine.

## Engine-specific gotcha, verified against the docs

**MySQL drops the FK on `events.customer_id`.** InnoDB partitioned tables cannot have
foreign keys in either direction — this is a hard MySQL limitation, not a shortcut
taken for this exercise. Referential integrity for `events` has to move to the
application layer or a periodic validation query once it's partitioned in MySQL.

PostgreSQL 11 and SQL Server have no such restriction (PG11 supports a foreign key
*from* a partitioned table to a regular table — only the reverse direction is
unsupported in PG11), so their FK stays intact.

All three engines require the partitioning column to be part of any unique/primary
key, so `event_id`'s PK becomes composite: `(event_id, event_time)` everywhere.

## Steps

### 1. Regenerate at full scale

Small-scale (100K events) won't make partitioning's benefit visible — the whole
table already fits comfortably in memory and scans are fast either way. Regenerate
at `full` (~15M events) first:

```bash
cd ../seed
python generate_data.py --scale full --out ../data
cd ../partitioning
```

Then reload all three engines with the new data (drop and recreate first — see
each engine's `apply-schema-*` step in the top-level README, then re-run the
matching `load_*` script). This will take a while at full scale — that's expected.

### 2. Generate the partition DDL

```bash
python generate_partition_ddl.py --out .
```

### 3. Baseline benchmark (before partitioning)

```bash
python benchmark.py --engine postgres --label before
python benchmark.py --engine mysql --label before
python benchmark.py --engine sqlserver --label before
```

### 4. Apply partitioning per engine

```bash
# Postgres
docker exec -i clinic_pg_old psql -U clinic -d clinic < postgres_partition.sql

# MySQL
docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < mysql_partition.sql

# SQL Server (MSYS_NO_PATHCONV avoids the Git-Bash path-mangling seen in Phase 0)
MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < sqlserver_partition.sql
```

### 5. Re-benchmark (after partitioning)

```bash
python benchmark.py --engine postgres --label after
python benchmark.py --engine mysql --label after
python benchmark.py --engine sqlserver --label after
```

### 6. Compare

```bash
python benchmark.py --report
```

This prints a before/after timing table. The full plan/pruning evidence (Postgres's
`EXPLAIN ANALYZE` output, MySQL's `EXPLAIN PARTITIONS`, SQL Server's `STATISTICS IO`
logical reads) is saved in `results.json` for the write-up.

## What "success" looks like

- Query time on the bounded 30-day range drops substantially (the query should now
  touch ~1 of 36 partitions instead of scanning the whole table).
- MySQL's `EXPLAIN PARTITIONS` output should list only 1-2 partitions instead of all 37.
- Postgres's `EXPLAIN ANALYZE` should show a `Subplans Removed` or per-partition scan
  count far below 36.
- SQL Server's `STATISTICS IO` logical reads should drop substantially between runs.
- Note the write overhead trade-off too, not just the read win — worth a line in the
  eventual write-up.

## Results

All three engines completed and validated at full scale (15M events, 37 monthly partitions):

| Engine | Before | After | Speedup | Mechanism evidence |
|---|---|---|---|---|
| PostgreSQL | 1440ms | 508ms | 2.8x | `EXPLAIN ANALYZE`: 1 of 37 partitions scanned |
| MySQL | 10099ms | 886ms | 11.4x | `EXPLAIN PARTITIONS`: pruned to 2 of 37 partitions |
| SQL Server | 1704ms | 467ms | 3.6x (33.6x fewer logical reads) | `STATISTICS IO`: 271,988 → 8,101 logical reads |

**Notable finding (MySQL):** `EXPLAIN PARTITIONS` included partition `p202309` (the
very first, September 2023) even though no row there could possibly match a filter
on `event_time >= '2026-08-02'`. This is a known MySQL 5.7 partition-pruning
limitation for `TO_DAYS()`-based range partitions with two-sided predicates — the
optimizer is occasionally conservative about boundary partitions. Not a bug in the
migration; still eliminated 35 of 37 partitions.

**Notable finding (all engines):** even after partitioning, none of the three
engines can do a true index *seek* within the matched partition(s) — the composite
PK `(event_id, event_time)` doesn't have `event_time` as a leading column, so each
matched partition still gets a full scan (just a much smaller one). That gap is
exactly what the indexing module addresses next.

**Environment lessons learned along the way** (worth knowing if reproducing this):
- MySQL's default 128MB InnoDB buffer pool caused the partitioning `ALTER TABLE` to
  stall for 7+ hours at 15M-row scale; fixed by bumping it to 1GB in docker-compose.
- MySQL DDL auto-commits per statement (no multi-statement transaction), so an
  interrupted run can leave partial progress — the generated script checks current
  state before each step rather than assuming a fresh start.
- Postgres's migration is fully transactional (`BEGIN`/`COMMIT`), so an interrupted
  run rolls back completely and cleanly — just re-run it.
- All three survived an abrupt host-level interruption (containers crashing
  simultaneously) with no data loss, thanks to each engine's own crash recovery
  (WAL for Postgres, redo log for InnoDB, transaction log for SQL Server).
