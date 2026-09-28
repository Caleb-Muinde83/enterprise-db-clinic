## Module 1: Partitioning nearly broke the whole project on day one

The starting point was `events`: 15 million rows, unpartitioned, every query
against it a full scan. Monthly range partitioning is the textbook fix, and
on Postgres it behaved like the textbook: 37 partitions, a 2.8x speedup, and
`EXPLAIN ANALYZE` confirming actual partition pruning rather than just
trusting that partitioning "should" help.

**Beginner note.** A table is a named set of records, and each record is a
row. A partition is one piece of a larger table. Here, the `events` table is
split into monthly ranges by `event_time`; when a query asks for August,
partition pruning means the database skips the other months. `EXPLAIN
ANALYZE` runs a query and reports the steps the database actually used, so
the project could confirm that it read the August partition instead of
assuming partitioning helped.

MySQL is where the project nearly stopped before it started. The same
migration, on the same data, stalled for over seven hours. The approach
was not wrong; MySQL 5.7's default InnoDB buffer pool
(128MB) was never going to hold a working set this size. Bumping it to 1GB
turned a seven-hour stall into an 11.4x speedup. That's the first instance
of a pattern that would repeat for the rest of the project: the "bug" is
rarely the SQL. It's almost always a default that made sense for a toy
database and was never revisited.

**Beginner note.** InnoDB is the storage engine MySQL uses to manage table
data and indexes. Its buffer pool is memory for keeping frequently needed
database pages ready. The working set is the data an operation needs to use
repeatedly; this partitioning task needed more than MySQL's default 128 MB
buffer pool could hold, so raising it to 1 GB helped.

SQL Server delivered the cleanest numbers of the three, with up to 33.6x
fewer logical reads. The whole migration also survived an unexpected event:
the laptop it ran on went to sleep mid-migration. All three
containers came back via crash recovery with zero data loss, which is less
a finding about partitioning and more a reminder that "the migration
failed" and "the migration is still running, you just can't see it"
are different problems that look identical from a terminal that's gone
quiet.

**Beginner note.** The databases ran inside Docker containers, isolated
environments such as `clinic_pg_old`. Crash recovery is how a database
restores a consistent state after an unexpected stop. Logical reads count
database pages examined by a query; SQL Server's lower logical-read count
showed that it examined less data after partitioning.

## Module 2: Indexing is where "it feels faster" stopped being evidence

Wrong-order composite indexes, redundant indexes, and indexes that were
never going to be used are standard items on an indexing audit list. The interesting part
wasn't finding them, it was how quickly wall-clock timing turned out to be
useless for judging the fix. A query going from 4ms to 2ms *feels* like
progress and proves almost nothing at that scale because process overhead and
noise swamp the signal. The fix was to stop trusting the stopwatch and pull
mechanism-level evidence instead: Postgres's actual execution time from
`EXPLAIN ANALYZE`, MySQL's plan type and key columns, SQL Server's logical
read counts from `STATISTICS IO`. Once that discipline was in place, the
real numbers showed up: one Postgres query went from 57ms to 0.148ms
(386x) after a properly-scoped local index, and SQL Server dropped as much
as 14,352x in logical reads on another.

**Beginner note.** An index is an extra lookup structure that helps the
database find rows without checking every row. A composite index contains
more than one column, and its column order matters. Selectivity describes
how many rows a condition matches: finding one order is more selective than
finding thousands of events. A query plan lists the steps the database
chooses. The project compared those plans and database-measured execution
time, rather than relying only on wall-clock time, which also includes
command startup and communication overhead.

The other real finding was structural rather than numeric: MySQL's InnoDB
silently creates a supporting index for any foreign-key-constrained column,
and that index doesn't go away when the FK is dropped. It has to be
removed explicitly, and trying to drop it while the FK still exists throws
a flat refusal (Error 1553). Neither Postgres nor SQL Server does this.
It's a small thing until you're trying to reason about why a table has more
indexes than anyone remembers creating.

