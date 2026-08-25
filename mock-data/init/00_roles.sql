-- ---------------------------------------------------------------------------
-- DEVELOPMENT ONLY.
--
-- Creates the same SELECT-only role the production deployment uses, so local
-- development exercises the real permission model rather than connecting as an
-- owner. If a query works locally, it works with production privileges.
--
-- Runs automatically on first start of the mock-ops-db container.
-- ---------------------------------------------------------------------------

CREATE ROLE bi_readonly LOGIN PASSWORD 'bi_readonly_dev_password';

GRANT CONNECT ON DATABASE decoinks_mock TO bi_readonly;
GRANT USAGE  ON SCHEMA public TO bi_readonly;

-- The seeder creates the tables after this script runs, so default privileges
-- are what actually grant access to them.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_readonly;

-- Mirrors the production role exactly (ARCHITECTURE.md, section D/L1).
ALTER ROLE bi_readonly SET default_transaction_read_only = on;
ALTER ROLE bi_readonly SET statement_timeout = '30s';
ALTER ROLE bi_readonly SET idle_in_transaction_session_timeout = '10s';
