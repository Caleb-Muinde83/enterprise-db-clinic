-- Indexing fix — PostgreSQL

-- Add the missing indexes.
CREATE INDEX IF NOT EXISTS ix_order_items_order_id ON order_items (order_id);
CREATE INDEX IF NOT EXISTS ix_order_items_product_id ON order_items (product_id);
CREATE INDEX IF NOT EXISTS ix_payments_order_id ON payments (order_id);

-- Local index within each partition — partitioning narrowed the search to the
-- right partition(s), but a customer+date lookup inside a partition still
-- needs its own index to avoid a full partition scan.
CREATE INDEX IF NOT EXISTS ix_events_customer_time ON events (customer_id, event_time);

-- Drop the wrong-order composite; replace with the order the workload actually uses.
DROP INDEX IF EXISTS ix_orders_status_customer;
CREATE INDEX IF NOT EXISTS ix_orders_customer_status ON orders (customer_id, status);

-- Drop the redundant single-column index — the composite (customer_id, order_date)
-- already serves any query this one would have.
DROP INDEX IF EXISTS ix_orders_customer_id;

-- Drop the unused index. In a real migration you'd confirm 0 scans in
-- pg_stat_user_indexes first (see benchmark.py's usage-stats query) rather than
-- dropping on assumption — this script assumes that check already happened.
DROP INDEX IF EXISTS ix_products_category;
