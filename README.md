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
`psql`, `mysql`, or `sqlcmd` installed on your host machine. They also don't depend on
`make`, since it isn't available by default in Git Bash on Windows.

```bash
# 1. Bring up an engine's old-version container (example: Postgres)
# --wait blocks until the healthcheck passes, not just until the container exists —
# important for MySQL and SQL Server, whose first-ever startup takes 20-40s.
cd environments/postgres && docker compose up -d --wait postgres_old && cd ../..

# 2. Install generator dependencies
cd seed && pip install -r requirements.txt && cd ..

# 3. Generate seed data (small scale for local testing first)
cd seed && python generate_data.py --scale small --out ../data && cd ..

# 4. Apply schema via the container's own client (no local install needed)
docker exec -i clinic_pg_old psql -U clinic -d clinic < seed/schema/postgres_schema.sql

# 5. Bulk-load into the running container
cd seed && python load/load_postgres.py --data-dir ../data --host localhost --port 5411 && cd ..
```

Same pattern for the other two engines:

```bash
# MySQL
cd environments/mysql && docker compose up -d --wait mysql_old && cd ../..
docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < seed/schema/mysql_schema.sql
cd seed && python load/load_mysql.py --data-dir ../data --host localhost --port 3357 && cd ..

# SQL Server
cd environments/sqlserver && docker compose up -d --wait sqlserver_old && cd ../..
# MSYS_NO_PATHCONV=1 stops Git Bash on Windows from mangling the /opt/... path below —
# harmless to include on macOS/Linux too.
MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < seed/schema/sqlserver_schema.sql
cd seed && python load/load_sqlserver.py --password 'Clinic!2017' && cd ..
```
(Don't pass `--container-data-dir` explicitly on Git Bash — typing a leading-slash path
directly at the prompt triggers the same MSYS mangling as before. The script already
defaults to `/data`, which matches the docker-compose volume mount.)

If you have `make` available (macOS/Linux, or Windows with it installed separately), the
`Makefile` wraps the container-up and schema-apply steps as shortcuts — but it's optional,
not required.

See `seed/generate_data.py --help` for scale options (`small` / `medium` / `full`).
`full` targets the ~10-20M row scale — expect it to take a while and to need real disk space.
