#!/bin/bash
# Creates a read-only role used by the app. Tables loaded later by the admin
# user are automatically readable (ALTER DEFAULT PRIVILEGES).
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE ROLE reader WITH LOGIN PASSWORD '${POSTGRES_READONLY_PASSWORD}';
  ALTER ROLE reader SET default_transaction_read_only = on;
  ALTER ROLE reader SET statement_timeout = '15s';
  GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO reader;
  GRANT USAGE ON SCHEMA public TO reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader;
  ALTER DEFAULT PRIVILEGES FOR ROLE "${POSTGRES_USER}" IN SCHEMA public GRANT SELECT ON TABLES TO reader;
EOSQL
