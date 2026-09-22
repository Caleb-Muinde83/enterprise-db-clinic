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
- `concurrent_writer.py --engine <name> --duration <seconds> --log <path>` — run in a
  separate terminal during `expand.py` and `contract.py`; alternates writes (INSERT a
  synthetic payment row against a real order) and reads (point `SELECT` + a `COUNT(*)`),
  logs per-operation latency and any errors to CSV
- `contract.py --engine <name>` — adds the FK (engine-specific technique above), makes
  `payment_method_id` NOT NULL, drops `method`
- `validate.py --engine <name>` — diagnostic report: backfill completeness, FK
  presence/validation state, whether `method` is gone

## Status

⏳ Not started. Scripts below are first drafts, not yet run against any container —
verify each step's actual output the same way every prior module did before trusting it
at scale.
