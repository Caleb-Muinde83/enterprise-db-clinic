-- Partitioning: partition `events` by month — MySQL
-- IMPORTANT: InnoDB partitioned tables cannot have foreign keys in either direction
-- (dev.mysql.com/doc/refman/5.7/en/partitioning-limitations-storage-engines.html).
-- events.customer_id's FK must be dropped — referential integrity for this table
-- now has to be enforced at the application layer or via a periodic validation
-- query instead of the database. This is a real, unavoidable trade-off of
-- partitioning a child table in MySQL, not a simplification for this exercise.
--
-- Each step below checks current state before acting, rather than assuming a
-- fresh start. MySQL DDL auto-commits per statement and doesn't participate in
-- transactions, so an interrupted run (killed query, crash, etc.) can leave
-- some steps already applied — a naive re-run from the top would fail trying to
-- redo them. Safe to re-run this whole script at any point, from any partial state.

-- Step 1: drop the FK, if it still exists.
SET @fk_name = (
    SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'events'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY' LIMIT 1
);
SET @sql = IF(@fk_name IS NOT NULL,
    CONCAT('ALTER TABLE events DROP FOREIGN KEY ', @fk_name),
    'SELECT "FK already dropped, skipping" AS status');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 2: make the PK composite (event_id, event_time), if it isn't already.
-- PK must include the partitioning column for MySQL to allow partitioning at all.
SET @pk_has_time = (
    SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'events'
      AND CONSTRAINT_NAME = 'PRIMARY' AND COLUMN_NAME = 'event_time'
);
SET @sql = IF(@pk_has_time = 0,
    'ALTER TABLE events DROP PRIMARY KEY, ADD PRIMARY KEY (event_id, event_time)',
    'SELECT "PK already composite, skipping" AS status');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- Step 3: apply partitioning, if the table isn't already partitioned.
SET @already_partitioned = (
    SELECT COUNT(*) FROM information_schema.PARTITIONS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'events'
      AND PARTITION_NAME IS NOT NULL
);
SET @sql = IF(@already_partitioned = 0,

    'ALTER TABLE events PARTITION BY RANGE (TO_DAYS(event_time)) (\n    PARTITION p202309 VALUES LESS THAN (TO_DAYS(''2023-10-01'')),\n    PARTITION p202310 VALUES LESS THAN (TO_DAYS(''2023-11-01'')),\n    PARTITION p202311 VALUES LESS THAN (TO_DAYS(''2023-12-01'')),\n    PARTITION p202312 VALUES LESS THAN (TO_DAYS(''2024-01-01'')),\n    PARTITION p202401 VALUES LESS THAN (TO_DAYS(''2024-02-01'')),\n    PARTITION p202402 VALUES LESS THAN (TO_DAYS(''2024-03-01'')),\n    PARTITION p202403 VALUES LESS THAN (TO_DAYS(''2024-04-01'')),\n    PARTITION p202404 VALUES LESS THAN (TO_DAYS(''2024-05-01'')),\n    PARTITION p202405 VALUES LESS THAN (TO_DAYS(''2024-06-01'')),\n    PARTITION p202406 VALUES LESS THAN (TO_DAYS(''2024-07-01'')),\n    PARTITION p202407 VALUES LESS THAN (TO_DAYS(''2024-08-01'')),\n    PARTITION p202408 VALUES LESS THAN (TO_DAYS(''2024-09-01'')),\n    PARTITION p202409 VALUES LESS THAN (TO_DAYS(''2024-10-01'')),\n    PARTITION p202410 VALUES LESS THAN (TO_DAYS(''2024-11-01'')),\n    PARTITION p202411 VALUES LESS THAN (TO_DAYS(''2024-12-01'')),\n    PARTITION p202412 VALUES LESS THAN (TO_DAYS(''2025-01-01'')),\n    PARTITION p202501 VALUES LESS THAN (TO_DAYS(''2025-02-01'')),\n    PARTITION p202502 VALUES LESS THAN (TO_DAYS(''2025-03-01'')),\n    PARTITION p202503 VALUES LESS THAN (TO_DAYS(''2025-04-01'')),\n    PARTITION p202504 VALUES LESS THAN (TO_DAYS(''2025-05-01'')),\n    PARTITION p202505 VALUES LESS THAN (TO_DAYS(''2025-06-01'')),\n    PARTITION p202506 VALUES LESS THAN (TO_DAYS(''2025-07-01'')),\n    PARTITION p202507 VALUES LESS THAN (TO_DAYS(''2025-08-01'')),\n    PARTITION p202508 VALUES LESS THAN (TO_DAYS(''2025-09-01'')),\n    PARTITION p202509 VALUES LESS THAN (TO_DAYS(''2025-10-01'')),\n    PARTITION p202510 VALUES LESS THAN (TO_DAYS(''2025-11-01'')),\n    PARTITION p202511 VALUES LESS THAN (TO_DAYS(''2025-12-01'')),\n    PARTITION p202512 VALUES LESS THAN (TO_DAYS(''2026-01-01'')),\n    PARTITION p202601 VALUES LESS THAN (TO_DAYS(''2026-02-01'')),\n    PARTITION p202602 VALUES LESS THAN (TO_DAYS(''2026-03-01'')),\n    PARTITION p202603 VALUES LESS THAN (TO_DAYS(''2026-04-01'')),\n    PARTITION p202604 VALUES LESS THAN (TO_DAYS(''2026-05-01'')),\n    PARTITION p202605 VALUES LESS THAN (TO_DAYS(''2026-06-01'')),\n    PARTITION p202606 VALUES LESS THAN (TO_DAYS(''2026-07-01'')),\n    PARTITION p202607 VALUES LESS THAN (TO_DAYS(''2026-08-01'')),\n    PARTITION p202608 VALUES LESS THAN (TO_DAYS(''2026-09-01'')),\n    PARTITION pmax VALUES LESS THAN MAXVALUE\n)',
    'SELECT "Already partitioned, skipping" AS status');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
