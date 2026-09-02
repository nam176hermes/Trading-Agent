# HWC Operator API

The source contract reserves a loopback-only Operator API at `127.0.0.1:8402`.
Deployment and service activation remain held.

## Required runtime configuration

The service has no credential defaults. All four variables are required before
readiness can succeed:

```text
OPERATOR_API_WEB_TOKEN_FILE
OPERATOR_API_WEB_PRINCIPAL_ID
OPERATOR_API_CLI_TOKEN_FILE
OPERATOR_API_CLI_PRINCIPAL_ID
```

Each token path must be normalized and absolute with safe, non-symlinked
ancestors. The file must be an effective-UID-owned regular file with no
group/other permissions, contain one visible ASCII token of 32–4096 bytes, and
have at most one final newline. WEB and CLI token values and principal IDs must
be distinct. Principal IDs match `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`.

Unsafe or missing credential authority keeps readiness unavailable. Errors and
logs never include a token or its absolute path. The credential match alone
sets the immutable `WEB` or `CLI` interface identity; request headers and bodies
cannot override it.

This source boundary grants no broker, network-trading, live, production,
deployment, or Release Authority v2 capability.

## HTTP contract

The API exposes exactly four routes: public `GET /health/live` and
`GET /health/ready`, CLI-only `GET /v1/state`, and authenticated
`POST /v1/commands`. WEB credentials may only activate the kill switch; CLI
credentials may also read raw state, set PAPER mode, and clear the kill switch
under the protected safety checks.

Request bodies are capped at 8 KiB before JSON parsing. Responses are
non-cacheable JSON with `nosniff` and a trace ID. Request logs contain only the
trace ID, method, normalized endpoint, status, duration, and authenticated
principal ID; command bodies, reasons, tokens, paths, journal content, and
receipts are excluded.

The versioned OpenAPI and JSON Schema artifacts live under
`generated/operator-api/`. The dashboard consumer type is generated at
`apps/dashboard/src/generated/operator-api-types.ts`; it grants no browser
access to credentials or protected state.
