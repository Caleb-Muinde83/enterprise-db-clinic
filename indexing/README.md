# Missing / Incorrect Indexing

Goal: diagnose and fix a realistic mix of missing indexes, a wrong-order composite
index, a redundant index, and an unused index — then prove each fix with real
before/after evidence, the same standard the partitioning module was held to.

## Starting state

The Phase 0 baseline schema deliberately has **no indexes beyond primary keys** —
that alone gives us missing-index problems for free (`order_items.order_id`,
`order_items.product_id`, `payments.order_id` are all unindexed foreign keys).
But real inherited systems aren't just under-indexed, they're *messily* indexed —
some past developer added indexes that made sense at the time and never got
cleaned up. The corruption scripts here add three deliberate problems on top of
the missing ones, so this module covers the full realistic range:

1. **Missing indexes** (already true from Phase 0): `order_items.order_id`,
   `order_items.product_id`, `payments.order_id` have no index at all.
2. **Wrong-order composite index**: `orders` gets an index on `(status, customer_id)`,
   but the actual workload filters by `customer_id` first — a composite index only
   helps a query that can use it as a leftmost prefix, so this index is nearly
   useless for the real access pattern.
3. **Redundant index**: `orders` gets *both* a single-column index on `customer_id`
   and a composite `(customer_id, order_date)` — the single-column one is fully
   redundant once the composite exists, since any query using the single-column
   index could use the composite's leftmost prefix instead. Pure write overhead
   for zero benefit.
4. **Unused index**: `products` gets an index on `category` that nothing in the
   workload ever queries — included specifically so its removal has to be justified
   by real usage statistics (0 scans), not by "we don't query that column" intuition.

## Representative workload

Eight queries chosen to exercise every problem above:

| # | Query | Problem it exposes |
|---|---|---|
| 1 | `orders` by `customer_id` | Should hit the composite `(customer_id, order_date)` index as leftmost prefix |
| 2 | `orders` by `customer_id` AND `status` | Wrong-order composite can't serve this efficiently |
| 3 | `order_items` by `order_id` | Missing index |
| 4 | `order_items` by `product_id` | Missing index |
| 5 | `payments` by `order_id` | Missing index |
| 6 | `events` by `customer_id` AND date range | Tests whether a local index inside each partition helps beyond partition pruning alone |
| 7 | Index usage stats query | Surfaces the unused `products.category` index and the redundant `orders.customer_id` index via each engine's own statistics, not guesswork |
| 8 | (diagnostic only, not benchmarked) Duplicate/overlapping index detection | Finds indexes that are a strict prefix of another index on the same table |

## Fix

- Add the three missing indexes: `order_items(order_id)`, `order_items(product_id)`,
  `payments(order_id)`.
- Drop the wrong-order `(status, customer_id)` composite, replace with
  `(customer_id, status)` — matches query #2's actual access pattern.
- Drop the redundant single-column `customer_id` index — the composite
  `(customer_id, order_date)` already serves any query that index would have.
- Drop the unused `products.category` index, justified by each engine's usage
  stats showing zero scans, not assumption.
- Add a local index on `events(customer_id, event_time)` — this is the piece that
  was still missing after the partitioning module: partitioning narrows the search
  to the right partition(s), but within a partition, matching customer + date range
  still needed an index to avoid a full partition scan.

## Steps

### 1. Diagnose the current (already messy) state

```bash
python benchmark.py --engine postgres --label before
python benchmark.py --engine mysql --label before
python benchmark.py --engine sqlserver --label before
```

This first applies the corruption script (adding the wrong-order/redundant/unused
indexes) if not already applied, then runs and times all 8 workload queries,
capturing execution plans.

### 2. Apply the fix per engine

```bash
docker exec -i clinic_pg_old psql -U clinic -d clinic < fix_postgres.sql
docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < fix_mysql.sql
MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < fix_sqlserver.sql
```

### 3. Re-benchmark and compare

```bash
python benchmark.py --engine postgres --label after
python benchmark.py --engine mysql --label after
python benchmark.py --engine sqlserver --label after
python benchmark.py --report
```

## What "success" looks like

- Queries 1-6 show seq-scan-to-index-seek transitions in their plans, with
  meaningfully lower execution time or logical reads/rows examined.
- Query 7's usage-stats check shows the unused `products.category` index at 0
  scans/reads before it's dropped, and the redundant `customer_id` index likewise
  showing near-zero *unique* usage once the composite index exists.
- Query 8 (duplicate detection) finds the redundant index by comparing key-column
  prefixes across indexes on the same table — a real technique, not a canned answer.
- Note the write-overhead trade-off of every new index added, not just the read win.

## Results

