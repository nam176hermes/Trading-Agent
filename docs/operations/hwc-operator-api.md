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

The dashboard server consumes the Operator API through the frozen loopback
client variables:

```text
TRADING_OPERATOR_API_URL=http://127.0.0.1:8402
TRADING_OPERATOR_API_WEB_TOKEN_FILE
```

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

## Independent CLI

Install the root project from its lockfile, then use `trading-agent` without a
dashboard process. The three URLs default to their literal loopback ports and
may be changed only to another literal `http://127.0.0.1:<port>` endpoint:

```text
TRADING_CONTROL_API_URL=http://127.0.0.1:8400
TRADING_JOB_API_URL=http://127.0.0.1:8401
TRADING_OPERATOR_API_URL=http://127.0.0.1:8402
TRADING_JOB_API_TOKEN_FILE
TRADING_OPERATOR_API_CLI_TOKEN_FILE
```

Both tokens are read from descriptor-safe protected files. The Operator API
CLI file may point to the same protected credential as the service's
`OPERATOR_API_CLI_TOKEN_FILE`; there is no token command-line flag or raw token
environment variable. The CLI does not print either credential.

```bash
trading-agent status
trading-agent capabilities
trading-agent jobs list --limit 50 --offset 0
trading-agent jobs show JOB_ID
trading-agent jobs cancel JOB_ID
trading-agent mode paper --idempotency-key OPERATOR_SELECTED_KEY
trading-agent kill-switch status
trading-agent kill-switch activate --reason "operator safety action" --idempotency-key OPERATOR_SELECTED_KEY
trading-agent kill-switch clear --idempotency-key OPERATOR_SELECTED_KEY
```

Every invocation generates one correlation ID. Every Operator API command also
generates one command ID. Mutation requests are serialized canonically and are
never retried automatically. Retrying an ambiguous outcome is an explicit
operator action and must reuse the same idempotency key; the API journal is the
outcome authority. Clear first reads raw Operator API state, requires an active
kill switch, and submits the exact observed `state_sha256`.

Exit codes are stable: `0` success, `2` command usage or local configuration,
`3` authentication or authorization failure, `4` upstream unavailable, `5`
conflict or unsafe command state, and `6` protocol or internal failure.
Standard output contains JSON only on success. Standard error contains a
sanitized JSON error code and never includes tokens, paths, request bodies,
reasons, or receipts.

## Portable qualification receipt

The protected-main Foundation workflow publishes the HWC evidence and signs
the exact portable receipt with GitHub artifact attestation. A later governance
import may set `HWC_PORTABLE_QUALIFIED=PASS` only while `gh attestation verify`
confirms the receipt was produced by the exact Foundation workflow, source SHA,
and `refs/heads/main` on a GitHub-hosted runner. A locally authored or merely
self-digested receipt remains `HELD`.
