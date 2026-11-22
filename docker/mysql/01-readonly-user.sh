#!/bin/bash
# Creates a SELECT-only user for the app.
set -e
mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" <<-EOSQL
  CREATE USER IF NOT EXISTS 'reader'@'%' IDENTIFIED BY '${MYSQL_READONLY_PASSWORD}';
  GRANT SELECT ON \`${MYSQL_DATABASE}\`.* TO 'reader'@'%';
  FLUSH PRIVILEGES;
EOSQL
