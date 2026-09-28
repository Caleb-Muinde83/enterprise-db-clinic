# Curriculum

The full plan for **enterprise-db-clinic**: what each module simulates, how the problem is diagnosed and fixed on each engine, what counts as success, and what the work actually turned up. All six modules are complete. Each module's own `README.md` holds the runnable steps and the detailed evidence; this document is the map.

---

## 1. Purpose and scope

The project inherits, breaks, diagnoses, and fixes the database problems real enterprises live with: unpartitioned tables, bad indexing, schema drift, changes that can't take the system offline, and end-of-life version upgrades. It is a personal learning lab and the source material for Datech Community case studies.

**Engines**

| Role | Engines | Why |
|---|---|---|
| Hands-on (built and benchmarked locally) | PostgreSQL, MySQL, SQL Server | Three of the four most-used relational engines; all run cleanly in Docker with no licensing friction |
| Comparison write-up only | Oracle, MongoDB | Oracle's licensing and footprint make a smooth local build painful; MongoDB's document model makes some modules apples-to-oranges |

**Versions**

| Engine | Old (modules 0-4) | New (module 5 target) |
|---|---|---|
| PostgreSQL | 11 | 16 |
| MySQL | 5.7 | 8.0 |
| SQL Server | 2017 | 2022 |

SQL Server's old version was originally planned as 2016. Microsoft pruned the `2016-latest` image tag from its registry after 2016 left extended support, so the plan moved to 2017.

---

## 2. Dataset

An e-commerce/fintech hybrid, generated deterministically (seed 42) so every engine holds identical data.

| Table | Rows | Notes |
|---|---|---|
| customers | 500,000 | Zipf-skewed activity: a few power users, a long tail |
| products | 20,000 | |
| orders | 3,000,000 | |
| order_items | 7,498,624 | |
| payments | 2,549,009 | |
| events | 15,000,000 | Time-ordered fact table spanning three years; the table most modules center on |

Tables introduced by later modules: `addresses` (442,767 rows, module 3), `customer_notes` (150,000 rows, module 3), `payment_methods` (small lookup, module 4). Total is roughly 29M rows across nine tables.

Scale presets (`small`, `medium`, `full`) exist so the generator and loaders can be checked quickly before committing to a full 15M-row load.

---

## 3. Conventions that apply to every module

- **Docker only.** No host database clients. Everything runs through `docker exec` against `clinic_pg_old`, `clinic_mysql_old`, `clinic_mssql_old` (and the `_new` containers for module 5). On Git Bash, commands containing Unix-style absolute paths need `MSYS_NO_PATHCONV=1`.
- **Reproducible broken state.** Each module starts from a scripted, version-controlled problem, never a hand-made one.
- **Mechanism-level evidence, not wall-clock timing.** Timing a `docker exec` call is dominated by process and connection overhead for any fast query. Proof is a plan or counter: Postgres `EXPLAIN ANALYZE` execution time, MySQL `EXPLAIN` access type and key (or `EXPLAIN PARTITIONS`), SQL Server `STATISTICS IO` logical reads.
- **Idempotent scripts.** Interruptions are routine (crashes, sleep, killed queries), so every script checks current state before acting. MySQL 5.7 has no `IF [NOT] EXISTS` for index and column DDL, so those checks go through `information_schema` and `PREPARE`/`EXECUTE`.
- **Verify, don't assume.** Check that a fix is actually present locally before re-running anything expensive; check that a drop or create actually happened before benchmarking; check a running query's plan before trusting that it is merely slow.
- **Topic-named directories** (`partitioning/`, `indexing/`, and so on), not phase-numbered ones.

---

## 4. Module map and order

| # | Module | Directory | Status |
|---|---|---|---|
| 0 | Foundation: dataset and environments | `environments/`, `seed/` | Complete |
| 1 | Unpartitioned tables at scale | `partitioning/` | Complete |
| 2 | Missing and incorrect indexing | `indexing/` | Complete |
| 3 | Schema drift and normalization debt | `schema-drift/` | Complete |
| 4 | Zero-downtime migration | `zero-downtime/` | Complete |
| 5 | Deprecated version migration | `version-migration/` | Complete |
| 6 | Cross-engine comparison (Oracle, MongoDB) | `cross-engine-comparison/` | Complete, write-up only |

