.PHONY: up-postgres up-mysql up-sqlserver up-all \
        up-new-postgres up-new-mysql up-new-sqlserver up-new-all \
        down-postgres down-mysql down-sqlserver down-all ps \
        seed-small seed-medium seed-full \
        apply-schema-postgres apply-schema-mysql apply-schema-sqlserver apply-schema-all \
        generate-partition-ddl \
        apply-partition-postgres apply-partition-mysql apply-partition-sqlserver apply-partition-all \
        benchmark-partition-postgres-before benchmark-partition-postgres-after \
        benchmark-partition-mysql-before benchmark-partition-mysql-after \
        benchmark-partition-sqlserver-before benchmark-partition-sqlserver-after \
        benchmark-partition-report \
        corrupt-index-postgres corrupt-index-mysql corrupt-index-sqlserver \
        fix-index-postgres fix-index-mysql fix-index-sqlserver \
        benchmark-index-postgres-before benchmark-index-postgres-after \
        benchmark-index-mysql-before benchmark-index-mysql-after \
        benchmark-index-sqlserver-before benchmark-index-sqlserver-after \
        benchmark-index-report benchmark-index-evidence-report \
        benchmark-index-usage-stats benchmark-index-duplicate-check \
        generate-debt-data \
        corrupt-drift-postgres corrupt-drift-mysql corrupt-drift-sqlserver \
        load-debt-postgres load-debt-mysql load-debt-sqlserver \
        validate-drift-postgres validate-drift-mysql validate-drift-sqlserver \
        migrate-drift-postgres migrate-drift-mysql migrate-drift-sqlserver \
        expand-zd-postgres expand-zd-mysql expand-zd-sqlserver \
        writer-zd-postgres writer-zd-mysql writer-zd-sqlserver \
        writer-zd-postgres-fk writer-zd-mysql-fk writer-zd-sqlserver-fk \
        contract-zd-postgres contract-zd-mysql contract-zd-sqlserver \
        contract-zd-postgres-skip-drop contract-zd-mysql-skip-drop contract-zd-sqlserver-skip-drop \
        validate-zd-postgres validate-zd-mysql validate-zd-sqlserver \
        migrate-version-postgres migrate-version-mysql \
        migrate-version-sqlserver-backup migrate-version-sqlserver-restore \
        capstone-validate-postgres capstone-validate-mysql capstone-validate-sqlserver \
        deprecated-probes-postgres deprecated-probes-mysql deprecated-probes-sqlserver

# ============================================================================
# MODULE 0: Foundation — containers, schema, seed data
# ============================================================================

up-postgres:
	cd environments/postgres && docker compose up -d --wait postgres_old

up-mysql:
	cd environments/mysql && docker compose up -d --wait mysql_old

up-sqlserver:
	cd environments/sqlserver && docker compose up -d --wait sqlserver_old

up-all: up-postgres up-mysql up-sqlserver

# The *_new containers — only needed from module 5 onward (version migration).
up-new-postgres:
	cd environments/postgres && docker compose up -d --wait postgres_new

up-new-mysql:
	cd environments/mysql && docker compose up -d --wait mysql_new

up-new-sqlserver:
	cd environments/sqlserver && docker compose up -d --wait sqlserver_new

up-new-all: up-new-postgres up-new-mysql up-new-sqlserver

down-postgres:
	cd environments/postgres && docker compose down

down-mysql:
	cd environments/mysql && docker compose down

down-sqlserver:
	cd environments/sqlserver && docker compose down

down-all: down-postgres down-mysql down-sqlserver

# Quick status check across every clinic container, old and new.
ps:
	docker ps -a --filter "name=clinic_"

# These run the DDL through the container's own client — no local psql/mysql/sqlcmd needed.
apply-schema-postgres:
	docker exec -i clinic_pg_old psql -U clinic -d clinic < seed/schema/postgres_schema.sql

apply-schema-mysql:
	docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < seed/schema/mysql_schema.sql

apply-schema-sqlserver:
	MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < seed/schema/sqlserver_schema.sql

apply-schema-all: apply-schema-postgres apply-schema-mysql apply-schema-sqlserver

seed-small:
	cd seed && python generate_data.py --scale small --out ../data

seed-medium:
	cd seed && python generate_data.py --scale medium --out ../data

seed-full:
	cd seed && python generate_data.py --scale full --out ../data

# ============================================================================
# MODULE 1: Partitioning
# ============================================================================

# Writes postgres_partition.sql / mysql_partition.sql / sqlserver_partition.sql into partitioning/
generate-partition-ddl:
	cd partitioning && python generate_partition_ddl.py --out .

apply-partition-postgres:
	docker exec -i clinic_pg_old psql -U clinic -d clinic < partitioning/postgres_partition.sql

apply-partition-mysql:
	docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < partitioning/mysql_partition.sql

