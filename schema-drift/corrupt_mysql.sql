-- Schema drift corruption — MySQL
-- Same structures as the Postgres version. MySQL 5.7 has no ADD COLUMN IF NOT
-- EXISTS (same gap as CREATE/DROP INDEX, established earlier in this repo), so
-- the column add checks information_schema first.

DROP TABLE IF EXISTS addresses_staging;
CREATE TABLE addresses_staging (
    customer_id BIGINT PRIMARY KEY,
    street      VARCHAR(255) NOT NULL,
    city        VARCHAR(255) NOT NULL,
    state       VARCHAR(255) NOT NULL,
    zip         VARCHAR(20) NOT NULL,
    country     VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'orders'
      AND COLUMN_NAME = 'shipping_address'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE orders ADD COLUMN shipping_address TEXT',
    'SELECT "shipping_address column already exists, skipping" AS status');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

DROP TABLE IF EXISTS customer_notes;
CREATE TABLE customer_notes (
    note_id     BIGINT PRIMARY KEY,
    customer_id VARCHAR(20) NOT NULL,
    note_text   TEXT NOT NULL,
    created_at  DATETIME NOT NULL
) ENGINE=InnoDB;
