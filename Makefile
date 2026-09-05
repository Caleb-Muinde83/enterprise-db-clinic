.PHONY: up-postgres up-mysql up-sqlserver down-all seed-small seed-medium seed-full

up-postgres:
	cd environments/postgres && docker compose up -d postgres_old

up-mysql:
	cd environments/mysql && docker compose up -d mysql_old

up-sqlserver:
	cd environments/sqlserver && docker compose up -d sqlserver_old

down-all:
	cd environments/postgres && docker compose down
	cd environments/mysql && docker compose down
	cd environments/sqlserver && docker compose down

seed-small:
	cd seed && python generate_data.py --scale small --out ../data

seed-medium:
	cd seed && python generate_data.py --scale medium --out ../data

seed-full:
	cd seed && python generate_data.py --scale full --out ../data