apply-partition-sqlserver:
	MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < partitioning/sqlserver_partition.sql

apply-partition-all: apply-partition-postgres apply-partition-mysql apply-partition-sqlserver

# Run --label before-partitioning, apply the DDL, then --label after to compare.
benchmark-partition-postgres-before:
	cd partitioning && python benchmark.py --engine postgres --label before

benchmark-partition-postgres-after:
	cd partitioning && python benchmark.py --engine postgres --label after

benchmark-partition-mysql-before:
	cd partitioning && python benchmark.py --engine mysql --label before

benchmark-partition-mysql-after:
	cd partitioning && python benchmark.py --engine mysql --label after

benchmark-partition-sqlserver-before:
	cd partitioning && python benchmark.py --engine sqlserver --label before

benchmark-partition-sqlserver-after:
	cd partitioning && python benchmark.py --engine sqlserver --label after

# Reads results.json, prints the before/after comparison across all three engines.
benchmark-partition-report:
	cd partitioning && python benchmark.py --report

# ============================================================================
# MODULE 2: Indexing
# ============================================================================

corrupt-index-postgres:
	docker exec -i clinic_pg_old psql -U clinic -d clinic < indexing/corrupt_postgres.sql

corrupt-index-mysql:
	docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < indexing/corrupt_mysql.sql

corrupt-index-sqlserver:
	MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < indexing/corrupt_sqlserver.sql

fix-index-postgres:
	docker exec -i clinic_pg_old psql -U clinic -d clinic < indexing/fix_postgres.sql

fix-index-mysql:
	docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < indexing/fix_mysql.sql

fix-index-sqlserver:
	MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < indexing/fix_sqlserver.sql

benchmark-index-postgres-before:
	cd indexing && python benchmark.py --engine postgres --label before

benchmark-index-postgres-after:
	cd indexing && python benchmark.py --engine postgres --label after

benchmark-index-mysql-before:
	cd indexing && python benchmark.py --engine mysql --label before

benchmark-index-mysql-after:
	cd indexing && python benchmark.py --engine mysql --label after

benchmark-index-sqlserver-before:
	cd indexing && python benchmark.py --engine sqlserver --label before

benchmark-index-sqlserver-after:
	cd indexing && python benchmark.py --engine sqlserver --label after

benchmark-index-report:
	cd indexing && python benchmark.py --report

# Real mechanism-level evidence per query (execution time / index usage / logical
# reads) instead of noisy wall-clock timing -- use this one, not --report, to judge
# whether a fix actually worked.
benchmark-index-evidence-report:
	cd indexing && python benchmark.py --evidence-report

benchmark-index-usage-stats:
	cd indexing && python benchmark.py --usage-stats

benchmark-index-duplicate-check:
	cd indexing && python benchmark.py --duplicate-check

# ============================================================================
# MODULE 3: Schema drift
# ============================================================================

# --scale must match whatever scale is already loaded (seed-small/medium/full above).
generate-debt-data:
	cd schema-drift && python generate_debt_data.py --scale full --out ../data

corrupt-drift-postgres:
	docker exec -i clinic_pg_old psql -U clinic -d clinic < schema-drift/corrupt_postgres.sql

corrupt-drift-mysql:
	docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < schema-drift/corrupt_mysql.sql

corrupt-drift-sqlserver:
	MSYS_NO_PATHCONV=1 docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2017' < schema-drift/corrupt_sqlserver.sql

# Bulk-loads addresses.csv/customer_notes.csv, populates orders.shipping_address, drops staging table.
load-debt-postgres:
	cd schema-drift && python load_debt_data.py --engine postgres --data-dir ../data

load-debt-mysql:
	cd schema-drift && python load_debt_data.py --engine mysql --data-dir ../data

load-debt-sqlserver:
	cd schema-drift && python load_debt_data.py --engine sqlserver --container-data-dir /data

validate-drift-postgres:
	cd schema-drift && python validate.py --engine postgres

validate-drift-mysql:
	cd schema-drift && python validate.py --engine mysql

validate-drift-sqlserver:
	cd schema-drift && python validate.py --engine sqlserver

migrate-drift-postgres:
	cd schema-drift && python migrate.py --engine postgres

migrate-drift-mysql:
	cd schema-drift && python migrate.py --engine mysql

migrate-drift-sqlserver:
	cd schema-drift && python migrate.py --engine sqlserver

# NOTE: patch_customer_notes.py and patch_migrate.py are one-off historical patch
# scripts that rewrite migrate.py's source in place (regex-based function
# replacement) -- they were each run exactly once to apply a specific bugfix
# (the sargable orphan-check fix and the parse-once JSON mapping fix,
# respectively) and are not meant to be re-run against an already-patched
# migrate.py. Kept here for reference/history only, deliberately not wired up
# as live targets:
#   python schema-drift/patch_customer_notes.py
#   python schema-drift/patch_migrate.py