**Beginner note.** A foreign key is a rule that requires a value in one
table to refer to a real row in another table. The supporting index helps
the database check that relationship efficiently. MySQL's InnoDB created
such an index for a foreign-key column in this project; dropping the
foreign-key rule did not automatically remove the index.

And a genuinely useful negative result: not every index helped. One
Postgres query got measurably *slower* at extreme selectivity after adding
an index. This is a real, worth-keeping counterexample to "just add an
index" as a default instinct.

**Production perspective.** Wall-clock timing is fine for confirming a
change didn't make things catastrophically worse; it's the wrong tool for
confirming an optimization actually worked, especially anywhere near
single-digit milliseconds. Pull the plan.

## Module 3: Schema drift caused three real multi-hour stalls, and how each was fixed

This module simulated the kind of debt that actually accumulates: a
bolted-on `customer_notes` table with the wrong column type (`VARCHAR`
where it should have been `BIGINT`) and no foreign key, plus an
`orders.shipping_address` JSON blob that turned out to be 85% redundant.
Proper extraction reduced 3 million rows to 442,767 real addresses. The
fix, on all three engines: archive the genuinely bad rows
(7,493 malformed, 7,544 orphaned), retype and constrain what's left,
extract the address data into its own table.

**Beginner note.** A schema is the database's structure: tables, columns,
data types, and rules. `VARCHAR` holds text; `BIGINT` holds large whole
numbers. Here, `customer_notes.customer_id` has the wrong type and no
foreign key, which is a rule linking its value to a real `customers` row.
An orphaned note refers to a customer that does not exist; malformed data
does not have the expected form. The repair archives those rows before
cleaning the active table. `orders.shipping_address` is JSON, structured
text containing address details. Since the same addresses repeat across
millions of orders, storing each distinct address in a separate lookup
table reduces duplication. This is called normalization; the original
repeated data is denormalized.

The path there is where this module earns its place in the handbook. Three
separate multi-hour stalls, three different root causes, each one only
found by actually watching what was running instead of assuming the script
was just slow:

- **Re-parsing the same JSON five times per row, per batch.** The original
  backfill extracted five fields from the same JSON blob independently
  instead of parsing it once. On Postgres, that turned into a three-hour
  first run. The fix was a parse-once mapping table, joined against by ID
  instead of re-parsed on every batch.

  **Beginner note.** A backfill fills new or corrected fields using data
  already in the database. A batch is a limited group of rows handled at
  once, rather than the whole table in one operation. The mapping table
  stores the extracted address values by order ID, so each JSON address is
  parsed once and then reused by later batches.
- **An idempotency check that lied by omission.** `SELECT 1 FROM ... WHERE
  ...` returns *zero rows* when the thing being checked doesn't exist,
  not a parseable "false." String-matching for `"1"` in the output
  therefore silently gave the wrong answer whenever the check should have
  said no. The fix was `SELECT COUNT(*)`, which always returns exactly one
  row. This small change closes an entire category of "the script
  said it worked" false positives.

  **Beginner note.** An idempotent migration step is safe to run again
  without doing the work twice or damaging data. `SELECT 1` returns a row
  only when it finds a match, while `COUNT(*)` always returns one result
  containing the number of matches, even when that number is zero. The
  project uses the count to make the existence check clear.
- **A non-sargable comparison that looked harmless.** Casting the *indexed*
  side of a join (`CAST(customers.customer_id AS TEXT) = notes.customer_id`)
  instead of the non-indexed side turned an indexed lookup into a full
  scan. MySQL 5.7 hit this at real cost, with a 6+ hour stall before it was
  caught and killed. Postgres, on the *exact same query shape*, was fine:
  its planner built an efficient hash anti-join anyway, despite the cast.
  The same bug produced completely different outcomes on the two engines.
  This is the clearest single illustration in this whole project of why "it works on
  Postgres" is not evidence that it works anywhere else.

  **Beginner note.** A cast converts a value to a different data type. A
  condition is sargable when the database can use an index to search for
  matches; casting the indexed customer-ID column stopped MySQL from using
  that lookup efficiently. An anti-join finds rows in one table with no
  match in another, such as notes with no matching customer. Postgres chose
  a hash anti-join, which builds an in-memory lookup of matching values.