**Why this order.** The first plan put version migration second. It was deliberately moved to fifth: a version migration is only a meaningful test when it has to carry a schema that already includes partitioning, indexing fixes, cleaned-up structure, and live-migration artifacts. Migrating a clean baseline proves much less. Module 5 therefore ends with a capstone that re-checks modules 1-4 against the migrated instances.

---

## 5. Module 0: Foundation

**Objective.** One realistic dataset and one disposable environment per engine, so every later module starts from a known, reproducible state.

**Deliverables**
- Docker Compose per engine with an old-version and a new-version container side by side. The old container carries modules 1-4; the new one stays empty until module 5.
- A data generator with skewed customer activity, a three-year date range, and low-cardinality status fields, so partitioning and indexing decisions have realistic selectivity to reason about.
- Per-engine schema DDL (no partitioning, no indexes beyond primary keys) and bulk loaders using each engine's native path: `COPY`, `LOAD DATA LOCAL INFILE`, `BULK INSERT`.
- Health checks on every container, with `docker compose up --wait`, because first startup of MySQL and SQL Server takes 20-40 seconds.

**Environment fixes that became part of the baseline**
- MySQL's InnoDB buffer pool raised from the 128MB default to 1GB.
- SQL Server's database set to SIMPLE recovery so a 15M-row `BULK INSERT` doesn't fill the transaction log.
- MySQL's `LOAD DATA` needs `ESCAPED BY '"'` and `\r\n` line terminators to read the generator's RFC 4180 CSVs correctly.
- SQL Server's `BULK INSERT` reads paths inside the container, so generated files go into the directory already mounted at `/data`.

---

## 6. Module 1: Unpartitioned tables at scale

**Objective.** Diagnose a large time-ordered table whose queries scan far more data than they need, fix it with partitioning, and prove the fix with plan evidence.

**Starting state.** `events` at 15M rows, unpartitioned, queried by a bounded "last 30 days" range (about 2.7% of the data).

**Diagnosis.** Baseline the bounded-range query; identify `event_time` as the natural partition key.

**Fix.** 36 monthly partitions plus a catch-all (37 in total) on all three engines, generated by script from the same date range the data generator uses.

| Engine | Mechanism |
|---|---|
| PostgreSQL | Declarative range partitioning; rename-create-copy-drop inside one transaction |
| MySQL | `ALTER TABLE ... PARTITION BY RANGE (TO_DAYS(event_time))`, made idempotent step by step |
| SQL Server | Partition function and scheme, rebuild onto the scheme |

**Engine constraints that shaped the design**
- All three require the partition key inside any primary key, so `event_id`'s PK became `(event_id, event_time)`.
- MySQL InnoDB does not allow foreign keys on partitioned tables in either direction, so `events.customer_id`'s FK had to be dropped. PostgreSQL 11 and SQL Server keep theirs.

**Success criteria.** Partition pruning visible in the plan; substantial drop in work for the bounded query; row counts unchanged.

**Results**

| Engine | Speedup | Evidence |
|---|---|---|
| PostgreSQL | 2.8x | `EXPLAIN ANALYZE`: 1 of 37 partitions scanned |
| MySQL | 11.4x | `EXPLAIN PARTITIONS`: 2 of 37 partitions |
| SQL Server | 3.6x wall-clock, 33.6x fewer logical reads | 271,988 to 8,101 reads |

**Findings worth keeping**
- MySQL's `ALTER TABLE ... PARTITION BY` stalled for over seven hours under the default 128MB buffer pool; 1GB fixed it.
- MySQL DDL auto-commits per statement, so an interrupted run leaves partial progress. Postgres's transactional script rolls back cleanly.
- MySQL 5.7's pruning included one boundary partition it didn't strictly need.
- Every engine survived an abrupt host-level interruption with no data loss through its own crash recovery.

---

## 7. Module 2: Missing and incorrect indexing

**Objective.** Fix a realistic mix of indexing problems and prove each fix with mechanism-level evidence.

