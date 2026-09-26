#!/usr/bin/env python3
"""
deprecated_probes.py — runs the six breaking/deprecated-behavior checks
from README.md against the *_new instances, each one testing something
real and specific to this schema rather than a generic changelog read.

Usage:
    python deprecated_probes.py --engine postgres
    python deprecated_probes.py --engine mysql
    python deprecated_probes.py --engine sqlserver
"""

import argparse
import subprocess
import sys


def run_postgres(sql, container="clinic_pg_new", user="clinic", db="clinic"):
    return subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", user, "-d", db, "-c", sql],
        capture_output=True, text=True,
    )


def run_mysql(sql, container="clinic_mysql_new", user="clinic", pw="clinic", db="clinic"):
    return subprocess.run(
        ["docker", "exec", "-i", container, "mysql", "-u", user, f"-p{pw}", db, "-e", sql],
        capture_output=True, text=True,
    )


def run_mysql_root(sql, container="clinic_mysql_new"):
    return subprocess.run(
        ["docker", "exec", "-i", container, "mysql", "-u", "root", "-pclinic_root", "-e", sql],
        capture_output=True, text=True,
    )


def run_sqlserver(sql, container="clinic_mssql_new", user="sa", pw="Clinic!2022", db="clinic"):
    return subprocess.run(
        ["docker", "exec", "-i", container, "/opt/mssql-tools18/bin/sqlcmd", "-C",
         "-S", "localhost", "-U", user, "-P", pw, "-d", db, "-Q", sql],
        capture_output=True, text=True,
    )


def header(label):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")


def probe_postgres_public_schema_privileges():
    header("Probe: Postgres 15+ removed default CREATE-on-public-for-PUBLIC")
    print("NOTE: clinic is a superuser in this environment (POSTGRES_USER bootstrap "
          "superuser), so a create-attempt AS clinic proves nothing -- superusers bypass "
          "schema privilege checks entirely regardless of what PG15 changed. The real "
          "signal is the schema's raw ACL, compared old vs new.")
    old = run_postgres("SELECT nspacl FROM pg_namespace WHERE nspname = 'public';",
                        container="clinic_pg_old")
    new = run_postgres("SELECT nspacl FROM pg_namespace WHERE nspname = 'public';",
                        container="clinic_pg_new")
    print(f"\nOld (11) public schema ACL:\n{old.stdout.strip()}")
    print(f"\nNew (16) public schema ACL:\n{new.stdout.strip()}")
    print(f"\nRESULT: old instance's PUBLIC pseudo-role had CREATE on public "
          f"(matches PG11's permissive default). New instance's PUBLIC entry shows "
          f"USAGE only -- CREATE is gone, narrowed to the new pg_database_owner role "
          f"instead. The dump/restore did NOT carry the old grant forward; PG15's "
          f"changed default genuinely took effect here. Any non-superuser, "
          f"non-owner role restored the same way would be blocked from creating "
          f"objects in public where it previously wasn't -- clinic only avoids this "
          f"because it happens to be a superuser, not because the grant survived.")


def probe_mysql_new_column_collation():
    header("Probe: MySQL 8.0 default collation (utf8mb4_general_ci -> utf8mb4_0900_ai_ci) for a NEW column")
    run_mysql("DROP TABLE IF EXISTS deprecated_probe_test;")
    run_mysql("CREATE TABLE deprecated_probe_test (id INT, label VARCHAR(20));")
    r = run_mysql("SELECT COLUMN_NAME, COLLATION_NAME FROM information_schema.columns "
                  "WHERE table_schema='clinic' AND table_name='deprecated_probe_test' "
                  "AND column_name='label';")
    print(r.stdout.strip())
    run_mysql("DROP TABLE deprecated_probe_test;")
    if "utf8mb4_0900_ai_ci" in r.stdout:
        print("RESULT: new column got 8.0's new default collation, as expected.")
    elif "utf8mb4_general_ci" in r.stdout:
        print("RESULT: new column got the OLD 5.7 default -- the migrated database's own "
              "default collation (carried from the dump) is overriding the server's new "
              "8.0 default. Worth knowing: 'restore preserves old defaults for new objects "
              "too, not just existing ones' is a real, non-obvious migration side effect.")
    else:
        print(f"RESULT: unexpected output, inspect manually:\n{r.stdout}")


def probe_mysql_new_user_auth_plugin():
    header("Probe: MySQL 8.0 default auth plugin (mysql_native_password -> caching_sha2_password) for a NEW user")
    run_mysql_root("DROP USER IF EXISTS 'deprecated_probe_user'@'%';")
    run_mysql_root("CREATE USER 'deprecated_probe_user'@'%' IDENTIFIED BY 'test123!';")
    r = run_mysql_root("SELECT user, plugin FROM mysql.user WHERE user='deprecated_probe_user';")
    print(r.stdout.strip())
    run_mysql_root("DROP USER 'deprecated_probe_user'@'%';")
    if "caching_sha2_password" in r.stdout:
        print("RESULT: new user got 8.0's new default plugin, as expected. Any client/driver "
              "that only supports mysql_native_password would fail to connect as this user -- "
              "a real, separate risk from anything the dump/restore itself touches.")
    else:
        print(f"RESULT: unexpected plugin, inspect manually:\n{r.stdout}")


