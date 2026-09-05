-- Clean baseline schema — SQL Server 2017
-- No partitioning, no extra indexes beyond primary keys: this is the
-- "inherited system" starting state before any module's fix is applied.

IF OBJECT_ID('dbo.events', 'U') IS NOT NULL DROP TABLE dbo.events;
IF OBJECT_ID('dbo.payments', 'U') IS NOT NULL DROP TABLE dbo.payments;
IF OBJECT_ID('dbo.order_items', 'U') IS NOT NULL DROP TABLE dbo.order_items;
IF OBJECT_ID('dbo.orders', 'U') IS NOT NULL DROP TABLE dbo.orders;
IF OBJECT_ID('dbo.products', 'U') IS NOT NULL DROP TABLE dbo.products;
IF OBJECT_ID('dbo.customers', 'U') IS NOT NULL DROP TABLE dbo.customers;
GO

CREATE TABLE dbo.customers (
    customer_id     BIGINT IDENTITY(1,1) PRIMARY KEY,
    email           NVARCHAR(255) NOT NULL,
    first_name      NVARCHAR(100) NOT NULL,
    last_name       NVARCHAR(100) NOT NULL,
    country         NVARCHAR(100) NOT NULL,
    customer_tier   NVARCHAR(20) NOT NULL,  -- 'bronze' | 'silver' | 'gold' | 'platinum'
    created_at      DATETIME2 NOT NULL
);

CREATE TABLE dbo.products (
    product_id      BIGINT IDENTITY(1,1) PRIMARY KEY,
    sku             NVARCHAR(64) NOT NULL,
    name            NVARCHAR(255) NOT NULL,
    category        NVARCHAR(100) NOT NULL,
    price           DECIMAL(10, 2) NOT NULL,
    created_at      DATETIME2 NOT NULL
);

CREATE TABLE dbo.orders (
    order_id        BIGINT IDENTITY(1,1) PRIMARY KEY,
    customer_id     BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.customers(customer_id),
    order_date      DATETIME2 NOT NULL,
    status          NVARCHAR(20) NOT NULL,  -- 'pending' | 'paid' | 'shipped' | 'cancelled' | 'refunded'
    total_amount    DECIMAL(10, 2) NOT NULL
);

CREATE TABLE dbo.order_items (
    order_item_id   BIGINT IDENTITY(1,1) PRIMARY KEY,
    order_id        BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.orders(order_id),
    product_id      BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.products(product_id),
    quantity        INT NOT NULL,
    unit_price      DECIMAL(10, 2) NOT NULL
);

CREATE TABLE dbo.payments (
    payment_id      BIGINT IDENTITY(1,1) PRIMARY KEY,
    order_id        BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.orders(order_id),
    payment_date    DATETIME2 NOT NULL,
    amount          DECIMAL(10, 2) NOT NULL,
    method          NVARCHAR(20) NOT NULL,  -- 'card' | 'paypal' | 'bank_transfer' | 'wallet'
    status          NVARCHAR(20) NOT NULL   -- 'success' | 'failed' | 'refunded'
);

-- The big one: this is the table every module's problems center on.
-- Deliberately no partitioning, no index beyond the PK.
CREATE TABLE dbo.events (
    event_id        BIGINT IDENTITY(1,1) PRIMARY KEY,
    customer_id     BIGINT NOT NULL FOREIGN KEY REFERENCES dbo.customers(customer_id),
    event_type      NVARCHAR(50) NOT NULL,  -- 'page_view' | 'add_to_cart' | 'checkout' | 'login' | ...
    event_time      DATETIME2 NOT NULL,
    metadata        NVARCHAR(MAX)           -- JSON stored as text; use ISJSON()/JSON_VALUE() to query
);
