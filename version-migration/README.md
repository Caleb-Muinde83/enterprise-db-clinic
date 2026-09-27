# Module 5: Deprecated version migration

Runs last, deliberately: it is the module most likely to expose problems
introduced by everything before it, so it needs the full curriculum's worth
of schema, data, and fixes already in place to be a meaningful test.

## The problem being simulated

Moving a production database from an old, increasingly unsupported engine
version to a current one: PostgreSQL 11 to 16, MySQL 5.7 to 8.0, and SQL Server
2017 to 2022 (versions fixed by `environments/*/docker-compose.yml`, not
chosen here). `*_old` carries every fix from modules 1-4; `*_new` starts
empty and receives the migration.

This module has three parts: actually moving the data with a realistic
technique, testing specific documented breaking/deprecated behavior changes
against this real schema (not assumed from changelogs), and a capstone
validation pass that re-checks every earlier module still holds on the new
version.

## Migration technique per engine

| Engine | Technique |
|---|---|
| PostgreSQL | `pg_dump` (plain SQL) piped directly into `psql` on `clinic_pg_new`: `docker exec clinic_pg_old pg_dump ... \| docker exec -i clinic_pg_new psql ...`, avoids buffering the whole dump in a script |
| MySQL | `mysqldump --single-transaction` (consistent snapshot, no locking) piped the same way into `mysql` on `clinic_mysql_new` |
| SQL Server | Native `BACKUP DATABASE` on `clinic_mssql_old` (written inside the container's own data dir, not the host-mounted `/data`: `sqlserver_new` doesn't share that mount), `docker cp` the `.bak` out to the host and into `clinic_mssql_new`, then `RESTORE DATABASE`. Split into two explicit phases in `migrate.py` (`--phase backup` then `--phase restore --data-name ... --log-name ...`) rather than guessing the logical file names automatically: phase 1 prints the real `RESTORE FILELISTONLY` output, phase 2 requires those exact values as arguments

## Migration mechanics: results

All three engines migrated and verified with real row-count checks:
9 tables, about 29M total rows, exact match on every table for every engine:

| Engine | Technique | Transfer time | Notes |
|---|---|---|---|
| PostgreSQL 11→16 | `pg_dump \| psql` | 982.1s | Clean on the first attempt, no obstacles |
| MySQL 5.7→8.0 | `mysqldump --single-transaction \| mysql` | 21,017.7s (final successful run) | Needed real fixes: see below |
| SQL Server 2017→2022 | `BACKUP`/`docker cp`/`RESTORE` | 219.6s backup + 427.8s restore | Needed real fixes: see below |

### MySQL: three real obstacles, found by running it, not by reading changelogs first

The first attempt failed silently: the pipe reported success while the
transfer had actually died partway through `events` (~110-120K of 15M
rows). Root cause, confirmed directly in both containers' logs: the *new*
8.0 instance's default `innodb_redo_log_capacity` (100MB) couldn't keep up
with the bulk insert rate, backpressure built up through the pipe, and the
*old* 5.7 instance's `net_write_timeout` (default 60s) eventually killed
the connection trying to write into a backed-up pipe. The receiving `mysql`
process just hit EOF on the truncated input and exited 0, which is why the
original shell-pipe-based version of this script wrongly reported success.

Three things ended up mattering (applied together, so individual
attribution is honest-but-uncertain rather than cleanly isolated):

1. `SET GLOBAL innodb_redo_log_capacity = 1073741824;` on the new instance
2. `GRANT PROCESS ON *.* TO 'clinic'@'%';` on the old instance (unrelated
   privilege gap: `mysqldump` wants `PROCESS` to read tablespace metadata,
   errors otherwise)
3. `SET GLOBAL net_write_timeout = 3600; SET GLOBAL net_read_timeout = 3600;`
   on the old instance

Also found along the way: a shell pipe's exit code is normally just the
*last* command's, which is what let the original truncated transfer report
success in the first place. The fix: rewriting the transfer to drive both
`docker exec` processes directly via `subprocess.Popen` rather than a shell
pipe string: checks both exit codes explicitly and sidesteps a separate,
unrelated problem this surfaced: an attempted `bash -c "set -o pipefail"`
fix broke on this Windows setup because `bash` resolved to a non-functional
WSL launcher stub instead of Git Bash's own `bash.exe`.

