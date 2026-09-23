#!/bin/bash
# Creates a separate database (with PostGIS) for the integration test suite.
set -euo pipefail

TEST_DB="${POSTGRES_TEST_DB:-expert_listing_test}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	SELECT 'CREATE DATABASE "${TEST_DB}"'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${TEST_DB}')\gexec
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$TEST_DB" <<-EOSQL
	CREATE EXTENSION IF NOT EXISTS postgis;
EOSQL
