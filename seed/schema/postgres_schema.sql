-- Clean baseline schema — PostgreSQL
-- No partitioning, no extra indexes beyond primary keys: this is the
-- "inherited system" starting state before any module's fix is applied.

DROP TABLE IF EXISTS events, payments, order_items, orders, products, customers CASCADE;

CREATE TABLE customers (
    customer_id     BIGSERIAL PRIMARY KEY,
    email           TEXT NOT NULL,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    country         TEXT NOT NULL,
    customer_tier   TEXT NOT NULL,      -- 'bronze' | 'silver' | 'gold' | 'platinum'
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE products (
    product_id      BIGSERIAL PRIMARY KEY,
    sku             TEXT NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    price           NUMERIC(10, 2) NOT NULL,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE orders (
    order_id        BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT NOT NULL REFERENCES customers(customer_id),
    order_date      TIMESTAMP NOT NULL,
    status          TEXT NOT NULL,      -- 'pending' | 'paid' | 'shipped' | 'cancelled' | 'refunded'
    total_amount    NUMERIC(10, 2) NOT NULL
);

CREATE TABLE order_items (
    order_item_id   BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders(order_id),
    product_id      BIGINT NOT NULL REFERENCES products(product_id),
    quantity        INT NOT NULL,
    unit_price      NUMERIC(10, 2) NOT NULL
);

CREATE TABLE payments (
    payment_id      BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders(order_id),
    payment_date    TIMESTAMP NOT NULL,
    amount          NUMERIC(10, 2) NOT NULL,
    method          TEXT NOT NULL,      -- 'card' | 'paypal' | 'bank_transfer' | 'wallet'
    status          TEXT NOT NULL       -- 'success' | 'failed' | 'refunded'
);

-- The big one: this is the table every module's problems center on.
-- Deliberately no partitioning, no index beyond the PK.
CREATE TABLE events (
    event_id        BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT NOT NULL REFERENCES customers(customer_id),
    event_type      TEXT NOT NULL,      -- 'page_view' | 'add_to_cart' | 'checkout' | 'login' | ...
    event_time      TIMESTAMP NOT NULL,
    metadata         JSONB
);
