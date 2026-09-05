# enterprise-db-clinic

A hands-on lab that inherits, breaks, diagnoses, and fixes the database problems real
enterprises live with — unpartitioned tables, bad indexing, schema drift, zero-downtime
cutovers, and deprecated version migrations — built and benchmarked across **PostgreSQL**,
**MySQL**, and **SQL Server**, with **Oracle** and **MongoDB** covered as comparison studies.

Full curriculum and module specs: [`docs/curriculum.md`](docs/curriculum.md)

## Phase order

| Phase | Module | Status |
|---|---|---|
| 0 | Foundation — dataset + environments | 🚧 in progress |
| 1 | Unpartitioned tables at scale | ⏳ not started |
| 2 | Missing / incorrect indexing | ⏳ not started |
| 3 | Schema drift / normalization debt | ⏳ not started |
| 4 | Zero-downtime migration (all 3 engines) | ⏳ not started |
| 5 | Deprecated version migration (run last) | ⏳ not started |
| 6 | Cross-engine comparison (Oracle, MongoDB) | ⏳ not started |

Version migration runs last on purpose — it has to migrate a schema that already carries
every other fix, not a clean baseline.

## Domain

An e-commerce/fintech hybrid: `customers`, `products`, `orders`, `order_items`, `payments`,
and a large time-ordered `events` table (clickstream/audit log) — the table every module's
problems center on. Target scale: ~10-20M rows in `events`.

## Repo layout

```
environments/       docker-compose files per engine (old-version + new-version containers)
seed/                dataset generator + per-engine schema DDL + bulk-load scripts
corruption/          scripts that deliberately re-introduce each module's problem (Phase 1+)
docs/                curriculum spec and per-phase write-ups
data/                generated CSVs (gitignored — regenerate locally, don't commit)
```

## Getting started (Phase 0)

```bash
# 1. Bring up an engine's old-version container (example: Postgres)
cd environments/postgres && docker compose up -d postgres_old

# 2. Install generator dependencies
cd ../../seed && pip install -r requirements.txt

# 3. Generate seed data (small scale for local testing first)
python generate_data.py --scale small --out ../data

# 4. Apply schema + bulk-load into the running container
psql -h localhost -p 5411 -U clinic -d clinic -f schema/postgres_schema.sql
python load/load_postgres.py --data-dir ../data --host localhost --port 5411
```

See `seed/generate_data.py --help` for scale options (`small` / `medium` / `full`).
`full` targets the ~10-20M row scale — expect it to take a while and to need real disk space.
