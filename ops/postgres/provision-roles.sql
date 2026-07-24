\set ON_ERROR_STOP on

-- Supply these psql variables from a protected local process. Never place
-- literal passwords in this file or on a command line retained in evidence.
SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading_owner')
  AS owner_exists \gset
\if :owner_exists
ALTER ROLE trading_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION PASSWORD :'owner_password';
\else
CREATE ROLE trading_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION PASSWORD :'owner_password';
\endif

SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading_migrator')
  AS migrator_exists \gset
\if :migrator_exists
ALTER ROLE trading_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION PASSWORD :'migrator_password';
\else
CREATE ROLE trading_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION PASSWORD :'migrator_password';
\endif

SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading_reader')
  AS reader_exists \gset
\if :reader_exists
ALTER ROLE trading_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION PASSWORD :'reader_password';
\else
CREATE ROLE trading_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION PASSWORD :'reader_password';
\endif

SELECT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trading_jobs')
  AS jobs_exists \gset
\if :jobs_exists
ALTER ROLE trading_jobs LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'jobs_password';
\else
CREATE ROLE trading_jobs LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD :'jobs_password';
\endif

SELECT EXISTS (SELECT FROM pg_database WHERE datname = 'trading_agent')
  AS database_exists \gset
\if :database_exists
ALTER DATABASE trading_agent OWNER TO trading_owner;
\else
CREATE DATABASE trading_agent OWNER trading_owner;
\endif
\connect trading_agent

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO trading_owner;
GRANT CONNECT ON DATABASE trading_agent
  TO trading_owner, trading_migrator, trading_reader, trading_jobs;
GRANT USAGE ON SCHEMA public TO trading_migrator, trading_reader;
GRANT USAGE ON SCHEMA public TO trading_jobs;

ALTER ROLE trading_reader IN DATABASE trading_agent
  SET default_transaction_read_only = on;
ALTER ROLE trading_owner IN DATABASE trading_agent SET timezone = 'UTC';
ALTER ROLE trading_migrator IN DATABASE trading_agent SET timezone = 'UTC';
ALTER ROLE trading_reader IN DATABASE trading_agent SET timezone = 'UTC';
ALTER ROLE trading_jobs IN DATABASE trading_agent SET timezone = 'UTC';
