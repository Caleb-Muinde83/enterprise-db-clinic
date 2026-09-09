-- Indexing corruption — SQL Server
-- Adds the deliberately messy indexes described in README.md. Idempotent via
-- IF NOT EXISTS checks against sys.indexes — T-SQL allows DDL directly inside
-- an IF block, no dynamic SQL needed here (unlike MySQL).

USE clinic;
GO

-- Wrong-order composite: workload filters customer_id first, but this leads
-- with status, so it can't serve that access pattern efficiently.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_orders_status_customer' AND object_id = OBJECT_ID('orders'))
    CREATE INDEX ix_orders_status_customer ON orders (status, customer_id);
GO

-- Redundant: once the composite below exists, this single-column index adds
-- nothing but write overhead.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_orders_customer_id' AND object_id = OBJECT_ID('orders'))
    CREATE INDEX ix_orders_customer_id ON orders (customer_id);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_orders_customer_date' AND object_id = OBJECT_ID('orders'))
    CREATE INDEX ix_orders_customer_date ON orders (customer_id, order_date);
GO

-- Unused: nothing in the workload queries products.category.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'ix_products_category' AND object_id = OBJECT_ID('products'))
    CREATE INDEX ix_products_category ON products (category);
GO
