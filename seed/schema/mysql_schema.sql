-- Clean baseline schema — MySQL 5.7
-- No partitioning, no extra indexes beyond primary keys: this is the
-- "inherited system" starting state before any module's fix is applied.

DROP TABLE IF EXISTS events, payments, order_items, orders, products, customers;

CREATE TABLE customers (
    customer_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255) NOT NULL,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    country         VARCHAR(100) NOT NULL,
    customer_tier   VARCHAR(20) NOT NULL,   -- 'bronze' | 'silver' | 'gold' | 'platinum'
    created_at      DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE products (
    product_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    sku             VARCHAR(64) NOT NULL,
    name            VARCHAR(255) NOT NULL,
    category        VARCHAR(100) NOT NULL,
    price           DECIMAL(10, 2) NOT NULL,
    created_at      DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE orders (
    order_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id     BIGINT NOT NULL,
    order_date      DATETIME NOT NULL,
    status          VARCHAR(20) NOT NULL,  -- 'pending' | 'paid' | 'shipped' | 'cancelled' | 'refunded'
    total_amount    DECIMAL(10, 2) NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
) ENGINE=InnoDB;

CREATE TABLE order_items (
    order_item_id   BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id        BIGINT NOT NULL,
    product_id      BIGINT NOT NULL,
    quantity        INT NOT NULL,
    unit_price      DECIMAL(10, 2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
) ENGINE=InnoDB;

CREATE TABLE payments (
    payment_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id        BIGINT NOT NULL,
    payment_date    DATETIME NOT NULL,
    amount          DECIMAL(10, 2) NOT NULL,
    method          VARCHAR(20) NOT NULL,  -- 'card' | 'paypal' | 'bank_transfer' | 'wallet'
    status          VARCHAR(20) NOT NULL,  -- 'success' | 'failed' | 'refunded'
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB;

-- The big one: this is the table every module's problems center on.
-- Deliberately no partitioning, no index beyond the PK.
CREATE TABLE events (
    event_id        BIGINT AUTO_INCREMENT PRIMARY KEY,
    customer_id     BIGINT NOT NULL,
    event_type      VARCHAR(50) NOT NULL,  -- 'page_view' | 'add_to_cart' | 'checkout' | 'login' | ...
    event_time      DATETIME NOT NULL,
    metadata        JSON,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
) ENGINE=InnoDB;
