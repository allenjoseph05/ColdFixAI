#!/bin/sh
# Runs once, on first container start, before Postgres accepts external
# connections. If you change this file you must `docker compose down -v` for it
# to run again — the initdb hook is skipped when the data volume already exists.
set -eu

for db in spike_a spike_b spike_c; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
        CREATE DATABASE $db;
        GRANT ALL PRIVILEGES ON DATABASE $db TO $POSTGRES_USER;
SQL
    echo "created database $db"
done
