#!/usr/bin/env bash
# Loads the three demo data sources. Run after `docker compose up -d`.
#   PostgreSQL : Olist e-commerce   (needs CSVs in data/olist/, from Kaggle)
#   MySQL      : Chinook music store (downloaded automatically)
#   SQLite     : Northwind trading   (downloaded automatically -> data/northwind.db)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

CHINOOK_URL="https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_MySql.sql"
NORTHWIND_URL="https://github.com/jpwhite3/northwind-SQLite3/raw/main/dist/northwind.db"

# ---------- 1. Olist -> PostgreSQL ----------
if ! ls data/olist/olist_orders_dataset.csv >/dev/null 2>&1; then
  echo "ERROR: Olist CSVs not found in data/olist/."
  echo "Download https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and unzip into data/olist/"
  exit 1
fi
echo ">> Loading Olist into PostgreSQL (about 1 minute)..."
docker compose exec -T postgres psql -q -U "${POSTGRES_USER:-admin}" -d "${POSTGRES_DB:-olist}" \
  -f /scripts/seed/olist_postgres.sql

# ---------- 2. Chinook -> MySQL ----------
mkdir -p data/chinook
[ -f data/chinook/Chinook_MySql.sql ] || curl -sSLf -o data/chinook/Chinook_MySql.sql "$CHINOOK_URL"
echo ">> Loading Chinook into MySQL..."
# Strip the script's own DROP/CREATE DATABASE + USE so it loads into our database.
grep -vE '^(DROP DATABASE|CREATE DATABASE|USE )' data/chinook/Chinook_MySql.sql \
  | docker compose exec -T mysql sh -c \
    'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" --default-character-set=utf8mb4 "$MYSQL_DATABASE" 2>&1 | grep -v "Using a password" || true'

# ---------- 3. Northwind -> SQLite file ----------
[ -f data/northwind.db ] || curl -sSLf -o data/northwind.db "$NORTHWIND_URL"
echo ">> Northwind SQLite file ready at data/northwind.db"

echo ">> Done. Row counts:"
docker compose exec -T postgres psql -U reader -d "${POSTGRES_DB:-olist}" -tAc \
  "SELECT 'postgres.orders', count(*) FROM orders UNION ALL SELECT 'postgres.order_items', count(*) FROM order_items"
docker compose exec -T mysql sh -c \
  'mysql -ureader -p"$MYSQL_READONLY_PASSWORD" "$MYSQL_DATABASE" -N -e "SELECT \"mysql.Track\", COUNT(*) FROM Track" 2>/dev/null'
