-- Indexing corruption — MySQL
-- Adds the deliberately messy indexes described in README.md.
-- MySQL 5.7 has no CREATE INDEX IF NOT EXISTS (added in 8.0.29+), so each index
-- checks information_schema.STATISTICS first — same idempotency pattern used in
-- the partitioning module's migration script. Safe to re-run at any point.

DELIMITER $$

CREATE PROCEDURE create_index_if_missing(
    IN idx_name VARCHAR(64), IN tbl_name VARCHAR(64), IN idx_cols VARCHAR(255)
)
BEGIN
    DECLARE idx_exists INT DEFAULT 0;
    SELECT COUNT(*) INTO idx_exists FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl_name AND INDEX_NAME = idx_name;
    IF idx_exists = 0 THEN
        SET @sql = CONCAT('CREATE INDEX ', idx_name, ' ON ', tbl_name, ' (', idx_cols, ')');
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    ELSE
        SELECT CONCAT(idx_name, ' already exists, skipping') AS status;
    END IF;
END$$

DELIMITER ;

-- Wrong-order composite: workload filters customer_id first, but this leads
-- with status, so it can't serve that access pattern efficiently.
CALL create_index_if_missing('ix_orders_status_customer', 'orders', 'status, customer_id');

-- Redundant: once the composite below exists, this single-column index adds
-- nothing but write overhead.
CALL create_index_if_missing('ix_orders_customer_id', 'orders', 'customer_id');
CALL create_index_if_missing('ix_orders_customer_date', 'orders', 'customer_id, order_date');

-- Unused: nothing in the workload queries products.category.
CALL create_index_if_missing('ix_products_category', 'products', 'category');

DROP PROCEDURE create_index_if_missing;
