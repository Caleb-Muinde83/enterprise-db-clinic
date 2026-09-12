-- Schema drift corruption — PostgreSQL
-- Creates the messy starting state: a denormalized shipping_address blob on
-- orders, and a bolted-on customer_notes table with type drift and no FK.
-- Data is loaded separately (load_debt_data.py) since it comes from CSVs —
-- this script only creates the empty structures.

-- Staging table: holds the one-address-per-customer data used to populate
-- orders.shipping_address. Not part of the "drift" itself — a normal ETL
-- intermediate, dropped once the blob is populated.
DROP TABLE IF EXISTS addresses_staging;
CREATE TABLE addresses_staging (
    customer_id BIGINT PRIMARY KEY,
    street      TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    zip         TEXT NOT NULL,
    country     TEXT NOT NULL
);

-- The denormalized blob column on orders.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_address TEXT;

-- Bolted-on feature table: customer_id as TEXT (should be BIGINT), no FK.
DROP TABLE IF EXISTS customer_notes;
CREATE TABLE customer_notes (
    note_id     BIGINT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    note_text   TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL
);
