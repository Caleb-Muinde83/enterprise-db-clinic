#!/usr/bin/env python3
"""
generate_partition_ddl.py — Produces the three per-engine partition migration scripts
for the partitioning module (unpartitioned tables at scale).

Monthly partition boundaries are derived from the SAME date range used by
seed/generate_data.py (3 years ending 2026-09-01), so the partitions actually line up
with where the seeded data lives. If you change DATE_RANGE_YEARS or the end date in
generate_data.py, update the constants below to match.

Engine-specific constraints this script accounts for (verified, not assumed):
  - MySQL: InnoDB partitioned tables cannot have foreign keys, in either direction
    (dev.mysql.com/doc/refman/5.7/en/partitioning-limitations-storage-engines.html).
    The FK on events.customer_id must be dropped — referential integrity moves to
    the application layer or a periodic validation query.
  - PostgreSQL 11: a foreign key FROM a partitioned table TO a regular table IS
    supported (only the reverse — a regular table referencing a partitioned one —
    is unsupported in PG11). So events.customer_id's FK can stay.
  - SQL Server: no FK/partitioning restriction either way. FK stays.
  - All three: any unique/primary key on a partitioned table must include the
    partitioning column, so event_id's PK becomes a composite (event_id, event_time)
    on all three engines.

Usage:
    python generate_partition_ddl.py --out .
"""

import argparse
from datetime import datetime, timedelta

DATE_RANGE_YEARS = 3
END_DATE = datetime(2026, 9, 1)


def month_boundaries():
    """Returns a list of first-of-month datetimes spanning the seeded date range,
    from the month containing START through the month containing END (inclusive of
    the END boundary itself, since it's used as an exclusive upper bound)."""
    start = END_DATE - timedelta(days=365 * DATE_RANGE_YEARS)
    boundaries = []
    cur = datetime(start.year, start.month, 1)
    end_marker = datetime(END_DATE.year, END_DATE.month, 1)
    while cur <= end_marker:
        boundaries.append(cur)
        if cur.month == 12:
            cur = datetime(cur.year + 1, 1, 1)
        else:
            cur = datetime(cur.year, cur.month + 1, 1)
    return boundaries


def generate_postgres(boundaries):
    parts = []
    parts.append("""-- Partitioning: partition `events` by month — PostgreSQL
-- PG11 supports a foreign key FROM a partitioned table TO a regular table, so
-- events.customer_id's FK is preserved. The PK must include the partition key
-- (event_time), so it becomes composite: (event_id, event_time).

BEGIN;

ALTER TABLE events RENAME TO events_old;

CREATE TABLE events (
    event_id        BIGINT NOT NULL,
    customer_id     BIGINT NOT NULL REFERENCES customers(customer_id),
    event_type      TEXT NOT NULL,
    event_time      TIMESTAMP NOT NULL,
    metadata        JSONB,
    PRIMARY KEY (event_id, event_time)
) PARTITION BY RANGE (event_time);
""")

    for i in range(len(boundaries) - 1):
        lo = boundaries[i].strftime("%Y-%m-%d")
        hi = boundaries[i + 1].strftime("%Y-%m-%d")
        name = f"events_y{boundaries[i].year}m{boundaries[i].month:02d}"
        parts.append(
            f"CREATE TABLE {name} PARTITION OF events "
            f"FOR VALUES FROM ('{lo}') TO ('{hi}');"
        )

    parts.append("CREATE TABLE events_default PARTITION OF events DEFAULT;")

    parts.append("""
INSERT INTO events (event_id, customer_id, event_type, event_time, metadata)
SELECT event_id, customer_id, event_type, event_time, metadata FROM events_old;

-- Reattach the original sequence so future inserts keep auto-incrementing correctly.
-- NOTE: renaming the table to events_old does NOT rename its serial sequence —
-- Postgres names sequences once at creation time and never updates them on rename,
-- so the sequence is still called events_event_id_seq, not events_old_event_id_seq.
ALTER TABLE events ALTER COLUMN event_id SET DEFAULT nextval('events_event_id_seq');
ALTER SEQUENCE events_event_id_seq OWNED BY events.event_id;
SELECT setval('events_event_id_seq', (SELECT MAX(event_id) FROM events));

DROP TABLE events_old;

COMMIT;
""")
    return "\n".join(parts)


