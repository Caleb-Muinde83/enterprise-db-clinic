.PHONY: up-postgres up-mysql up-sqlserver down-all seed-small seed-medium seed-full \
        apply-schema-postgres apply-schema-mysql apply-schema-sqlserver

up-postgres:
	cd environments/postgres && docker compose up -d --wait postgres_old

up-mysql:
	cd environments/mysql && docker compose up -d --wait mysql_old

up-sqlserver:
	cd environments/sqlserver && docker compose up -d --wait sqlserver_old

down-all:
	cd environments/postgres && docker compose down
	cd environments/mysql && docker compose down
	cd environments/sqlserver && docker compose down

# These run the DDL through the container's own client — no local psql/mysql/sqlcmd needed.
apply-schema-postgres:
	docker exec -i clinic_pg_old psql -U clinic -d clinic < seed/schema/postgres_schema.sql

apply-schema-mysql:
	docker exec -i clinic_mysql_old mysql -u clinic -pclinic clinic < seed/schema/mysql_schema.sql

apply-schema-sqlserver:
	docker exec -i clinic_mssql_old /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P 'Clinic!2016' < seed/schema/sqlserver_schema.sql

seed-small:
	cd seed && python generate_data.py --scale small --out ../data

seed-medium:
	cd seed && python generate_data.py --scale medium --out ../data

seed-full:
	cd seed && python generate_data.py --scale full --out ../data
