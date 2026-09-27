# Module 6: Cross-engine comparison (Oracle, MongoDB)

Write-up only, by design: no local Oracle or MongoDB build. The point of
this module isn't to repeat modules 1-5's hands-on rigor on two more
engines; it's to take the five real problems this project actually solved
on Postgres/MySQL/SQL Server and ask a sharper question for each: how does
Oracle handle this, and how does MongoDB's fundamentally different data
model change (or dissolve) the problem entirely? Every claim below is
checked against current documentation, not written from memory: same
discipline as the rest of the project, applied to research instead of a
live container.

## 1. Partitioning

Oracle's closest equivalent isn't just "the same feature": it's a
meaningfully more automated version of what modules 1 and 3 required manual
scripting for. **Interval partitioning** lets Oracle auto-create new range
partitions the moment data arrives past the last defined boundary, with no
scheduled job or cron-driven "create next month's partition" script
required at all. None of the three flagship engines in this project have a
true equivalent: Postgres, MySQL, and SQL Server all require some form of
explicit partition creation ahead of time (a maintenance script, an
extension like `pg_partman`, or a scheduled job), which is exactly the kind
of operational surface area this project had to build and test by hand.

MongoDB's answer to "how do I handle a huge table" isn't partitioning at
all: it's **sharding**, and conflating the two is a common mistake worth
being precise about. Sharding distributes a *collection* across multiple
physical nodes for horizontal write/read scale-out; it's chosen for
capacity and throughput, not for pruning a single node's query plan the way
this project's monthly `events` partitions were. A sharded MongoDB
deployment and a partitioned Postgres table are solving genuinely different
problems that happen to share the word "partition" in casual conversation.

## 2. Indexing

Oracle has a genuinely distinctive capability with no equivalent among the
three flagship engines: **invisible indexes**. An index can be marked
invisible to the optimizer without being dropped: meaning "what happens if
we remove this index" can be tested risk-free, instantly reversible, rather
than the drop/measure/recreate-if-wrong cycle this project's indexing
module actually had to use. Given how many of this project's own findings
depended on carefully re-verifying before/after state across re-runs
(module 2's own README documents needing multiple redo cycles after full
suite reruns clobbered each other), a native invisible-index toggle would
have removed an entire category of risk from that process.