def generate_mysql(boundaries):
    parts = []
    parts.append("""-- Partitioning: partition `events` by month — MySQL
-- IMPORTANT: InnoDB partitioned tables cannot have foreign keys in either direction
-- (dev.mysql.com/doc/refman/5.7/en/partitioning-limitations-storage-engines.html).
-- events.customer_id's FK must be dropped — referential integrity for this table
-- now has to be enforced at the application layer or via a periodic validation
-- query instead of the database. This is a real, unavoidable trade-off of
-- partitioning a child table in MySQL, not a simplification for this exercise.
--
-- If the constraint name below doesn't match your instance, look it up first:
--   SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
--   WHERE TABLE_NAME='events' AND CONSTRAINT_TYPE='FOREIGN KEY';

ALTER TABLE events DROP FOREIGN KEY events_ibfk_1;

-- PK must include the partitioning column (event_time), so it becomes composite.
ALTER TABLE events DROP PRIMARY KEY, ADD PRIMARY KEY (event_id, event_time);

ALTER TABLE events PARTITION BY RANGE (TO_DAYS(event_time)) (
""")

    partition_lines = []
    for i in range(len(boundaries) - 1):
        hi = boundaries[i + 1].strftime("%Y-%m-%d")
        name = f"p{boundaries[i].year}{boundaries[i].month:02d}"
        partition_lines.append(f"    PARTITION {name} VALUES LESS THAN (TO_DAYS('{hi}'))")
    partition_lines.append("    PARTITION pmax VALUES LESS THAN MAXVALUE")
    parts.append(",\n".join(partition_lines) + "\n);")

    return "\n".join(parts)


def generate_sqlserver(boundaries):
    parts = []
    parts.append("""-- Partitioning: partition `events` by month — SQL Server
-- No FK/partitioning restriction here (unlike MySQL), so events.customer_id's FK
-- is preserved. PK must include the partition key, so it becomes composite
-- (event_id, event_time), same as the other two engines.

USE clinic;
GO

CREATE PARTITION FUNCTION pf_events_monthly (DATETIME2)
AS RANGE RIGHT FOR VALUES (
""")
    boundary_values = ", ".join(f"'{b.strftime('%Y-%m-%d')}'" for b in boundaries[1:])
    parts.append(f"    {boundary_values}\n);\nGO\n")

    parts.append("""CREATE PARTITION SCHEME ps_events_monthly
AS PARTITION pf_events_monthly ALL TO ([PRIMARY]);
GO

EXEC sp_rename 'events', 'events_old';
GO

CREATE TABLE events (
    event_id        BIGINT IDENTITY(1,1) NOT NULL,
    customer_id     BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.customers(customer_id),
    event_type      NVARCHAR(50) NOT NULL,
    event_time      DATETIME2 NOT NULL,
    metadata        NVARCHAR(MAX),
    CONSTRAINT PK_events PRIMARY KEY (event_id, event_time)
) ON ps_events_monthly(event_time);
GO

SET IDENTITY_INSERT events ON;
GO

INSERT INTO events (event_id, customer_id, event_type, event_time, metadata)
SELECT event_id, customer_id, event_type, event_time, metadata FROM events_old;
GO

SET IDENTITY_INSERT events OFF;
GO

DBCC CHECKIDENT ('events', RESEED);
GO

DROP TABLE events_old;
GO
""")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=".", help="Output directory for the three SQL files")
    args = parser.parse_args()

    boundaries = month_boundaries()
    print(f"Generating {len(boundaries) - 1} monthly partitions "
          f"({boundaries[0].date()} to {boundaries[-1].date()})")

    with open(f"{args.out}/postgres_partition.sql", "w") as f:
        f.write(generate_postgres(boundaries))
    with open(f"{args.out}/mysql_partition.sql", "w") as f:
        f.write(generate_mysql(boundaries))
    with open(f"{args.out}/sqlserver_partition.sql", "w") as f:
        f.write(generate_sqlserver(boundaries))

    print("Wrote postgres_partition.sql, mysql_partition.sql, sqlserver_partition.sql")


if __name__ == "__main__":
    main()
