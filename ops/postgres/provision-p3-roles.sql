\set ON_ERROR_STOP on

-- Cluster-admin pre-step for the P3 migrations 0020-0032.
--
-- Creates exactly the three roles those migrations' preflights require, with
-- the attributes and membership they check: 0020 trading_p3_owner (NOLOGIN,
-- granted to trading_owner), 0021 trading_p3_authority (LOGIN, no membership)
-- and 0027 trading_p3_custodian (LOGIN NOINHERIT, no membership). The
-- statements are the P3 fixtures' own bootstrap statements. No password,
-- database grant or other privilege is issued here.

CREATE ROLE trading_p3_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT trading_p3_owner TO trading_owner;
CREATE ROLE trading_p3_authority LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE trading_p3_custodian LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
