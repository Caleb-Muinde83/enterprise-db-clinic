-- Indexing fix — SQL Server
USE clinic;
GO

-- Add the missing indexes.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_order_items_order_id' AND object_id = OBJECT_ID('order_items'))
    CREATE INDEX ix_order_items_order_id ON order_items (order_id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_order_items_product_id' AND object_id = OBJECT_ID('order_items'))
    CREATE INDEX ix_order_items_product_id ON order_items (product_id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_payments_order_id' AND object_id = OBJECT_ID('payments'))
    CREATE INDEX ix_payments_order_id ON payments (order_id);
GO

-- Local index within each partition. Aligned to the same partition scheme so it
-- stays partition-local rather than becoming a separate non-aligned structure.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_events_customer_time' AND object_id = OBJECT_ID('events'))
    CREATE INDEX ix_events_customer_time ON events (customer_id, event_time) ON ps_events_monthly(event_time);
GO

-- Drop the wrong-order composite; replace with the order the workload actually uses.
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_orders_status_customer' AND object_id = OBJECT_ID('orders'))
    DROP INDEX ix_orders_status_customer ON orders;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_orders_customer_status' AND object_id = OBJECT_ID('orders'))
    CREATE INDEX ix_orders_customer_status ON orders (customer_id, status);
GO

-- Drop the redundant single-column index.
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_orders_customer_id' AND object_id = OBJECT_ID('orders'))
    DROP INDEX ix_orders_customer_id ON orders;
GO

-- Drop the unused index (see benchmark.py's usage-stats query for the evidence
-- that justifies this — sys.dm_db_index_usage_stats shows 0 seeks/scans first).
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_products_category' AND object_id = OBJECT_ID('products'))
    DROP INDEX ix_products_category ON products;
GO
