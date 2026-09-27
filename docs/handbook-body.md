## Module 1: Partitioning — the one that almost broke the whole project on day one

The starting point was `events`: 15 million rows, unpartitioned, every query
against it a full scan. Monthly range partitioning is the textbook fix, and
on Postgres it behaved like the textbook: 37 partitions, a 2.8x speedup, and
`EXPLAIN ANALYZE` confirming actual partition pruning rather than just
trusting that partitioning "should" help.

MySQL is where the project nearly stopped before it started. The same
migration, on the same data, stalled for over seven hours — not because the
approach was wrong, but because MySQL 5.7's default InnoDB buffer pool
(128MB) was never going to hold a working set this size. Bumping it to 1GB
turned a seven-hour stall into an 11.4x speedup. That's the first instance
of a pattern that would repeat for the rest of the project: the "bug" is
rarely the SQL. It's almost always a default that made sense for a toy
database and was never revisited.

SQL Server delivered the cleanest numbers of the three — up to 33.6x fewer
logical reads — and the whole migration survived something none of it was
designed for: the laptop it ran on went to sleep mid-migration. All three
containers came back via crash recovery with zero data loss, which is less
a finding about partitioning and more a reminder that "the migration
failed" and "the migration is still running, you just can't see it"
are different problems that look identical from a terminal that's gone
quiet.

## Module 2: Indexing — the point where "it feels faster" stopped being evidence

Wrong-order composite indexes, redundant indexes, indexes that were never
going to be used — the standard indexing-audit list. The interesting part
wasn't finding them, it was how quickly wall-clock timing turned out to be
useless for judging the fix. A query going from 4ms to 2ms *feels* like
progress and proves almost nothing at that scale — process overhead and
noise swamp the signal. The fix was to stop trusting the stopwatch and pull
mechanism-level evidence instead: Postgres's actual execution time from
`EXPLAIN ANALYZE`, MySQL's plan type and key columns, SQL Server's logical
read counts from `STATISTICS IO`. Once that discipline was in place, the
real numbers showed up — one Postgres query went from 57ms to 0.148ms
(386x) after a properly-scoped local index, and SQL Server dropped as much
as 14,352x in logical reads on another.

The other real finding was structural rather than numeric: MySQL's InnoDB
silently creates a supporting index for any foreign-key-constrained column,
and that index doesn't go away when the FK is dropped — it has to be
removed explicitly, and trying to drop it while the FK still exists throws
a flat refusal (Error 1553). Neither Postgres nor SQL Server does this.
It's a small thing until you're trying to reason about why a table has more
indexes than anyone remembers creating.

And a genuinely useful negative result: not every index helped. One
Postgres query got measurably *slower* at extreme selectivity after adding
an index — a real, worth-keeping counterexample to "just add an index" as
a default instinct.

**Production perspective.** Wall-clock timing is fine for confirming a
change didn't make things catastrophically worse; it's the wrong tool for
confirming an optimization actually worked, especially anywhere near
single-digit milliseconds. Pull the plan.

## Module 3: Schema drift — three real multi-hour stalls, and the fixes for each

This module simulated the kind of debt that actually accumulates: a
bolted-on `customer_notes` table with the wrong column type (`VARCHAR`
where it should have been `BIGINT`) and no foreign key, plus an
`orders.shipping_address` JSON blob that turned out to be 85% redundant —
3 million rows collapsing to 442,767 real addresses once extracted
properly. The fix, on all three engines: archive the genuinely bad rows
(7,493 malformed, 7,544 orphaned), retype and constrain what's left,
extract the address data into its own table.

The path there is where this module earns its place in the handbook. Three
separate multi-hour stalls, three different root causes, each one only
found by actually watching what was running instead of assuming the script
was just slow:

- **Re-parsing the same JSON five times per row, per batch.** The original
  backfill extracted five fields from the same JSON blob independently
  instead of parsing it once. On Postgres, that turned into a three-hour
  first run. The fix was a parse-once mapping table, joined against by ID
  instead of re-parsed on every batch.
- **An idempotency check that lied by omission.** `SELECT 1 FROM ... WHERE
  ...` returns *zero rows* when the thing being checked doesn't exist —
  not a parseable "false." String-matching for `"1"` in the output
  therefore silently gave the wrong answer whenever the check should have
  said no. The fix — `SELECT COUNT(*)`, which always returns exactly one
  row — is a small change that closes an entire category of "the script
  said it worked" false positives.
- **A non-sargable comparison that looked harmless.** Casting the *indexed*
  side of a join (`CAST(customers.customer_id AS TEXT) = notes.customer_id`)
  instead of the non-indexed side turned an indexed lookup into a full
  scan. MySQL 5.7 hit this at real cost — a 6+ hour stall before it was
  caught and killed. Postgres, on the *exact same query shape*, was fine:
  its planner built an efficient hash anti-join anyway, despite the cast.
  Same bug, two engines, two completely different outcomes — which is the
  clearest single illustration in this whole project of why "it works on
  Postgres" is not evidence that it works anywhere else.