MongoDB indexing is conceptually close to what modules 2 already did:
compound indexes, background builds so index creation doesn't block
reads/writes, but the *decision* of what to index is shaped differently by
the document model: a compound index's field order still matters
(same lesson as this project's wrong-order composite index fixes), but
there's no separate "missing FK-supporting index" failure mode the way
modules 2 and 3 hit repeatedly on MySQL, because MongoDB has no enforced
foreign keys to support in the first place.

## 3. Schema drift

This is where the comparison gets genuinely interesting rather than just
"same problem, different syntax."

**Oracle has a built-in, first-class tool for almost exactly module 3's
manual process.** `DBMS_REDEFINITION` performs an online table
redefinition: create an interim table, sync ongoing changes, then swap:
which is *precisely* the shadow-table-and-cutover pattern this project's
`schema-drift` and `zero-downtime` modules had to hand-build: a nullable
new column, a sync mechanism for concurrent writes (this project's own
answer was a database trigger, discovered only after a real failed run
showed the gap), a batched backfill, and a final swap. Oracle ships this as
a supported package with documented restrictions, rather than something a
team writes and re-derives from a live failure the way this project did.

**MongoDB doesn't have this problem in the same shape at all.** Its
flexible-schema model means two documents in the same collection can simply
have different fields with no `ALTER TABLE`-equivalent operation required:
the wrong-type, orphaned-reference mess module 3 simulated (a bolted-on
`customer_notes` table with a `VARCHAR` id and no FK) doesn't arise as a
*schema* problem in MongoDB, because there's no schema being enforced
unless you opt into one via a JSON Schema validator. But that flexibility
has a real, documented cost of its own: turning on validation rules does
**not** retroactively migrate existing documents: MongoDB's own
documentation is explicit that changing validation rules never touches
data already in the collection, so a real migration still has to happen,
just relocated into application code (commonly: tag documents with a
schema version, migrate incrementally as each is read or via a background
job) rather than a single batched SQL statement. Different shape, same
underlying problem: "old and new data coexisting during a transition":
just moved from the database layer into the app layer.

## 4. Zero-downtime migration

Oracle again has purpose-built tooling directly comparable to module 4's
hand-rolled expand/contract approach: `DBMS_REDEFINITION` covers the
data-structure side (same package as above), and **Edition-Based
Redefinition (EBR)**: genuinely unique to Oracle among these five engines
: covers the *code* side: multiple versions of PL/SQL packages, views, and
other editionable objects can coexist in the same database, so existing
sessions keep running against the old edition while new sessions pick up
the new one, with an explicit, deliberate cutover once nothing's left on
the old edition. That's conceptually the same "don't force a hard cutover,
let both versions coexist during a transition" lesson module 4 arrived at
empirically (the finding that dropping `payments.method` outright broke any
writer that hadn't yet cut over to `payment_method_id`): Oracle just
provides it as a database-native mechanism instead of requiring an
application-level dual-write discipline.

## 5. Deprecated version migration

Two real, meaningfully different paradigms from anything module 5 did.

**Oracle's AutoUpgrade utility runs prechecks *before* the upgrade even
starts**: an "Analyze" mode that flags deprecated/desupported feature
usage and other breaking changes ahead of time, with many issues
auto-fixable during the deploy phase itself. That's the inverse of this
project's actual approach: module 5's dump-then-probe-for-breakage sequence
found real issues (the Postgres `public` schema ACL change, SQL Server's
fully-removed `*=` join syntax) only *after* the data had already moved,
because none of Postgres, MySQL, or SQL Server ship an equivalent
single-tool "tell me what will break before I touch anything" precheck for
a multi-major-version jump. Oracle also supports skipping versions in one
upgrade (this project's own SQL Server and MySQL jumps were each a single
hop; Oracle explicitly supports going from, say, 11.2.0.4 straight to a
current release).

**MongoDB's upgrade model is structurally different from every dump/restore
approach this project used, and stricter in a specific way.** It's a
*rolling binary swap* across replica set members: upgrade secondaries one
at a time, step down the primary, upgrade it last: keeping the deployment
available throughout, conceptually close to module 4's zero-downtime work
rather than module 5's offline transfer. But it comes with a hard
constraint none of the three flagship engines' migration paths had:
**major versions must be upgraded one at a time, in sequence** (5.0→6.0→7.0,
never 5.0 straight to 7.0): the exact multi-version jump module 5 did in a
single `pg_dump`/`mysqldump`/`BACKUP-RESTORE` pass isn't how a MongoDB
upgrade works at all. MongoDB also has its own version of the
NOT-VALID-then-VALIDATE two-phase pattern this project found repeatedly in
module 4 (Postgres, SQL Server), just applied at the whole-deployment level
instead of a single constraint: binaries get upgraded first, and new
backwards-incompatible behavior stays dormant until a separate, deliberate
`featureCompatibilityVersion` bump, which is also the point past which
downgrading gets harder. Same shape of lesson this project kept
re-discovering: don't force validation/new-behavior and the transport
change to happen atomically: showing up again at a completely different
layer of a completely different kind of database.

## What this module actually adds

Not a sixth round of "same bugs, new engine": the three flagship engines
already produced that pattern repeatedly (the FK-index asymmetry, the
sargability bug, the collation bug, all recurring across modules in
different forms). What Oracle and MongoDB add is a check on how much of
this project's hard-won process was a genuinely general lesson about
databases at scale versus a workaround for something Postgres, MySQL, and
SQL Server all happen to lack. The expand/sync-trigger/contract pattern
module 4 built by hand, MongoDB's schema-version-and-migrate-on-read
pattern, and Oracle's `DBMS_REDEFINITION`/EBR are the same underlying
idea: coexistence during a transition and deliberate cutover, appearing
as a hand-rolled process, an application-layer convention, and a
first-class database feature, respectively. That's a stronger conclusion
than "every engine has quirks": the quirks cluster around a small number of
real distributed-systems problems that don't go away, they just move to
whichever layer doesn't have a built-in answer for them.

## Status

Complete (research/write-up only, as scoped from the start: no local
Oracle or MongoDB build).
