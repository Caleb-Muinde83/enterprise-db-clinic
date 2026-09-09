-- Indexing fix — MySQL
-- MySQL has no IF EXISTS support for CREATE INDEX or DROP INDEX (confirmed against
-- MySQL's own bug tracker — DROP INDEX has never gained it), so every step here
-- checks information_schema.STATISTICS first, same pattern as corrupt_mysql.sql.

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

CREATE PROCEDURE drop_index_if_present(IN idx_name VARCHAR(64), IN tbl_name VARCHAR(64))
BEGIN
    DECLARE idx_exists INT DEFAULT 0;
    SELECT COUNT(*) INTO idx_exists FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = tbl_name AND INDEX_NAME = idx_name;
    IF idx_exists > 0 THEN
        SET @sql = CONCAT('DROP INDEX ', idx_name, ' ON ', tbl_name);
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    ELSE
        SELECT CONCAT(idx_name, ' already absent, skipping') AS status;
    END IF;
END$$

DELIMITER ;

-- Add the missing indexes.
CALL create_index_if_missing('ix_order_items_order_id', 'order_items', 'order_id');
CALL create_index_if_missing('ix_order_items_product_id', 'order_items', 'product_id');
CALL create_index_if_missing('ix_payments_order_id', 'payments', 'order_id');

-- Local index within each partition.
CALL create_index_if_missing('ix_events_customer_time', 'events', 'customer_id, event_time');

-- Drop the wrong-order composite; replace with the order the workload actually uses.
CALL drop_index_if_present('ix_orders_status_customer', 'orders');
CALL create_index_if_missing('ix_orders_customer_status', 'orders', 'customer_id, status');

-- Drop the redundant single-column index.
CALL drop_index_if_present('ix_orders_customer_id', 'orders');

-- Drop the unused index (see benchmark.py's usage-stats query for the evidence
-- that justifies this — performance_schema shows 0 scans before dropping).
CALL drop_index_if_present('ix_products_category', 'products');

DROP PROCEDURE create_index_if_missing;
DROP PROCEDURE drop_index_if_present;
