# Module 4: Zero-downtime migration

## The problem being simulated

`payments.method` is free text (`'card' | 'paypal' | 'bank_transfer' | 'wallet'`) with
no referential integrity — nothing stops a typo or a new unvalidated value from landing
in a 2.5M-row table that's under constant read/write load in production. The fix is to
extract it into a proper `payment_methods` lookup table and add a validated FK from
`payments.payment_method_id` — but `payments` can't be taken offline to do it.

Unlike `schema-drift`, which proved a batched fix was *correct*, this module's job is to
prove a live-table schema change is *actually non-blocking* — not just that it finishes,
but that concurrent reads and writes against `payments` never stall or error while it runs.

## Approach: expand → validate → contract

1. **Expand** (`expand.py`) — create `payment_methods` (seeded with the 4 known values),
   add a nullable `payments.payment_method_id` column, batch-backfill it by joining on
   the existing `method` text (same batched-UPDATE pattern as `schema-drift`, batch size
   250,000). Nullable + no constraint yet means this step alone can never block on
   existing traffic.
2. **Constrain** (`contract.py`) — add the FK and make `payment_method_id` NOT NULL, then
   drop the old `method` text column. How the FK gets added, and whether it can be split
   into a fast unvalidated step plus a separate slow-but-non-blocking validation step, is
   genuinely different per engine — see below.
3. **Validate** (`validate.py`) — confirms 100% of rows have `payment_method_id`, the FK
   is present and trusted/enforced, and `method` is gone.

Throughout expand *and* contract — not just contract — `concurrent_writer.py` runs
against `payments` in a separate terminal, doing both writes and reads, logging latency
and any errors. The backfill is a batched UPDATE across 2.5M rows — real sustained work
against a live table, arguably higher-risk than the constraint-add itself — so it gets
tested too, not just the FK step.

## Per-engine FK technique — and a real asymmetry, not smoothed over

| Engine | Technique |
|---|---|
| PostgreSQL | `ADD CONSTRAINT ... FOREIGN KEY ... NOT VALID` (near-instant, skips the initial full scan+lock), then `VALIDATE CONSTRAINT` as a separate statement (`SHARE UPDATE EXCLUSIVE` lock only — documented as not blocking reads/writes) |
| SQL Server | `WITH NOCHECK ADD CONSTRAINT ... FOREIGN KEY ...` (fast, unvalidated/"untrusted"), then `WITH CHECK CHECK CONSTRAINT ...` as a separate statement to validate |
| MySQL (InnoDB) | `ALGORITHM=INPLACE ADD FOREIGN KEY ...` — validation happens *inside* the same statement. There is no separate "add unvalidated, validate later" step to expose on this engine. |

MySQL's single-step shape isn't a gap in this module's design — it's a real, documented
cross-engine difference in how the three engines let you defer constraint validation,
and it's exactly the kind of finding worth writing up alongside the FK-index and
sargability findings from earlier modules.

## Open question this module is designed to answer empirically

Postgres's `VALIDATE CONSTRAINT` is documented as non-blocking for concurrent reads and
writes. Whether SQL Server's `WITH CHECK CHECK CONSTRAINT` validation step behaves the
same way — or blocks concurrent DML while it scans — is **not assumed going in**. That's
exactly what `concurrent_writer.py`'s logged latency/error data during `contract.py`'s
SQL Server run is for. The module's write-up should report what was actually observed,
not what the docs claim.

## Files

- `expand.py --engine <name>` — creates `payment_methods`, adds nullable
  `payment_method_id`, creates a sync trigger (BEFORE INSERT/UPDATE on
  Postgres/MySQL, AFTER on SQL Server) so new writes during the migration
  self-populate, then batch-backfills existing rows. The trigger exists
  because the first real run of this module proved a one-shot backfill isn't
  enough: `concurrent_writer.py`'s inserts (which don't set
  `payment_method_id`, same as a real app pre-cutover) kept landing after
  their batch had already passed, growing the NULL count for as long as
  writes continued — not a data-quality problem, just a timing gap a trigger
  closes