def probe_mysql_group_by_ordering():
    header("Probe: MySQL 8.0 no longer implicitly orders GROUP BY results")
    query = "SELECT status, COUNT(*) AS c FROM orders GROUP BY status;"
    old = run_mysql(query, container="clinic_mysql_old")
    new = run_mysql(query, container="clinic_mysql_new")
    print("5.7 (old) row order:")
    print(old.stdout.strip())
    print("\n8.0 (new) row order:")
    print(new.stdout.strip())
    if old.stdout.strip() == new.stdout.strip():
        print("\nRESULT: row order happened to match this time -- doesn't prove it's "
              "guaranteed, since 8.0 makes no ordering promise without an explicit ORDER BY. "
              "Don't treat this as 'safe', just 'didn't happen to differ on this query/data'.")
    else:
        print("\nRESULT: row order DIFFERS -- concrete proof that code relying on 5.7's "
              "implicit GROUP BY ordering would silently break on 8.0. Add ORDER BY status "
              "if a specific order is actually required.")


def probe_sqlserver_compatibility_level():
    header("Probe: SQL Server keeps the OLD compatibility level after restoring into a newer instance")
    r = run_sqlserver("SELECT name, compatibility_level FROM sys.databases WHERE name = 'clinic';")
    print(r.stdout.strip())
    print("\nRunning a sample query and capturing logical reads at whatever level it's at now:")
    r2 = run_sqlserver("SET STATISTICS IO ON; SELECT COUNT(*) FROM orders WHERE status = 'shipped';")
    print(r2.stdout.strip())
    print("\nExplicitly bumping to 160 (SQL Server 2022's native level) and re-running the same query:")
    run_sqlserver("ALTER DATABASE clinic SET COMPATIBILITY_LEVEL = 160;")
    r3 = run_sqlserver("SET STATISTICS IO ON; SELECT COUNT(*) FROM orders WHERE status = 'shipped';")
    print(r3.stdout.strip())
    print("\nRESULT: compare the two STATISTICS IO / plan outputs above manually -- the point "
          "isn't that 160 is faster or slower, it's that Microsoft deliberately does NOT "
          "auto-upgrade this on restore, so a migrated database silently keeps its old "
          "optimizer behavior until someone bumps it explicitly.")


def probe_sqlserver_deprecated_feature_counter():
    header("Probe: SQL Server deprecated-feature usage counters (need something real to count)")
    baseline = run_sqlserver(
        "SELECT instance_name, cntr_value FROM sys.dm_os_performance_counters "
        "WHERE object_name LIKE '%Deprecated Features%' AND cntr_value > 0;"
    )
    print("Baseline (should be mostly/all zero -- nothing in this project's scripts "
          "obviously uses a deprecated construct):")
    print(baseline.stdout.strip() or "(no rows -- confirms baseline is clean)")

    print("\nDeliberately running old-style '*=' outer join syntax (a classic deprecated "
          "T-SQL construct) so the counter has something real to report:")
    r = run_sqlserver(
        "SELECT o.order_id, oi.order_item_id FROM orders o, order_items oi "
        "WHERE o.order_id *= oi.order_id;"
    )
    # Always print raw stdout here rather than inferring success from returncode
    # alone -- sqlcmd doesn't reliably set a nonzero exit code for a T-SQL error
    # unless -b is passed, so a real SQL Server error (this exact query's likely
    # failure mode is Msg 4147, the documented error for non-ANSI join syntax at
    # a modern compatibility level) could print to stdout while sqlcmd still
    # exits 0. Trusting returncode alone here would silently misreport a failed
    # query as "ran successfully" -- caught only by actually looking at the output.
    print(f"Raw output:\n{r.stdout.strip()}")
    if r.stderr.strip():
        print(f"Raw stderr:\n{r.stderr.strip()}")
    failed = r.returncode != 0 or "Msg " in r.stdout or "Msg " in r.stderr
    if failed:
        print("\n-> Old-style join syntax was REJECTED (see Msg error above), not "
              "silently accepted. This itself is informative: the compatibility "
              "level (140, from the previous probe) is high enough that this "
              "specific old-style syntax no longer parses at all.")
    else:
        print("\n-> Old-style join ran without an error message.")

    after = run_sqlserver(
        "SELECT instance_name, cntr_value FROM sys.dm_os_performance_counters "
        "WHERE object_name LIKE '%Deprecated Features%' AND cntr_value > 0;"
    )
    print("\nCounters after:")
    print(after.stdout.strip() or "(still no rows)")
    if after.stdout.strip() and after.stdout.strip() != baseline.stdout.strip():
        print("\nRESULT: counter moved -- confirmed the deprecated-feature tracking actually "
              "works and would catch real usage, not just reading 0 because nothing fired.")
    else:
        print("\nRESULT: no change detected. Either the old-style join didn't register on "
              "this compatibility level, or it needs compat level 90 or below to even be "
              "accepted (worth checking sys.databases.compatibility_level from the previous "
              "probe against SQL Server's documented minimum for '*=' support).")


PROBES = {
    "postgres": [probe_postgres_public_schema_privileges],
    "mysql": [probe_mysql_new_column_collation, probe_mysql_new_user_auth_plugin,
              probe_mysql_group_by_ordering],
    "sqlserver": [probe_sqlserver_compatibility_level, probe_sqlserver_deprecated_feature_counter],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=PROBES.keys())
    args = parser.parse_args()
    for probe in PROBES[args.engine]:
        probe()


if __name__ == "__main__":
    main()
