-- Indexing corruption — PostgreSQL
-- Adds the deliberately messy indexes described in README.md. Safe to re-run
-- (IF NOT EXISTS on every index).

-- Wrong-order composite: workload filters customer_id first, but this index
-- leads with status, so it can't serve that access pattern efficiently.
CREATE INDEX IF NOT EXISTS ix_orders_status_customer ON orders (status, customer_id);

-- Redundant: once the composite below exists, this single-column index adds
-- nothing but write overhead — any customer_id-only query can use the
-- composite's leftmost prefix instead.
CREATE INDEX IF NOT EXISTS ix_orders_customer_id ON orders (customer_id);
CREATE INDEX IF NOT EXISTS ix_orders_customer_date ON orders (customer_id, order_date);

-- Unused: nothing in the workload queries products.category. Its removal has
-- to be justified by usage stats (pg_stat_user_indexes), not assumption.
CREATE INDEX IF NOT EXISTS ix_products_category ON products (category);