SQL Server added its own, smaller footnote: a nonclustered index has a hard
1,700-byte key length cap, and the address columns' worst case reached
2,080 bytes. It only warned, never failed, because none of the real data
happened to hit that ceiling, but it's exactly the kind of constraint that
doesn't show up until a value finally does.

**Beginner note.** A nonclustered index is a separate SQL Server lookup
structure that points back to table rows. The key length is the maximum
size of the indexed values. Possible address values totaled 2,080 bytes,
above SQL Server's 1,700-byte limit, but actual data stayed below the limit,
so the engine warned instead of failing.

**Production perspective.** The sargability finding is the one worth
carrying into any engine, not just these three: wrapping an indexed column
in a function or a cast, on either side of a comparison, can silently
disable the index. Whether it actually does is genuinely
engine-dependent, confirmed by planner behavior, not assumable from the
SQL alone.

## Module 4: Zero-downtime migration, proven rather than asserted

Modules 1–3 fixed things offline. This one asked a harder question:
extract `payments.method` into a proper `payment_methods` lookup table with
a foreign key, on a table taking real traffic, and *prove* nothing broke.
Finishing without an error is not enough; the proof comes from running a concurrent
reader/writer against the table the entire time, then checking its logged
latency against the migration's own timestamped steps, second by second.

**Beginner note.** A migration changes the database while an application
may still be using it. Concurrency means those operations happen at the
same time; latency is how long an individual read or write takes. Zero
downtime means users continue to get acceptable service during the change.
The project tests this by keeping a reader/writer active, recording its
response times and errors, and comparing them with the migration timeline.

Postgres came through clean: `NOT VALID` + `VALIDATE CONSTRAINT`, `SET NOT
NULL`, and the eventual column drop. There were zero errors and no latency elevation,
confirmed against real overlapping timestamps rather than assumed from
documentation.

**Beginner note.** A constraint is a rule the database enforces, such as a
foreign key or `NOT NULL`. `NOT VALID` lets PostgreSQL add a foreign key
without first checking every old row; new writes are still checked. A later
`VALIDATE CONSTRAINT` checks the existing rows. `SET NOT NULL` changes the
column rule so every payment must have a method ID. The project separates
these steps to measure their effect on the active writer.

MySQL surprised in three separate ways. `ALGORITHM=INPLACE ADD FOREIGN
KEY` flatly refuses to run with `foreign_key_checks` on. The actual fast
path turns validation off entirely, which is a *more* aggressive
version of Postgres's "add unvalidated" step. MySQL has no built-in
follow-up validation command, so getting the same assurance meant
writing a manual anti-join check by hand. Then: neither adding nor
removing `NOT NULL` is a metadata-only operation on MySQL 5.7's InnoDB.
Both changes measured as genuine multi-minute table rewrites (243 and roughly 229
seconds), against Postgres's sub-second cost in both directions. And the
FK-add's latency spikes weren't random: nine of ten landed on full-table
scan reads specifically, which points at InnoDB still having to build the
FK's supporting index even with validation turned off (architectural
inference from the spike pattern, not a directly observed index-build
event, but a `SHOW INDEX` confirmed the resulting index existed, and the
timing lines up with nothing else in that window).

**Beginner note.** `ALGORITHM=INPLACE` asks MySQL to change a table without
copying the whole table to a new one. `foreign_key_checks` controls whether
MySQL checks foreign-key references during the operation. This run had to
turn checks off, so the script separately searched for orphan rows with an
anti-join. A table rewrite rebuilds or copies table data to apply a change;
on MySQL 5.7, changing `NOT NULL` in either direction took several minutes.

