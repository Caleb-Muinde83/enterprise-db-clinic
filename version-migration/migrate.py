#!/usr/bin/env python3
"""
migrate.py — moves the full database from the *_old container (carrying
every fix from modules 1-4) into the *_new container, using the realistic
technique for each engine's major-version jump (see README.md for why each
technique was chosen). This is a full-database move, not a batched/live
migration like module 4 -- *_new starts empty and is not serving traffic,
so there's no concurrent-writer test here.

Usage:
    python migrate.py --engine postgres
"""

import argparse
import subprocess
import sys
import time

TABLES = ["customers", "products", "orders", "order_items", "payments",
          "events", "addresses", "customer_notes", "payment_methods"]


def sh(cmd, **kwargs):
    """Runs a single shell command (no pipe) via the OS default shell.
    Fine for non-piped commands like docker cp; NOT used for the bulk
    dump|restore transfers -- see pipe_two_commands for why."""
    print(f"$ {cmd}")
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)


def pipe_two_commands(cmd1, cmd2, label):
    """Runs cmd1 | cmd2 by wiring two Popen objects together directly,
    rather than via a shell pipe string. Two real problems this avoids:

    1. A shell pipe's exit code is normally just the LAST command's. A real
       failure exposed this: mysqldump died mid-dump (old server's
       net_write_timeout hit while blocked writing to a pipe backed up by
       the new instance's undersized default innodb_redo_log_capacity), but
       the receiving `mysql` process just hit EOF on partial input and
       exited 0 -- so the pipe's own exit code reported success on a
       silently truncated transfer. Row-count verification caught it that
       time, but that should be a second layer, not the only one.

    2. The obvious fix -- add `set -o pipefail` by explicitly invoking bash
       -- broke on this Windows setup: `bash` resolved to a WSL launcher
       stub instead of Git Bash's own bash.exe, and WSL wasn't set up to
       run it, failing immediately with a WSL-specific error unrelated to
       the actual migration. Driving both processes directly from Python
       sidesteps shell-resolution entirely and checks both exit codes
       explicitly, which is the property actually needed.
    """
    print(f"$ {' '.join(cmd1)} | {' '.join(cmd2)}")
    p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(cmd2, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p1.stdout.close()  # let p1 receive SIGPIPE if p2 exits early, instead of blocking forever
    p2_out, p2_err = p2.communicate()
    p1.wait()
    p1_err = p1.stderr.read()

    if p1.returncode != 0:
        print(f"FAILED at: {label} (SOURCE command failed, exit {p1.returncode})\n"
              f"STDERR: {p1_err.decode(errors='replace')[-2000:]}", file=sys.stderr)
        sys.exit(1)
    if p2.returncode != 0:
        print(f"FAILED at: {label} (DESTINATION command failed, exit {p2.returncode})\n"
              f"STDERR: {p2_err.decode(errors='replace')[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {label}")


def check(result, step):
    if result.returncode != 0:
        print(f"FAILED at: {step}\nSTDOUT: {result.stdout[-2000:]}\nSTDERR: {result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {step}")


def row_counts_postgres(container, user="clinic", db="clinic"):
    counts = {}
    for t in TABLES:
        r = subprocess.run(
            ["docker", "exec", "-i", container, "psql", "-U", user, "-d", db, "-t", "-c",
             f"SELECT COUNT(*) FROM {t};"],
            capture_output=True, text=True,
        )
        digits = [int(n) for n in r.stdout.split() if n.strip().isdigit()]
        counts[t] = digits[0] if digits else None  # None = table doesn't exist there
    return counts


def row_counts_mysql(container, user="clinic", pw="clinic", db="clinic"):
    counts = {}
    for t in TABLES:
        r = subprocess.run(
            ["docker", "exec", "-i", container, "mysql", "-u", user, f"-p{pw}", db, "-N", "-e",
             f"SELECT COUNT(*) FROM {t};"],
            capture_output=True, text=True,
        )
        digits = [int(n) for n in r.stdout.split() if n.strip().isdigit()]
        counts[t] = digits[0] if digits else None
    return counts


def row_counts_sqlserver(container, user="sa", pw=None, db="clinic", is_2022=False):
    """is_2022: the 2022 image renamed mssql-tools -> mssql-tools18 and that
    tool enforces TLS by default, needing -C (trust server cert) for a local
    unencrypted connection -- both discovered via a real failed run, not
    assumed from release notes. See README's SQL Server findings."""
    tools = "/opt/mssql-tools18/bin/sqlcmd" if is_2022 else "/opt/mssql-tools/bin/sqlcmd"
    extra_flags = ["-C"] if is_2022 else []
    counts = {}
    for t in TABLES:
        cmd = ["docker", "exec", "-i", container, tools] + extra_flags + [
             "-S", "localhost", "-U", user, "-P", pw, "-d", db, "-h", "-1", "-Q",
             f"SET NOCOUNT ON; SELECT COUNT(*) FROM {t};"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        digits = [int(n) for n in r.stdout.split() if n.strip().isdigit()]
        counts[t] = digits[0] if digits else None
    return counts


def compare_counts(old_counts, new_counts):
    print(f"\n{'table':<20}{'old':>12}{'new':>12}{'match':>8}")
    all_match = True
    for t in TABLES:
        o, n = old_counts.get(t), new_counts.get(t)
        match = "OK" if o == n else "MISMATCH"
        if o != n:
            all_match = False
        print(f"{t:<20}{str(o):>12}{str(n):>12}{match:>8}")
    return all_match


def migrate_postgres():
    print("=== Migrating PostgreSQL: clinic_pg_old (11) -> clinic_pg_new (16) ===")
    old_counts = row_counts_postgres("clinic_pg_old")

    t0 = time.time()
    # Plain-SQL dump piped directly into the new container's psql -- avoids
    # buffering gigabytes of dump text in this script for the 15M-row events
    # table. -i on the second docker exec is required for stdin piping.
    pipe_two_commands(
        ["docker", "exec", "clinic_pg_old", "pg_dump", "-U", "clinic", "clinic"],
        ["docker", "exec", "-i", "clinic_pg_new", "psql", "-U", "clinic", "-d", "clinic"],
        "pg_dump | psql",
    )
    print(f"Transfer took {time.time() - t0:.1f}s")

    new_counts = row_counts_postgres("clinic_pg_new")
    if not compare_counts(old_counts, new_counts):
        print("ROW COUNT MISMATCH -- inspect before trusting this migration.", file=sys.stderr)
        sys.exit(1)
    print("\nRow counts match across all tables. Migration complete.")


def migrate_mysql():
    print("=== Migrating MySQL: clinic_mysql_old (5.7) -> clinic_mysql_new (8.0) ===")
    old_counts = row_counts_mysql("clinic_mysql_old")

    t0 = time.time()
    # --single-transaction: consistent snapshot without locking clinic_old
    # for the duration of the dump (InnoDB-only, which every table here is).
    pipe_two_commands(
        ["docker", "exec", "clinic_mysql_old", "mysqldump", "--single-transaction",
         "-u", "clinic", "-pclinic", "clinic"],
        ["docker", "exec", "-i", "clinic_mysql_new", "mysql", "-u", "clinic", "-pclinic", "clinic"],
        "mysqldump | mysql",
    )
    print(f"Transfer took {time.time() - t0:.1f}s")

    new_counts = row_counts_mysql("clinic_mysql_new")
    if not compare_counts(old_counts, new_counts):
        print("ROW COUNT MISMATCH -- inspect before trusting this migration.", file=sys.stderr)
        sys.exit(1)
    print("\nRow counts match across all tables. Migration complete.")


def migrate_sqlserver_backup():
    """Phase 1: backup on old, transfer via docker cp, print the real logical
    file names. Does NOT restore -- the logical names are read here, not
    assumed, so the actual restore (phase 2) needs them passed in explicitly
    rather than this script guessing 'clinic'/'clinic_log' and hoping.

    Real gotchas found running this against actual containers, not assumed:
    - A freshly-started container briefly refuses `sa` logins with "Server
      is in script upgrade mode" -- transient, wait and retry rather than
      treating it as a hard failure.
    - The 2022 image renamed /opt/mssql-tools/ to /opt/mssql-tools18/, and
      that newer sqlcmd enforces TLS by default -- needs -C (trust server
      cert) for a local unencrypted connection to work at all. Both are
      genuine breaking changes between the two container images, not just
      "SQL Server 2017 vs 2022" behavior.
    - On Windows Git Bash, paths starting with / get auto-translated to a
      Windows path unless MSYS_NO_PATHCONV=1 prefixes the command -- affects
      any standalone docker exec run directly in the shell (not this
      script's own subprocess calls, which don't go through that translation)."""
    print("=== SQL Server migration, phase 1: backup + transfer ===")
    old_pw = "Clinic!2017"

    t0 = time.time()
    check(sh(
        'docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd '
        f'-S localhost -U sa -P "{old_pw}" -Q '
        '"BACKUP DATABASE clinic TO DISK = \'/var/opt/mssql/data/clinic_migrate.bak\' WITH FORMAT;"'
    ), f"BACKUP DATABASE on old ({time.time() - t0:.1f}s)")

    check(sh("docker cp clinic_mssql_old:/var/opt/mssql/data/clinic_migrate.bak ./clinic_migrate.bak"),
          "docker cp out to host")
    check(sh("docker cp ./clinic_migrate.bak clinic_mssql_new:/var/opt/mssql/data/clinic_migrate.bak"),
          "docker cp into new container")

    new_pw = "Clinic!2022"
    filelist = sh(
        'docker exec -i clinic_mssql_new /opt/mssql-tools18/bin/sqlcmd -C '
        f'-S localhost -U sa -P "{new_pw}" -Q '
        '"RESTORE FILELISTONLY FROM DISK = \'/var/opt/mssql/data/clinic_migrate.bak\';"'
    )
    check(filelist, "RESTORE FILELISTONLY")
    print("\n" + "=" * 60)
    print("Read the LogicalName column (first column) above for the data")
    print("and log files, then run phase 2 with those exact values, e.g.:")
    print("  python migrate.py --engine sqlserver --phase restore \\")
    print("    --data-name <LogicalName of the .mdf row> \\")
    print("    --log-name <LogicalName of the .ldf row>")
    print("Do not guess or assume these match the old database's names.")
    print("=" * 60)


def migrate_sqlserver_restore(data_name, log_name):
    """Phase 2: actual restore, using logical file names the caller read
    from phase 1's FILELISTONLY output -- not hardcoded here."""
    print(f"=== SQL Server migration, phase 2: restore (data='{data_name}', log='{log_name}') ===")
    new_pw = "Clinic!2022"
    t0 = time.time()
    check(sh(
        'docker exec -i clinic_mssql_new /opt/mssql-tools18/bin/sqlcmd -C '
        f'-S localhost -U sa -P "{new_pw}" -Q '
        '"RESTORE DATABASE clinic FROM DISK = \'/var/opt/mssql/data/clinic_migrate.bak\' '
        f"WITH MOVE '{data_name}' TO '/var/opt/mssql/data/clinic.mdf', "
        f"MOVE '{log_name}' TO '/var/opt/mssql/data/clinic_log.ldf', REPLACE;\""
    ), f"RESTORE DATABASE on new ({time.time() - t0:.1f}s)")

    old_counts = row_counts_sqlserver("clinic_mssql_old", pw="Clinic!2017", is_2022=False)
    new_counts = row_counts_sqlserver("clinic_mssql_new", pw=new_pw, is_2022=True)
    if not compare_counts(old_counts, new_counts):
        print("ROW COUNT MISMATCH -- inspect before trusting this migration.", file=sys.stderr)
        sys.exit(1)
    print("\nRow counts match across all tables. Migration complete.")


ENGINES = {"postgres": migrate_postgres, "mysql": migrate_mysql}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=["postgres", "mysql", "sqlserver"])
    parser.add_argument("--phase", choices=["backup", "restore"],
                         help="sqlserver only: 'backup' runs backup+transfer+shows real logical "
                              "file names; 'restore' does the actual restore using --data-name/"
                              "--log-name read from phase 1's output")
    parser.add_argument("--data-name", help="sqlserver restore phase: LogicalName of the data file")
    parser.add_argument("--log-name", help="sqlserver restore phase: LogicalName of the log file")
    args = parser.parse_args()

    if args.engine == "sqlserver":
        if args.phase == "restore":
            if not args.data_name or not args.log_name:
                print("--data-name and --log-name are required for --phase restore "
                      "(read them from phase 1's RESTORE FILELISTONLY output).", file=sys.stderr)
                sys.exit(1)
            migrate_sqlserver_restore(args.data_name, args.log_name)
        else:
            migrate_sqlserver_backup()
    else:
        ENGINES[args.engine]()


if __name__ == "__main__":
    main()
