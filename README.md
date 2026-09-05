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

These commands run schema/load steps **through the container itself** — you don't need
`psql`, `mysql`, or `sqlcmd` installed on your host machine.

```bash
# 1. Bring up an engine's old-version container (example: Postgres)
make up-postgres

# 2. Install generator dependencies
cd seed && pip install -r requirements.txt && cd ..

# 3. Generate seed data (small scale for local testing first)
cd seed && python generate_data.py --scale small --out ../data && cd ..

# 4. Apply schema (via docker exec — no local client needed)
make apply-schema-postgres

# 5. Bulk-load into the running container
cd seed && python load/load_postgres.py --data-dir ../data --host localhost --port 5411 && cd ..
```

Same pattern for MySQL (`make up-mysql`, `make apply-schema-mysql`, `load_mysql.py`) and
SQL Server (`make up-sqlserver`, `make apply-schema-sqlserver`, `load_sqlserver.py`) —
see the Makefile for the exact `docker exec` commands each one runs.

See `seed/generate_data.py --help` for scale options (`small` / `medium` / `full`).
`full` targets the ~10-20M row scale — expect it to take a while and to need real disk space.