**Starting state.** The baseline has no indexes beyond primary keys, and the corruption scripts add three more problems:
1. Missing indexes on `order_items.order_id`, `order_items.product_id`, `payments.order_id`.
2. A wrong-order composite: `orders (status, customer_id)` when queries lead with `customer_id`.
3. A redundant single-column `orders (customer_id)` beside the composite `(customer_id, order_date)`.
4. An unused `products (category)`.

**Workload.** Six timed queries plus two diagnostic modes: an index usage-statistics query (`pg_stat_user_indexes`, `performance_schema`, `sys.dm_db_index_usage_stats`) and a prefix-overlap check for redundant indexes.

**Fix.** Add the missing indexes; replace the composite with `(customer_id, status)`; drop the redundant and unused indexes; add a local per-partition index on `events (customer_id, event_time)`.

**Success criteria.** Scan-to-seek transitions in plans; lower execution time or logical reads; usage statistics that justify each drop.

**Results (highlights)**
- Postgres `events_by_customer_daterange`: 57.163ms to 0.148ms (386x). Partitioning had already narrowed the query to one partition; the new index then removed the full scan inside it.
- SQL Server logical reads: 43,056 to 3 for the `order_items` lookups, 24,441 to 3 for `payments`, 8,101 to 3 for `events`.

**Findings worth keeping**
- Wall-clock timing was unreliable below roughly 5-10ms of real work and produced several apparent "regressions" that were pure overhead. This is why the evidence standard in section 3 exists.
- MySQL InnoDB creates a supporting index for every foreign-key column, and refuses to drop it while the FK exists (`ERROR 1553`). The missing-index scenario simply cannot be staged on those columns in MySQL.
- Indexes are not always a win: one Postgres single-row lookup got slower with the new index because the matching row sat near the start of the table and a sequential scan found it first.

---

## 8. Module 3: Schema drift and normalization debt

**Objective.** Clean up structural debt that accumulated through ad hoc changes, using a batched migration rather than one blocking transaction.

**Starting state.** Two problems, added without disturbing the partitioned and indexed core tables:
- `orders.shipping_address`: a JSON blob repeating each customer's address on every order. 3,000,000 orders collapse to 442,767 distinct addresses, about 85% redundancy.
- `customer_notes`: a bolted-on table with `customer_id` stored as text, no foreign key, 7,493 malformed values (`CUST-XXXX`) and 7,544 orphaned ones among 150,000 rows.

**Diagnosis.** A per-engine validation script reports type, FK presence, malformed count, orphan count, and address redundancy. All three engines agree on every number.

**Fix (`migrate.py`)**
1. Create a proper `addresses` table by parsing the JSON blob (not by shortcutting through `customer_id`).
2. Build an `order_id` to `address_id` mapping table, parsing each row's JSON once.
3. Backfill `orders.address_id` in batches joined through the mapping table.
4. Drop the blob column.
5. Archive the 15,037 malformed and orphaned notes (never silently delete), retype `customer_id` to bigint, add the FK.

**Success criteria.** Zero orphans, zero malformed values, FK present and enforced, every order linked to an address, identical numbers on all three engines.

**Findings worth keeping**
- The first Postgres run took about three hours because the batch `UPDATE` re-parsed the JSON blob five times per row. Parsing once into a mapping table fixed it, and later engines backfilled in minutes (SQL Server in about 103 seconds).
- The same class of bug, wrapping an indexed column in a function or cast so the index can't be used, caused two separate multi-hour MySQL stalls (the collation-fix join and the orphan check). The rule: wrap the non-indexed side of a comparison, and check the plan before trusting it at scale.
- Postgres survived the non-sargable orphan check because its planner built a hash anti-join. MySQL 5.7's did not. A genuine cross-engine optimizer difference.
- MySQL's JSON extraction returns `utf8mb4_bin` while plain columns inherited `latin1_swedish_ci`, so a direct comparison fails; only the JSON side should be converted.
- SQL Server caps nonclustered index keys at 1700 bytes against the address columns' 2080-byte worst case; a warning only, since actual values fit.
- An existence check written as `SELECT 1 ...` returns zero rows rather than a false value, which silently broke string matching across the three CLIs. `COUNT(*)` always returns exactly one row.