### SQL Server: two real breaking changes between the 2017 and 2022 container images

- **`/opt/mssql-tools/` was renamed to `/opt/mssql-tools18/`** in the 2022
  image: a genuine breaking change at the tooling level, not just an
  engine-version thing.
- **`mssql-tools18`'s `sqlcmd` enforces TLS by default**, requiring `-C`
  (trust server certificate) for even a local unencrypted connection to
  work: encrypted-by-default is itself a real SQL Server behavior change
  worth documenting alongside the compatibility-level probe below.
- Separately (environmental, not version-specific): a freshly-started
  container briefly refuses `sa` logins with "Server is in script upgrade
  mode": transient, needs a retry/wait rather than treating it as a hard
  failure.

## Capstone validation: results

Confirmed clean across all three engines: every check from modules 1-4
holds on the migrated `*_new` instances:

| Module | Postgres | MySQL | SQL Server |
|---|---|---|---|
| 1: Partitioning still prunes | 37 partitions, plan shows a single-partition scan | 37 partitions confirmed | 7,415 logical reads vs. 15M total rows |
| 2: Fix indexes intact | All 5 expected + PKs present | Same | Same |
| 3: schema-drift FKs enforced | Validated (`convalidated=t`), 0 NULLs | Present, 0 NULLs | Trusted (`is_not_trusted=0`), 0 NULLs |
| 4: zero-downtime FK + column drop | Validated, 0 NULLs, `method` confirmed gone | Same | Same |

Worth noting honestly: the first version of this script had two real bugs of
its own, caught only by actually running it against live containers:
`capstone_validate.py` never printed `stderr`, so when `clinic_pg_new` and
`clinic_mssql_new` were briefly stopped (unrelated Docker restart), every
check failed silently as blank output instead of a visible error. And the
original `method`-column check used raw-text presence instead of a
`COUNT(*)`, which is the *exact* class of bug schema-drift already
documented and fixed once (`SELECT 1` returning zero rows isn't a parseable
false): re-learned the hard way here instead of applying it the first time.
Both fixed; the table above reflects the corrected, re-verified run.

## Breaking/deprecated behavior probes: reframed after review, tested against this real schema, not assumed from release notes

- **Postgres 15 changed default privileges on the `public` schema** (no
  longer grants `CREATE` to `PUBLIC`). Since this repo lands on 16, this is
  directly in scope: does `clinic` user's restore actually succeed cleanly,
  or does creating objects in `public` need an explicit grant first
  **Confirmed via the schema's raw ACL, old vs. new**: Postgres 11's
  `public` schema ACL shows `PUBLIC` (the pseudo-role meaning "everyone")
  had both `USAGE` and `CREATE`; the restored Postgres 16 instance shows
  `PUBLIC` with `USAGE` only, `CREATE` narrowed to the new
  `pg_database_owner` role instead. The dump/restore did **not** carry the
  old permissive grant forward: PG15's changed default genuinely took
  effect. `clinic` itself never notices because it's a superuser in this
  environment (bypasses schema privilege checks entirely), which is exactly
  why the first version of this probe: testing whether `clinic` could
  create a table: gave a misleadingly reassuring "yes" that proved nothing
  about the actual grant state. Any non-superuser, non-owner role restored
  the same way would be blocked from creating objects in `public` where it
  previously wasn't.
- **MySQL 8.0's default collation** (`utf8mb4_general_ci` →
  `utf8mb4_0900_ai_ci`). Existing columns carry their original collation
  explicitly in the dump's `CREATE TABLE` statements, so they won't silently
  drift: the real test is what collation a **newly created column** gets
  post-migration when none is specified. **Confirmed: a new column gets
  8.0's new default (`utf8mb4_0900_ai_ci`) directly**, not a lingering old
  default inherited from the migrated database: collation defaults are a
  server/database-level setting the dump doesn't override for new objects.