SQL Server resolved the module's original open question directly: `WITH
CHECK CHECK CONSTRAINT` is confirmed non-blocking, matching Postgres. But
relaxing the old column's `NOT NULL` constraint blocked one concurrent read
for roughly 31 seconds, a genuine table-level lock and a different
failure shape than MySQL's write-validation errors: MySQL rejected writes
outright; SQL Server just made an unrelated read wait.

**Beginner note.** In SQL Server, `WITH NOCHECK` can add a foreign key
without checking all existing rows first, and `WITH CHECK CHECK CONSTRAINT`
checks and trusts it afterward. A lock is the database's way of preventing
conflicting changes while an operation runs. Here, a concurrent read waited
about 31 seconds while the old column's nullability changed.

The finding that ties the whole module together is one no single engine's
documentation would have surfaced: a one-shot backfill isn't enough for a
table taking live writes, so the fix was a database trigger. It was added only
after a real run showed the NULL count *growing* instead of shrinking,
because new rows kept arriving faster than a fixed-range batch job could
catch them. And dropping the old column is only safe once every writer has
actually cut over *and* the old column's own constraints have been
relaxed first. This was discovered because a writer that had already switched to
the new column still failed, for the entire drop, on a leftover `NOT NULL`
requirement on the column it was ignoring.

**Beginner note.** A trigger is database code that runs automatically when
rows are inserted or updated. The project uses one to copy the old
`payments.method` value into `payment_method_id` while older writers still
send only `method`. Expand and contract is the broader staged approach:
first add the new structure, then move data and application writes over,
and only after that remove the old structure. The project keeps both
columns during the transition so old and new writers can coexist.

**Production perspective.** "Zero-downtime" is a claim about concurrent
traffic, not about a migration script exiting 0. The only way to know
whether it's true is to have something else hitting the table the whole
time and check its logs against the migration's own timestamps. Anything
short of that is an assumption wearing the language of a guarantee.

## Module 5: Deprecated version migration across three major versions and engines

Postgres 11→16, MySQL 5.7→8.0, SQL Server 2017→2022, each carrying every
fix the first four modules had already built. Roughly 29 million rows,
exact match on every table, on every engine, but getting there wasn't
uniform.

**Beginner note.** A major version is a significant new release of a
database engine, which can change behavior as well as add features. This
project upgrades PostgreSQL 11 to 16, MySQL 5.7 to 8.0, and SQL Server
2017 to 2022. A dump-and-restore exports a database from the old server
and loads it into a new one; the project checks row counts afterward to
confirm that data arrived intact.

Postgres was clean on the first attempt: `pg_dump` piped straight into
`psql`, 982 seconds, done.

MySQL's first attempt *failed silently*: the transfer died partway through
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

**Beginner note.** `pg_dump` and `mysqldump` are command-line tools that
export database contents; `psql` and `mysql` can load SQL into a server. A
pipe (`|`) sends one command's output directly to another command. EOF
means “end of file,” or that no more input is arriving. In this failed run,
the restore saw EOF before the full dump arrived. Its zero exit code meant
that command thought it had succeeded, even though the export had failed;
the project changed the script to check both commands instead of trusting
only the last command's exit code.

**Beginner note.** The redo log records database changes so InnoDB can
recover them after a failure. `innodb_redo_log_capacity` sets how much space
is available for those records; the destination could not keep up with the
incoming writes. A timeout is a maximum wait before a connection gives up:
`net_write_timeout` expired while the source waited to send more data.
`PROCESS` is a MySQL permission that `mysqldump` needed from the migration
user. A privilege is simply permission to perform a database action.

SQL Server needed its own real fixes: the 2022 container image renamed
`mssql-tools` to `mssql-tools18` outright, that newer client enforces TLS
by default, and a freshly-started container transiently refuses logins
while it finishes booting.

**Beginner note.** TLS (Transport Layer Security) encrypts network traffic
between a database client and server. The newer SQL Server command-line
tools required TLS by default, so the migration had to account for that
connection requirement as well as the renamed `mssql-tools18` client.

The capstone check re-ran one real verification from each of modules
1 through 4 against every migrated instance. It came back clean everywhere:
partitions still pruned, every fix index was still in place, every foreign
key was still validated. That's the actual point of running this module
last: it's the only piece of evidence that version migration didn't
quietly undo the rest of the project.

**Beginner note.** Capstone validation is a final set of checks after a
large change. Here, the project rechecked representative results from
partitioning, indexing, schema drift, and zero-downtime migration on each
upgraded database. This catches problems that a matching row count alone
would miss.

The six breaking-behavior probes produced two results meaningfully
different from what the module assumed going in. On Postgres, the `clinic`
user could still create tables in the `public` schema after migrating to
16, but only because it happens to be a superuser in this environment,
which sidesteps the check entirely. The real evidence was in the schema's
raw ACL: Postgres 11 had granted `PUBLIC` both usage and create on that
schema; the restored Postgres 16 instance showed usage only, narrowed to a
new `pg_database_owner` role. PG15's tightened default had genuinely taken
effect. The dump/restore didn't carry the old permissive grant forward,
and a real non-superuser role would have been blocked exactly where the
old one wasn't. On SQL Server, deliberately running the classic deprecated
`*=` outer join to exercise a usage counter hit `Msg 102: incorrect syntax`,
which is a parser-level error rather than the semantic compatibility-level
rejection expected. SQL Server 2022 hasn't just deprecated that syntax; it deleted
the grammar entirely, at any compatibility level.

**Beginner note.** A role is a database identity or permission group. A
superuser is an administrator with unusually broad powers, so testing with
the project's `clinic` superuser did not show what an ordinary account could
do. An ACL (Access Control List) records who may perform which actions.
PostgreSQL's `PUBLIC` role means all users; `pg_database_owner` represents
the owner of the current database. The probe compared their permissions on
the `public` schema, a namespace that groups database objects.

The `*=` syntax is SQL Server's old way to write an outer join, which
returns matching rows and can also keep unmatched rows from one side. A
parser error means the database could not understand the SQL grammar at
all: SQL Server 2022 rejected `*=` before it could run. A compatibility
level lets a newer SQL Server preserve some older behavior for a database,
but it cannot restore syntax removed from the parser.

## Module 6: How Oracle and MongoDB differ from Postgres, MySQL, and SQL Server

The first five modules built and tested three engines by hand. This one
asked a different question of two more, Oracle and MongoDB, without
building either locally: how much of what this project had to hand-roll is
a genuinely universal database problem, and how much of it is a gap
specific to the three engines this project happened to pick?

The answer, checked against current documentation rather than assumed: a
meaningful amount of it is a gap. Oracle ships `DBMS_REDEFINITION` as a
supported package that does almost exactly what module 3's schema
extraction and module 4's expand/contract migration had to build by
creating an interim table, synchronizing live changes, and cutting over. Its
Edition-Based Redefinition takes the same idea to the application-code
layer: old and new versions of PL/SQL objects coexist until every session
still on the old edition has finished, which is precisely the
"don't force a hard cutover" lesson module 4 learned the hard way when
dropping `payments.method` broke any writer that hadn't yet switched to
`payment_method_id`. Oracle's interval partitioning auto-creates new range
partitions on insert, with no scheduled job required. None of Postgres,
MySQL, or SQL Server do this natively. Oracle's AutoUpgrade tool
runs deprecated-feature prechecks *before* an upgrade starts, which is the
exact inverse of module 5's approach: probing for breakage only after the
data had already moved, because none of the three flagship engines ship an
equivalent single-tool precheck for a multi-major-version jump.

**Beginner note.** `DBMS_REDEFINITION` is an Oracle package, meaning a set
of database-provided procedures, for replacing a table's structure while
keeping it available. An interim table is a temporary new version used
during that change. Edition-Based Redefinition (EBR) lets old and new
versions of Oracle application code coexist; PL/SQL is Oracle's language
for code stored in the database. Interval partitioning creates new date
or number ranges automatically when needed. AutoUpgrade checks for known
upgrade problems before the upgrade begins. These features provide
database-managed versions of steps this project built and tested itself.

MongoDB reframes the problems rather than solving them the same way.
Sharding is its answer to scale, but it solves a different problem from
partitioning: it distributes reads and writes across nodes rather than
pruning queries on a single node. Treating the two as interchangeable is a
common, avoidable mistake. Flexible schema means module 3's core failure
mode (a bolted-on column with the wrong type and no constraint) mostly
doesn't arise as a *schema* problem at all. MongoDB's own documentation
is explicit that turning on a JSON Schema validator never
retroactively migrates documents already in the collection. The
coexistence problem doesn't disappear, it just relocates into application
code: tag documents with a schema version, migrate incrementally on read.
And MongoDB's own version-upgrade model turned out to be the closest
analog to module 4's territory in this comparison: a rolling binary swap
across replica set members that keeps the deployment available. It comes
with a constraint none of this project's own version jumps had:
major versions must be upgraded one at a time, in strict sequence, never
skipped. It also has its own version of the same two-phase pattern this
project kept re-discovering on Postgres and SQL Server: binaries upgrade
first, and any new backwards-incompatible behavior stays dormant until a
separate, deliberate `featureCompatibilityVersion` bump. This applies the
same principle to an entire deployment instead of one constraint.

**Beginner note.** MongoDB stores records as documents and groups them in
collections, roughly like rows and tables in a relational database. Its
flexible schema allows documents in one collection to have different
fields, but a JSON Schema validator only checks documents when they are
written; it does not repair old documents. Sharding distributes a
collection across machines for scale, whereas the project's table
partitioning helps one database skip irrelevant date ranges. A replica
set is a group of MongoDB servers that keep copies of the same data. A
rolling upgrade updates its members one at a time. The
`featureCompatibilityVersion` setting controls which version-specific
features are enabled after the software binaries are upgraded, so the
project comparison treats that setting as a separate, deliberate step.

That's the real conclusion of this module, and arguably of the whole
project: the patterns built here by hand, including sync triggers,
expand/contract, relax-before-drop, and parse-once mapping tables, aren't
universal truths about databases. They're workarounds for capabilities that
specific engines either ship natively or make unnecessary by design. The underlying
problem is how to let two versions of something coexist long enough to
cut over safely. That problem doesn't go away. It just moves to whichever layer
doesn't already have a built-in answer for it.

## The part that isn't a database finding

Twice, the tooling built to *verify* something in this project made the
exact mistake the project existed to catch.

Module 3 documented, explicitly, that `SELECT 1` returns zero rows rather
than a parseable false, and fixed it. Module 5's own capstone-validation
script, written independently afterward to re-check that module's own
findings, checked whether a column still existed by looking for raw text
in a query's output instead of counting rows, and got the exact same
class of false positive, on the exact same kind of check, for the exact
reason the earlier fix already existed to prevent.

Separately, a script meant to prove Postgres's schema-privilege change had
survived a migration tested it by asking the migration's own database user
whether it could create a table without first checking whether the user
in question was a superuser and therefore immune to the very permission
being tested. The result looked reassuring and proved nothing.

Neither of those is a database finding. They're a finding about verification
itself: the discipline of checking real evidence instead of trusting that
something worked doesn't get easier to apply consistently just because
you've already learned the lesson once, on a different script, in a
different module. It has to be re-applied every time, including to the
tools built to enforce it. This is either the least comfortable thing in
this whole project to admit or the strongest evidence that the discipline
was worth having at all.