---

## 9. Module 4: Zero-downtime migration

**Objective.** Change a live table's structure without taking it offline, and prove "zero downtime" with a concurrent workload rather than by observing that the migration finished.

**Scenario.** Extract `payments.method` (free text, 2.5M rows) into a `payment_methods` lookup table with `payments.payment_method_id` as a validated foreign key, using expand/contract.

**Approach.** Expand (create the lookup table, add a nullable column, backfill in batches), then contract (add the FK, tighten and finally drop the old column). A concurrent reader and writer run against `payments` during every risky phase, and their overlap with the migration is verified from matching wall-clock timestamps, not assumed.

| Engine | Technique for adding the validated FK |
|---|---|
| PostgreSQL | `ADD CONSTRAINT ... NOT VALID`, then `VALIDATE CONSTRAINT` separately |
| MySQL | `ALGORITHM=INPLACE` FK add under `foreign_key_checks=OFF`, followed by a hand-written validation query (no built-in equivalent) |
| SQL Server | `WITH NOCHECK`, then `WITH CHECK CHECK CONSTRAINT` |

**Success criteria.** No errors and no latency elevation in the concurrent workload; FK trusted or validated; no data loss; old column dropped safely.

**Results**
- **PostgreSQL:** clean end to end. Backfill, `NOT VALID` plus `VALIDATE`, `SET NOT NULL`, and `DROP COLUMN` all ran with zero errors and no latency elevation.
- **MySQL:** three engine-specific findings. The in-place FK add needs `foreign_key_checks=OFF`; adding and removing `NOT NULL` are both full 5.7 InnoDB table rewrites (about 243s and 229s, against sub-second on Postgres); FK-add latency spikes come from index-build I/O contention on full-table scans.
- **SQL Server:** `WITH CHECK CHECK CONSTRAINT` was confirmed non-blocking, which answered the module's original open question. Relaxing the old column's `NOT NULL` did block one concurrent read for about 31 seconds through a table-level lock, a different failure mode from MySQL's.

**Findings worth keeping**
- A one-shot backfill leaves a gap for writes that land during it; a sync trigger closes it.
- Dropping the old column is only safe after writers have cut over and after the old column's constraints are relaxed, and that relax step has a real cost on MySQL and SQL Server, so it is not gap-free there.
- Open caveat: Postgres's drop-without-relax test (zero errors) may have been lucky, with a 0.59s window against roughly two operations per second, rather than proof that no equivalent issue exists.

---

## 10. Module 5: Deprecated version migration

**Objective.** Move each engine from its deprecated version to the current one, carrying the schema and data built by modules 1-4, and probe specific breaking behaviors instead of assuming the changelog is accurate.

| Engine | Move | Technique |
|---|---|---|
| PostgreSQL | 11 to 16 | `pg_dump` piped into `psql` |
| MySQL | 5.7 to 8.0 | `mysqldump` piped into `mysql` |
| SQL Server | 2017 to 2022 | `BACKUP DATABASE`, `docker cp`, `RESTORE DATABASE` |

**Success criteria.** Exact row-count parity on all tables (about 29M rows), plus a capstone that re-checks modules 1-4 on the migrated instances: partition pruning, index usage, schema-drift constraints, and the zero-downtime FK.

**Results.** All three engines matched row counts exactly, and the capstone was clean on all three.

**Migration mechanics that needed real fixes**
- MySQL needed three fixes together: a larger destination `innodb_redo_log_capacity`, the `PROCESS` grant on the source, and `net_write_timeout` / `net_read_timeout` raised to 3600s. An earlier attempt reported success after a truncated transfer, because a shell pipeline's exit code reflects only its last command. Driving both `docker exec` processes directly from Python and checking both return codes fixed it.
- SQL Server 2022's image renamed `mssql-tools` to `mssql-tools18`, whose `sqlcmd` enforces TLS by default (`-C` is required), and a freshly started container transiently refuses `sa` logins.

**Breaking-behavior probes (six)**