# ============================================================================
# MODULE 4: Zero-downtime migration
# ============================================================================

expand-zd-postgres:
	cd zero-downtime && python expand.py --engine postgres

expand-zd-mysql:
	cd zero-downtime && python expand.py --engine mysql

expand-zd-sqlserver:
	cd zero-downtime && python expand.py --engine sqlserver

# Default (--write-mode method): simulates a pre-cutover app writing the old
# `method` column. Run in a separate terminal / backgrounded with & while
# expand-zd-* or contract-zd-* runs, NOT before it.
writer-zd-postgres:
	cd zero-downtime && python concurrent_writer.py --engine postgres --duration 300 --log writer_log_postgres.csv

writer-zd-mysql:
	cd zero-downtime && python concurrent_writer.py --engine mysql --duration 300 --log writer_log_mysql.csv

writer-zd-sqlserver:
	cd zero-downtime && python concurrent_writer.py --engine sqlserver --duration 300 --log writer_log_sqlserver.csv

# --write-mode fk: simulates a post-cutover app (writes payment_method_id
# directly). Use THIS when testing the column drop specifically, not the
# default method mode, or every write will correctly-but-confusingly fail
# once `method` is gone.
writer-zd-postgres-fk:
	cd zero-downtime && python concurrent_writer.py --engine postgres --write-mode fk --duration 120 --log writer_log_postgres_drop.csv

writer-zd-mysql-fk:
	cd zero-downtime && python concurrent_writer.py --engine mysql --write-mode fk --duration 120 --log writer_log_mysql_drop.csv

writer-zd-sqlserver-fk:
	cd zero-downtime && python concurrent_writer.py --engine sqlserver --write-mode fk --duration 120 --log writer_log_sqlserver_drop.csv

# Full contract: adds FK, validates, sets NOT NULL, relaxes + drops the old column.
contract-zd-postgres:
	cd zero-downtime && python contract.py --engine postgres

contract-zd-mysql:
	cd zero-downtime && python contract.py --engine mysql

contract-zd-sqlserver:
	cd zero-downtime && python contract.py --engine sqlserver

# --skip-drop: FK + validate + NOT NULL only, leaves the old `method` column in
# place -- use this to isolate those steps' concurrency behavior from the
# (much more disruptive) column drop.
contract-zd-postgres-skip-drop:
	cd zero-downtime && python contract.py --engine postgres --skip-drop

contract-zd-mysql-skip-drop:
	cd zero-downtime && python contract.py --engine mysql --skip-drop

contract-zd-sqlserver-skip-drop:
	cd zero-downtime && python contract.py --engine sqlserver --skip-drop

validate-zd-postgres:
	cd zero-downtime && python validate.py --engine postgres

validate-zd-mysql:
	cd zero-downtime && python validate.py --engine mysql

validate-zd-sqlserver:
	cd zero-downtime && python validate.py --engine sqlserver

# ============================================================================
# MODULE 5: Deprecated version migration (needs up-new-* containers running too)
# ============================================================================

migrate-version-postgres:
	cd version-migration && python migrate.py --engine postgres

migrate-version-mysql:
	cd version-migration && python migrate.py --engine mysql

# SQL Server is two explicit phases -- phase 1 prints the real logical file
# names from RESTORE FILELISTONLY, phase 2 needs those passed in, not guessed.
migrate-version-sqlserver-backup:
	cd version-migration && python migrate.py --engine sqlserver --phase backup

# Override DATA_NAME/LOG_NAME with the values phase 1 actually printed, e.g.:
#   make migrate-version-sqlserver-restore DATA_NAME=clinic LOG_NAME=clinic_log
DATA_NAME ?= clinic
LOG_NAME ?= clinic_log
migrate-version-sqlserver-restore:
	cd version-migration && python migrate.py --engine sqlserver --phase restore --data-name $(DATA_NAME) --log-name $(LOG_NAME)

# The strongest check in this module -- re-runs one real verification from
# EACH earlier module (1-4) against the migrated *_new instance.
capstone-validate-postgres:
	cd version-migration && python capstone_validate.py --engine postgres

capstone-validate-mysql:
	cd version-migration && python capstone_validate.py --engine mysql

capstone-validate-sqlserver:
	cd version-migration && python capstone_validate.py --engine sqlserver

# The six reframed breaking/deprecated-behavior probes, run against *_new.
deprecated-probes-postgres:
	cd version-migration && python deprecated_probes.py --engine postgres

deprecated-probes-mysql:
	cd version-migration && python deprecated_probes.py --engine mysql

deprecated-probes-sqlserver:
	cd version-migration && python deprecated_probes.py --engine sqlserver

# ============================================================================
# MODULE 6: Cross-engine comparison (Oracle, MongoDB)
# ============================================================================
# Write-up only, by design -- no local build, no commands. See
# cross-engine-comparison/README.md.