- **MySQL 8.0's default auth plugin** (`mysql_native_password` →
  `caching_sha2_password`). A dump/restore doesn't carry `mysql.user` or
  server-level auth config, so this isn't about existing connections
  breaking: the real test is what plugin a **newly created user** gets by
  default on the fresh 8.0 instance. **Confirmed: `caching_sha2_password`**
 : any client/driver that only supports the old plugin would fail to
  connect as a newly provisioned user post-migration, a real and separate
  risk from anything the data transfer itself touches.
- **MySQL 8.0 no longer implicitly orders `GROUP BY` results** (5.7 did,
  undocumented but real behavior). Pick a real `GROUP BY`-without-`ORDER BY`
  query from an earlier module and check whether row order actually differs
  post-migration. **Confirmed, concretely**: `SELECT status, COUNT(*) FROM
  orders GROUP BY status` returns `cancelled, paid, pending, refunded,
  shipped` on 5.7 and `paid, pending, shipped, cancelled, refunded` on 8.0.
  Real proof, not a theoretical risk: identical query, identical data,
  visibly different row order.
- **SQL Server keeps the old compatibility level (140) after restoring into
  2022** rather than auto-upgrading to 160 (deliberate Microsoft behavior,
  avoids silently changing query plans on upgrade). **Confirmed**: the
  first post-restore check read `compatibility_level = 140`: the original
  2017 setting, not auto-upgraded. Bumping to 160 explicitly did change the
  plan (a `Worktable` scan appeared that wasn't there at 140), confirming
  the compatibility level genuinely affects the optimizer's behavior, not
  just a cosmetic setting. (Note: this probe isn't side-effect-free: the
  `ALTER DATABASE` it runs persists, so a second run of this script no
  longer sees the original 140 baseline, only 160.)
- **SQL Server's deprecated-feature usage counters**
  (`sys.dm_os_performance_counters`, object `SQLServer:Deprecated
  Features`) only increment when a deprecated construct actually runs.
  Attempted to exercise this with the classic `*=` old-style outer join:
  and hit `Msg 102, Incorrect syntax near '*='`, a **parser-level syntax
  error**, not the semantic "not allowed at this compatibility level" error
  (`Msg 4147`) expected going in. SQL Server 2022 has fully removed the
  `*=`/`=*` grammar, not just deprecated it: the parser doesn't recognize
  it as valid SQL at all anymore, at any compatibility level. The counter
  mechanism itself is real and would catch a construct that's deprecated
  but still parseable; this particular textbook example turned out to be
  too old to even reach that stage. Genuinely useful finding on its own:
  deprecated features have a lifecycle, and far enough behind, some of what
  used to be merely flagged has been deleted from the language entirely:
  surfacing as a hard migration-time syntax break rather than a subtle
  behavior change.

## Capstone validation

The strongest piece of this module: re-run one representative check from
*every earlier module* against the migrated `*_new` instance:

- Partitioning still prunes correctly (module 1)
- The FK-supporting-index behavior from module 2 still holds
- schema-drift's `customer_notes`/`addresses` FKs are still enforced
  (module 3)
- zero-downtime's `payment_methods` FK on `payments` still validates
  (module 4)

If version migration silently broke or reset anything the rest of the
curriculum built, this is what catches it, not just "row counts match."

## Files

- `migrate.py --engine <name>`: Postgres/MySQL do the full dump→restore in
  one call with row-count verification built in. SQL Server needs
  `--phase backup` then `--phase restore --data-name <n> --log-name <n>`
  (see table above for why)
- `deprecated_probes.py --engine <name>`: the six reframed breaking-behavior
  checks, run against `*_new`
- `capstone_validate.py --engine <name>`: re-runs one real check from each
  of modules 1-4 against `*_new`

## Status

Complete: all three engines' data migration verified (row-count exact
match, ~29M rows), capstone validation confirmed clean against modules 1-4
on all three engines, and all six breaking/deprecated-behavior probes run
with real, verified results. Two findings, the Postgres schema-ACL change
and the SQL Server removed-grammar behavior, turned out meaningfully
different from what was assumed going in. Module 5, and the full
curriculum, complete.
