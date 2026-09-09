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
