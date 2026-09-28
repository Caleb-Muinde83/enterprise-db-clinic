# The Enterprise DB Clinic Handbook: Six Real Database Failure Modes Fixed on Postgres, MySQL, and SQL Server, Then Checked Against Oracle and MongoDB

Most database tutorials show you the happy path: run this migration, get this
speedup, ship it. They rarely show you the part where the migration script
silently reports success while the transfer actually died at row 116,955,
or where a fix for a 6-hour stall turns out to be a *different* 6-hour stall
in disguise, or where the tool built to verify a fix has the same bug the
fix itself was written to catch.

This handbook covers all of that because it actually happened, three
times, over the course of building **Enterprise DB Clinic**: a set of six
modules, five of them hands-on, simulating the database problems that show
up at real scale, on PostgreSQL, MySQL, and SQL Server side by side,
against the same ~29-million-row dataset (a hybrid e-commerce/fintech
schema containing customers, orders, payments, and a 15-million-row event stream). The
sixth module checks the first five against Oracle and MongoDB, on paper,
to find out how much of what this project had to hand-build is a real
universal problem versus a gap specific to three particular engines.

Every one of the three engines is a genuinely separate flagship build, not
one implementation with two syntax translations bolted on. That turned out
to matter more than expected: the same fix, applied the same way, produced
three different outcomes often enough that "cross-engine difference" became
the single most common finding across the whole project.

## How to read this

Three kinds of claims appear throughout, kept deliberately distinct:

* **Repository fact:** something directly observed running against a real
  container: a terminal output, a matched timestamp, an execution plan, a
  row count. These are the load-bearing claims. Where the evidence for one
  is worth showing, it's shown.
* **Architectural inference:** a reasonable conclusion drawn by combining
  several repository facts (for example: the exact causal chain behind
  MySQL's silent migration failure in module 5 was pieced together from
  log timestamps on two separate containers, not observed as a single
  event). Inferences are flagged as such where they appear, and could in
  principle be wrong in a way a repository fact can't be.
* **General engineering guidance:** industry practice and tradeoffs that
  aren't specific to this project's containers at all. This is most
  concentrated in module 6, which is research rather than a hands-on
  build, and in the "Production perspective" note at the end of each
  module.

## The six modules

1. **Partitioning:** an unpartitioned 15M-row events table, and what
   monthly range partitioning actually buys you (and costs you) on each
   engine
2. **Indexing:** wrong-order composite indexes, redundant indexes, and
   the missing ones that actually matter, verified with mechanism-level
   evidence instead of wall-clock timing
3. **Schema drift:** a bolted-on table with the wrong column type and a
   denormalized JSON blob repeated across 3 million rows, fixed live
4. **Zero-downtime migration:** extracting a column into a proper lookup
   table with a foreign key, on a table taking real concurrent traffic,
   with a script hammering it the whole time to prove "zero-downtime"
   rather than assert it
5. **Deprecated version migration:** moving the whole database three
   major versions forward on each engine (Postgres 11→16, MySQL 5.7→8.0,
   SQL Server 2017→2022), then checking whether anything the first four
   modules built survived
6. **Cross-engine comparison:** the same five problems, researched
   against Oracle and MongoDB rather than built by hand, to separate
   universal database problems from gaps specific to three engines

## The thread that runs through all six

The working rule for this whole project was **verify, don't assume**:
extract real evidence (an execution plan, a lock wait, a matched
timestamp) instead of trusting that a script finished cleanly because it
said so. That rule paid for itself constantly. It also, twice, caught the
verification tooling itself breaking the same rule it existed to enforce.
That is either an embarrassing footnote or the most honest possible proof
that the rule was worth having. This handbook includes both instances,
because cutting them would be exactly the kind of unverified "it worked"
claim the whole project was built to avoid.

---