| # | Engine | Probe | Result |
|---|---|---|---|
| 1 | PostgreSQL | PG15 revoked `CREATE` on `public` from `PUBLIC` | The `clinic` user could still create objects only because it is a superuser. A raw ACL diff showed the new default genuinely took effect and did not carry over the dump/restore for a real non-superuser role |
| 2 | MySQL | Default collation for new columns | New columns get 8.0's `utf8mb4_0900_ai_ci` directly rather than inheriting an old default |
| 3 | MySQL | Default auth plugin | New users get `caching_sha2_password` |
| 4 | MySQL | `GROUP BY` ordering | Row order genuinely differs from 5.7 on identical data; a concrete diff was captured |
| 5 | SQL Server | Compatibility level | Stays at 140 after restoring into 2022; bumping to 160 measurably changed a query plan |
| 6 | SQL Server | Deprecated-feature counter, exercised with the old `*=` outer join | Msg 102, a parser-level syntax error. 2022 has removed that grammar entirely, at any compatibility level, not merely deprecated it |

Probes 1 and 6 came out meaningfully different from what was assumed going in, which is the reason to run them.

---

## 11. Module 6: Cross-engine comparison (Oracle, MongoDB)

**Objective.** Show how the two engines that were not built locally handle the same five problem categories. Research and write-up only; no hands-on build.

**Oracle**
- `DBMS_REDEFINITION` is a purpose-built online shadow-table-and-swap, the pattern module 4 implemented by hand.
- Edition-Based Redefinition lets old and new code editions coexist during a cutover, with no equivalent in the three flagship engines.
- Interval partitioning creates partitions automatically on insert, more automated than anything in modules 1-5.
- AutoUpgrade runs deprecated-feature prechecks before the migration and can skip versions in one hop, the inverse of module 5's probe-after-the-fact approach.

**MongoDB**
- Sharding is a scale-out mechanism, not a pruning mechanism, and shouldn't be conflated with partitioning.
- Flexible schema removes the "wrong-typed bolted-on column" problem as a schema problem, but JSON Schema validators do not retroactively migrate existing documents, so the coexistence problem moves into the application layer.
- Version upgrades are a rolling binary swap across replica-set members, closer to module 4's territory. They must go one major version at a time (no skipping), and new behavior stays dormant until a separate `featureCompatibilityVersion` bump, the same "don't force validation and transport to happen atomically" lesson this project kept rediscovering.

**Conclusion.** The hand-built patterns from this curriculum (sync triggers, expand/contract, relax-before-drop) are not universal truths about databases. They are workarounds for capabilities that Oracle ships natively and that MongoDB's model mostly sidesteps.

---

## 12. Cross-cutting findings

1. **Asymmetries between engines are the recurring story.** MySQL's FK and index coupling, its per-statement DDL commits, and its slower `NOT NULL` changes differ sharply from Postgres's transactional DDL and metadata-only operations; SQL Server sits between them with its own lock behavior.
2. **Optimizers differ on identical SQL.** The same non-sargable comparison was harmless on Postgres and catastrophic on MySQL 5.7.
3. **Evidence quality is part of the work.** Timing noise, silently overwritten "before" results, stale local files, and shell pipes that hide failures each produced convincing but wrong conclusions until the check changed from "did it finish" to "did the mechanism do what we think."
4. **Long-running is not the same as working.** Several multi-hour stalls were queries making progress through an unindexed plan. When something scoped for minutes runs for tens of minutes, read its plan.
5. **Data durability was never the problem.** Disk exhaustion, a laptop sleep, killed containers, and an accidental `docker compose down` all left the data intact; the friction was in getting scripts and local files right.

---

## 13. Repository layout

```
enterprise-db-clinic/
  environments/            docker-compose per engine (old + new containers)
  seed/                    dataset generator, per-engine schema DDL, bulk loaders
  partitioning/            module 1
  indexing/                module 2
  schema-drift/            module 3
  zero-downtime/           module 4
  version-migration/       module 5
  cross-engine-comparison/ module 6
  docs/                    this curriculum
  data/                    generated CSVs (gitignored)
```