- `concurrent_writer.py --engine <name> --duration <seconds> --log <path> [--write-mode method|fk]` —
  run in a separate terminal (or backgrounded with `&`) during `expand.py` and
  `contract.py`; alternates writes (INSERT a synthetic payment row) and reads
  (point `SELECT` + a `COUNT(*)`), logs per-operation latency and any errors
  to CSV. `--write-mode method` (default) simulates a pre-cutover app —
  writes the old text column, depends on the sync trigger. `--write-mode fk`
  simulates a post-cutover app — writes `payment_method_id` directly, and is
  what actually proves the final `DROP COLUMN method` step is safe (a
  `method`-mode writer will correctly and expectedly start erroring the
  instant that column is gone — that's the real cutover boundary, not a bug)
- `contract.py --engine <name> [--skip-drop]` — adds the FK (engine-specific
  technique above), makes `payment_method_id` NOT NULL, drops `method`.
  `--skip-drop` isolates the FK/NOT NULL steps from the drop, useful for
  testing each phase against the writer mode that actually matches it
- `validate.py --engine <name>` — diagnostic report: backfill completeness, FK
  presence/validation state, whether `method` is gone

## Results

### PostgreSQL — ✅ complete, fully verified with real concurrent-traffic data

2,549,851 total payments, 100% backfilled, FK present and `convalidated=t`,
`method` column gone. Every phase was tested with `concurrent_writer.py`
actually running concurrently (confirmed via matching wall-clock timestamps
between the writer's CSV log and each migration step's start/end, not
assumed from the docs) and produced no errors and no meaningful latency
elevation at any step:

| Step | Concurrent writer latency observed | Errors |
|---|---|---|
| Batched backfill (11 batches, ~144–159s, `method`-mode writer) | Normal baseline range; 2 isolated spikes traced to a *later*, unrelated autovacuum run, not the backfill itself | 0 |
| `ADD CONSTRAINT ... NOT VALID` + `VALIDATE CONSTRAINT` (`method`-mode writer) | 255–1294ms, no elevation vs. baseline | 0 |
| `ALTER ... SET NOT NULL` (`method`-mode writer) | 389–899ms, no elevation | 0 |
| `DROP COLUMN method` (`fk`-mode writer, i.e. already cut over) | Normal range | 0 |

### MySQL — ✅ complete, fully verified with real concurrent-traffic data

2,549,204 total payments, 100% backfilled, FK present, `method` column gone.
Same rigor as Postgres — every phase timestamp-verified against the writer's
CSV log — but the results are meaningfully different on almost every step,
not just slower:

| Step | Duration | Concurrent writer latency | Errors |
|---|---|---|---|
| Batched backfill (11 batches, ~206s, `method`-mode writer) | ~206s | Normal baseline range | 0 |
| Sync trigger creation | fast | — | — |
| `SET foreign_key_checks=0` + `ADD FOREIGN KEY ALGORITHM=INPLACE` (`method`-mode writer) | 86.10s | 11.5% spike rate (10/87 ops), up to 5,857ms, concentrated almost entirely in `scan_read` | 0 |
| `MODIFY payment_method_id ... NOT NULL` (writer overlap inferred from sequencing, not timestamp-verified — see caveat below) | 243.54s | 2.3% spike rate (10/427), up to 4,454ms, including 2 elevated writes | 0 |
| Relax `method` to nullable, pre-drop (`fk`-mode writer) | ~229s (bracketed via error timestamps, not a direct timed measurement on the run that mattered) | Every write errored for the full duration | 100% of writes during this window |
| `DROP COLUMN method` (`fk`-mode writer, post-relax) | 246.90s | 2 minor spikes only | 0 |

**Caveat on the NOT NULL row:** that specific test was run via a direct
`docker exec` ALTER rather than through `contract.py`'s timed wrapper, so
overlap is inferred from command sequencing (writer started, `sleep 10`, then
the ALTER fired while the writer's stated duration was still running) rather
than confirmed via matching timestamps the way every other row in this table
is. Treat it as good-but-not-fully-verified evidence, not equal footing with
the rest.

### SQL Server — ⏳ not yet run

The open question from the design phase — whether `WITH CHECK CHECK
CONSTRAINT` blocks concurrent DML the way Postgres's `VALIDATE CONSTRAINT`
doesn't — is still genuinely unverified.

## Notable findings

**A one-shot backfill isn't sufficient for a live table — proved empirically,
not assumed.** The first real Postgres run showed the NULL count climbing
from 64 to 320 while `concurrent_writer.py` kept running after `expand.py`'s
batched backfill finished: new INSERTs (simulating a pre-cutover app) kept
landing after their batch's fixed id range had already passed. The fix is a
sync trigger created *before* the backfill loop starts, so every new write
self-populates going forward — not a data-quality issue like `customer_notes`
had, purely a timing gap between a fixed-range backfill and an ongoing write
stream.

**The DB-side technique being non-blocking is necessary but not sufficient —
the write path itself has to have cut over before the old column can be
dropped.** Early attempts to test the drop step kept hard-failing every
writer op with `column "method" does not exist` — not because the drop
itself was unsafe, but because the writer was still (correctly, for that
test) targeting the column that had just been removed. Postgres confirmed
this cleanly: a writer already targeting `payment_method_id` (`--write-mode
fk`) got through the drop with 0 errors.

**MySQL's `ALGORITHM=INPLACE ADD FOREIGN KEY` does not validate inline — the
original module design was wrong about this, corrected via a real failed
run, not caught in advance.** It refuses to run at all with
`foreign_key_checks` ON (`ERROR 1846`), forcing a choice between
`ALGORITHM=COPY` (full rebuild) or `foreign_key_checks=OFF` (skip
validation entirely). The latter is actually a *more* extreme version of
Postgres's `NOT VALID` / SQL Server's `WITH NOCHECK` — except MySQL has no
built-in `VALIDATE CONSTRAINT` equivalent afterward. Getting the same
assurance requires writing and running that anti-join check yourself
(`verify_no_orphans_mysql` in `contract.py`).

**Neither adding nor removing `NOT NULL` is a metadata-only operation on
MySQL 5.7 InnoDB, in either direction — both are real, multi-minute data
rewrites.** `SET NOT NULL` measured 243.54s; relaxing `method` back to
nullable measured ~229s by the same mechanism. Postgres does both
essentially instantly (sub-1s). This directly undermines the "relax before
drop" fix on this specific engine/version: the fix is conceptually correct
(confirmed — once the relax completed, the following 246.9s drop ran with 0
errors against a fully cut-over writer) but the relax step itself isn't
cheap enough to make the transition window actually gap-free. A genuinely
gap-free cutover on MySQL 5.7 would need dual-writing both columns during
the transition rather than relying on constraint relaxation being fast (true
instant nullability changes are an 8.0.12+ feature, not available on 5.7).

**The FK-add's spikes on MySQL were concentrated almost entirely in
full-table scan reads, not point reads or writes** — 9 of 10 spikes during
the 86s FK-add were `scan_read`. Even with `foreign_key_checks=OFF` skipping
data validation, InnoDB still had to build the supporting index the FK
needs (confirmed via `SHOW INDEX`), and that index build competes for I/O
specifically with other full scans — the same underlying mechanism as
schema-drift's FK-index findings, just showing up as a latency pattern
instead of a lock.

## Status

🚧 In progress — PostgreSQL and MySQL complete and verified. SQL Server not
yet started.
