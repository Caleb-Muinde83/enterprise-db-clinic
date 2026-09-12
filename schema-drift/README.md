# Schema Drift / Normalization Debt

Goal: clean up structural debt that accumulates over "years" of ad hoc changes —
a denormalized blob where a proper table should exist, and a bolted-on feature
table with an inconsistent column type and no referential integrity — without
breaking existing data, and using a batched migration rather than one giant
blocking transaction.

## Files

- `generate_debt_data.py` — generates `addresses.csv` (one per customer) and
  `customer_notes.csv` (with deliberate malformed/orphaned `customer_id` values).
- `corrupt_postgres.sql` / `corrupt_mysql.sql` / `corrupt_sqlserver.sql` —
  creates the messy starting structures (empty; data loaded separately).
- `load_debt_data.py --engine <name>` — bulk-loads both CSVs, then populates
  `orders.shipping_address` from the staged addresses and drops the staging
  table.
- `validate.py --engine <name>` — quantifies the debt: malformed/orphaned
  `customer_notes` rows, address redundancy, current type, FK presence.
- `migrate.py --engine <name>` — the batched fix: proper `addresses` table,
  batched `address_id` backfill, `customer_notes` archive/retype/FK.

## Starting state

Two deliberate problems, chosen to be realistic rather than contrived:

1. **Denormalized address blob.** `orders` gets a `shipping_address` JSON/text
   column, populated with a full address per order. Since most customers ship
   to the same address across all their orders, this is heavily redundant —
   exactly the kind of "someone just JSON-blobbed it instead of normalizing"
   decision real systems accumulate. One address is generated per customer and
   reused across all of that customer's orders, so the redundancy is real and
   measurable, not fabricated for effect.

2. **Bolted-on table with type drift and no FK.** A new `customer_notes` table
   (simulating a CRM/support feature added later, by a different team, without
   following the rest of the schema's conventions): `customer_id` stored as
   `VARCHAR` instead of `BIGINT` like every other table, and **no foreign key
   constraint at all**. Some rows deliberately reference customer IDs that
   don't exist — orphaned data, the kind that accumulates when there's no FK
   to prevent it in the first place.

## Diagnosis

Before fixing anything, quantify the debt — don't assume, measure:

- How many `customer_notes` rows have a `customer_id` that isn't valid at all
  (non-numeric string)?
- How many have a numeric `customer_id` that doesn't match any real customer
  (genuinely orphaned)?
- How much redundancy exists in `orders.shipping_address` — how many orders
  share an identical blob with other orders from the same customer, versus how
  many distinct addresses actually exist?

`validate.py` runs all of this and prints a data-quality report — run it
before touching anything, and again after the fix, to prove the fix actually
worked rather than just assuming it did.

## Fix (batched, not one blocking transaction)

1. Create a proper `addresses` table — one row per customer, extracted from
   the distinct addresses in the blob data.
2. Add `orders.address_id`, backfill it in batches (chunked by `order_id`
   range with intermediate commits — the way you'd actually do this against a
   live, multi-million-row production table, not a single lock-everything
   transaction), then drop the now-redundant `shipping_address` column.
3. Archive `customer_notes` rows that are malformed (non-numeric `customer_id`)
   or orphaned (valid-looking but no matching customer) into
   `customer_notes_orphaned_archive` — never silently delete data — then
   remove them from the live table.
4. Alter `customer_notes.customer_id` to `BIGINT`, add the FK constraint.

## Steps

```bash
# 1. Generate the debt data (addresses per customer, customer_notes with
#    some deliberately invalid/orphaned customer_ids) — into ../data, the
#    directory already mounted into the SQL Server container for BULK INSERT
python generate_debt_data.py --scale full --out ../data

# 2. Apply corruption schema per engine (structures only, no data yet)
docker exec -i clinic_pg_old psql -U clinic -d clinic < corrupt_postgres.sql
docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < corrupt_mysql.sql
MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < corrupt_sqlserver.sql

# 3. Load the CSVs and populate the shipping_address blob from staged addresses
python load_debt_data.py --engine postgres
python load_debt_data.py --engine mysql
python load_debt_data.py --engine sqlserver

# 4. Diagnose — quantify the debt before touching anything
python validate.py --engine postgres
python validate.py --engine mysql
python validate.py --engine sqlserver

# 5. Apply the batched fix
python migrate.py --engine postgres
python migrate.py --engine mysql
python migrate.py --engine sqlserver

# 6. Re-validate — prove the fix worked
python validate.py --engine postgres
python validate.py --engine mysql
python validate.py --engine sqlserver
```

## What "success" looks like

- Zero orphaned or malformed rows remain in `customer_notes` (they're archived,
  not silently gone).
- `customer_notes.customer_id` is `BIGINT` with an enforced FK — a future
  orphan is now structurally impossible, the same protection every other
  table already had.
- `addresses` has one row per customer, not one per order — the redundancy
  measured in diagnosis is gone.
- The batched backfill completes without a single long-held lock — worth
  timing and comparing against what a single unbatched `UPDATE` would have
  required, as a concrete illustration of why batching matters at scale.