SQL Server added its own, smaller footnote: a nonclustered index has a hard
1,700-byte key length cap, and the address columns' worst case reached
2,080 bytes. It only warned, never failed, because none of the real data
happened to hit that ceiling — but it's exactly the kind of constraint that
doesn't show up until a value finally does.

**Production perspective.** The sargability finding is the one worth
carrying into any engine, not just these three: wrapping an indexed column
in a function or a cast, on either side of a comparison, can silently
disable the index — and whether it actually does is genuinely
engine-dependent, confirmed by planner behavior, not assumable from the
SQL alone.

## Module 4: Zero-downtime migration — proving it, not asserting it

Modules 1–3 fixed things offline. This one asked a harder question:
extract `payments.method` into a proper `payment_methods` lookup table with
a foreign key, on a table taking real traffic, and *prove* nothing broke —
not by finishing without an error, but by running a concurrent
reader/writer against the table the entire time and checking its logged
latency against the migration's own timestamped steps, second by second.

Postgres came through clean: `NOT VALID` + `VALIDATE CONSTRAINT`, `SET NOT
NULL`, the eventual column drop — zero errors, no latency elevation,
confirmed against real overlapping timestamps rather than assumed from
documentation.

MySQL surprised in three separate ways. `ALGORITHM=INPLACE ADD FOREIGN
KEY` flatly refuses to run with `foreign_key_checks` on — the actual fast
path is turning validation off entirely, which is a *more* aggressive
version of Postgres's "add unvalidated" step, except MySQL has no built-in
follow-up validation command at all; getting the same assurance meant
writing a manual anti-join check by hand. Then: neither adding nor
removing `NOT NULL` is a metadata-only operation on MySQL 5.7's InnoDB —
both measured as genuine multi-minute table rewrites (243 and roughly 229
seconds), against Postgres's sub-second cost in both directions. And the
FK-add's latency spikes weren't random — nine of ten landed on full-table
scan reads specifically, which points at InnoDB still having to build the
FK's supporting index even with validation turned off (architectural
inference from the spike pattern, not a directly observed index-build
event — but a `SHOW INDEX` confirmed the resulting index existed, and the
timing lines up with nothing else in that window).