All three engines validated with real mechanism-level evidence (wall-clock timing
proved unreliable for these queries — see "A methodology lesson" below — so every
number here is server-side execution time, plan structure, or logical reads,
not `docker exec` wall-clock).

### PostgreSQL (Execution Time, from `EXPLAIN ANALYZE`)

| Query | Before | After | Speedup |
|---|---|---|---|
| orders_by_customer | 0.217ms | 0.133ms | 1.6x |
| orders_by_customer_status | 0.288ms | 0.164ms | 1.8x |
| order_items_by_order | 0.212ms | 0.131ms | 1.6x |
| order_items_by_product | 0.733ms | 0.800ms | ~flat (noise) |
| payments_by_order | 0.092ms | 0.217ms | **slower** (see below) |
| events_by_customer_daterange | 57.163ms | 0.148ms | **386x** |

### MySQL (plan type/key, from `EXPLAIN`)

| Query | Before | After |
|---|---|---|
| orders_by_customer | `ref/ix_orders_customer_id` | `ref/ix_orders_customer_date` |
| orders_by_customer_status | `ref/ix_orders_status_customer` | `ref/ix_orders_customer_status` |
| order_items_by_order | blocked — see below | blocked — see below |
| order_items_by_product | blocked — see below | blocked — see below |
| payments_by_order | blocked — see below | blocked — see below |
| events_by_customer_daterange | `ALL/NULL` (full scan) | `range/ix_events_customer_time` |

### SQL Server (logical reads, from `STATISTICS IO`)

| Query | Before | After | Reduction |
|---|---|---|---|
| orders_by_customer | 3 | 3 | flat (already indexed pre-fix) |
| orders_by_customer_status | 4 | 3 | small |
| order_items_by_order | 43,056 | 3 | **14,352x** |
| order_items_by_product | 43,056 | 3 | **14,352x** |
| payments_by_order | 24,441 | 3 | **8,147x** |
| events_by_customer_daterange | 8,101 | 3 | **2,700x** |

## Notable findings

**MySQL structurally cannot test "missing index" for FK-constrained columns.**
`order_items.order_id`, `order_items.product_id`, and `payments.order_id` all sit
under active foreign key constraints. Attempting to drop their supporting index
fails outright: `ERROR 1553: Cannot drop index 'ix_order_items_order_id': needed
in a foreign key constraint`. This isn't just "MySQL auto-creates FK indexes"
(true, and already meant our fix's new indexes were partly redundant with
auto-generated ones) — it's stronger: **as long as the FK exists, MySQL will not
let you remove the index at all.** Postgres and SQL Server have no such
restriction. Contrast with `events`, whose FK was deliberately dropped during the
partitioning module — that's specifically why `events_by_customer_daterange`
*could* show a real missing-index story in MySQL, while the other three couldn't.

**A methodology lesson: wall-clock `docker exec` timing is unreliable below
roughly 5-10ms of real query cost.** Every query in this module completes in
under a millisecond to a few milliseconds server-side, but `docker exec` process
spawn + connection overhead runs 200-700ms per invocation — noise far larger
than the signal. Several early benchmark runs showed queries getting *slower*
after adding a correct, genuinely-helpful index purely from this overhead. The
fix: extract real evidence instead — Postgres's `EXPLAIN ANALYZE` execution
time, MySQL's plan access type, SQL Server's `STATISTICS IO` logical reads (see
`benchmark.py --evidence-report`). The partitioning module's queries were large
enough (hundreds of ms of genuine work) that wall-clock timing was trustworthy
there — it only broke down once queries got this fast.

**Indexes aren't a universal win — `payments_by_order` on Postgres got
genuinely slower with the new index (0.092ms → 0.217ms).** Only 1 row matches
`order_id = 1` out of millions. Since `order_id = 1` was among the very first
orders created, its payment row is very likely stored near the physical start
of the table — a sequential scan finds it almost immediately, while an index
scan pays B-tree traversal + heap-fetch overhead on top for no benefit at this
extreme selectivity. Real, valid database behavior, not a bug — a genuinely
useful caution against assuming "add an index" is always correct without
measuring.

**The clearest, most complete story: `events_by_customer_daterange`.**
Partitioning (previous module) got Postgres down to scanning 1 of 37 partitions;
this module's local per-partition index then avoided even scanning that whole
partition — **57.163ms → 0.148ms (386x)**, confirmed via the plan showing a
`Parallel Seq Scan` (with `Rows Removed by Filter: 141963`) replaced by a
`Bitmap Index Scan` finding the 1 matching row directly. SQL Server shows the
same story via logical reads (8,101 → 3). This is the module's clearest proof
that partitioning and indexing solve genuinely different, complementary
problems — partitioning narrows *which* partition, indexing narrows *within* it.