SQL Server resolved the module's original open question directly: `WITH
CHECK CHECK CONSTRAINT` is confirmed non-blocking, matching Postgres. But
relaxing the old column's `NOT NULL` constraint blocked one concurrent read
for roughly 31 seconds — a genuine table-level lock, and a different
failure shape than MySQL's write-validation errors: MySQL rejected writes
outright; SQL Server just made an unrelated read wait.

The finding that ties the whole module together is one no single engine's
documentation would have surfaced: a one-shot backfill isn't enough for a
table taking live writes — the fix was a database trigger, added only
after a real run showed the NULL count *growing* instead of shrinking,
because new rows kept arriving faster than a fixed-range batch job could
catch them. And dropping the old column is only safe once every writer has
actually cut over *and* the old column's own constraints have been
relaxed first — discovered because a writer that had already switched to
the new column still failed, for the entire drop, on a leftover `NOT NULL`
requirement on the column it was ignoring.

**Production perspective.** "Zero-downtime" is a claim about concurrent
traffic, not about a migration script exiting 0. The only way to know
whether it's true is to have something else hitting the table the whole
time and check its logs against the migration's own timestamps — anything
short of that is an assumption wearing the language of a guarantee.

## Module 5: Deprecated version migration — moving three major versions on three engines

Postgres 11→16, MySQL 5.7→8.0, SQL Server 2017→2022, each carrying every
fix the first four modules had already built. Roughly 29 million rows,
exact match on every table, on every engine — but getting there wasn't
uniform at all.

Postgres was clean on the first attempt: `pg_dump` piped straight into
`psql`, 982 seconds, done.

MySQL's first attempt *failed silently* — the transfer died partway through
the 15-million-row `events` table, but the receiving process just hit EOF
on the truncated input and exited cleanly, so the pipe reported success on
a genuinely broken migration. The real cause traced to three separate
things at once: the destination's default `innodb_redo_log_capacity`
couldn't keep pace with the write rate, the source's `net_write_timeout`
eventually gave up waiting on the backed-up pipe, and the migration user
was missing the `PROCESS` privilege `mysqldump` wanted. Fixing the
false-success problem itself meant rewriting the transfer to drive both
processes directly rather than trust a shell pipe's exit code, which only
ever reflects the *last* command in the chain.

SQL Server needed its own real fixes: the 2022 container image renamed
`mssql-tools` to `mssql-tools18` outright, that newer client enforces TLS
by default, and a freshly-started container transiently refuses logins
while it finishes booting.

The capstone check — re-running one real verification from each of modules
1 through 4 against every migrated instance — came back clean everywhere:
partitions still pruned, every fix index was still in place, every foreign
key was still validated. That's the actual point of running this module
last: it's the only piece of evidence that version migration didn't
quietly undo the rest of the project.

The six breaking-behavior probes produced two results meaningfully
different from what the module assumed going in. On Postgres, the `clinic`
user could still create tables in the `public` schema after migrating to
16 — but only because it happens to be a superuser in this environment,
which sidesteps the check entirely. The real evidence was in the schema's
raw ACL: Postgres 11 had granted `PUBLIC` both usage and create on that
schema; the restored Postgres 16 instance showed usage only, narrowed to a
new `pg_database_owner` role. PG15's tightened default had genuinely taken
effect — the dump/restore didn't carry the old permissive grant forward,
and a real non-superuser role would have been blocked exactly where the
old one wasn't. On SQL Server, deliberately running the classic deprecated
`*=` outer join to exercise a usage counter hit `Msg 102: incorrect syntax`
— a parser-level error, not the semantic compatibility-level rejection
expected. SQL Server 2022 hasn't just deprecated that syntax, it's deleted
the grammar entirely, at any compatibility level.

## Module 6: Cross-engine comparison — what Oracle and MongoDB do that Postgres, MySQL, and SQL Server don't

The first five modules built and tested three engines by hand. This one
asked a different question of two more, Oracle and MongoDB, without
building either locally: how much of what this project had to hand-roll is
a genuinely universal database problem, and how much of it is a gap
specific to the three engines this project happened to pick?

The answer, checked against current documentation rather than assumed: a
meaningful amount of it is a gap. Oracle ships `DBMS_REDEFINITION` as a
supported package that does almost exactly what module 3's schema
extraction and module 4's expand/contract migration had to build by
hand — create an interim table, synchronize live changes, cut over. Its
Edition-Based Redefinition takes the same idea to the application-code
layer: old and new versions of PL/SQL objects coexist until every session
still on the old edition has finished, which is precisely the
"don't force a hard cutover" lesson module 4 learned the hard way when
dropping `payments.method` broke any writer that hadn't yet switched to
`payment_method_id`. Oracle's interval partitioning auto-creates new range
partitions on insert, with no scheduled job required — something none of
Postgres, MySQL, or SQL Server do natively. And Oracle's AutoUpgrade tool
runs deprecated-feature prechecks *before* an upgrade starts, which is the
exact inverse of module 5's approach: probing for breakage only after the
data had already moved, because none of the three flagship engines ship an
equivalent single-tool precheck for a multi-major-version jump.

MongoDB reframes the problems rather than solving them the same way.
Sharding is its answer to scale, but it's solving a different problem than
partitioning was — horizontal write/read distribution across nodes, not
single-node query pruning — and treating the two as interchangeable is a
common, avoidable mistake. Flexible schema means module 3's core failure
mode (a bolted-on column with the wrong type and no constraint) mostly
doesn't arise as a *schema* problem at all — but MongoDB's own
documentation is explicit that turning on a JSON Schema validator never
retroactively migrates documents already in the collection. The
coexistence problem doesn't disappear, it just relocates into application
code: tag documents with a schema version, migrate incrementally on read.
And MongoDB's own version-upgrade model turned out to be the closest
analog to module 4's territory of anything in this comparison — a rolling
binary swap across replica set members, available the entire time — except
it comes with a constraint none of this project's own version jumps had:
major versions must be upgraded one at a time, in strict sequence, never
skipped. It also has its own version of the same two-phase pattern this
project kept re-discovering on Postgres and SQL Server: binaries upgrade
first, and any new backwards-incompatible behavior stays dormant until a
separate, deliberate `featureCompatibilityVersion` bump — the same
principle, at the scale of an entire deployment instead of one constraint.

That's the real conclusion of this module, and arguably of the whole
project: the patterns built here by hand — sync triggers, expand/contract,
relax-before-drop, parse-once mapping tables — aren't universal truths
about databases. They're workarounds for capabilities specific engines
either ship natively or make unnecessary by design. The underlying
problem — how do you let two versions of something coexist long enough to
cut over safely — doesn't go away. It just moves to whichever layer
doesn't already have a built-in answer for it.

## The part that isn't a database finding

Twice, the tooling built to *verify* something in this project made the
exact mistake the project existed to catch.

Module 3 documented, explicitly, that `SELECT 1` returns zero rows rather
than a parseable false — and fixed it. Module 5's own capstone-validation
script, written independently afterward to re-check that module's own
findings, checked whether a column still existed by looking for raw text
in a query's output instead of counting rows — and got the exact same
class of false positive, on the exact same kind of check, for the exact
reason the earlier fix already existed to prevent.

Separately, a script meant to prove Postgres's schema-privilege change had
survived a migration tested it by asking the migration's own database user
whether it could create a table — without checking, first, that the user
in question was a superuser, and therefore immune to the very permission
being tested. The result looked reassuring and proved nothing.

Neither of those is a database finding. They're a finding about verification
itself: the discipline of checking real evidence instead of trusting that
something worked doesn't get easier to apply consistently just because
you've already learned the lesson once, on a different script, in a
different module. It has to be re-applied every time, including to the
tools built to enforce it — which is either the least comfortable thing in
this whole project to admit, or the strongest evidence that the discipline
was worth having at all.
