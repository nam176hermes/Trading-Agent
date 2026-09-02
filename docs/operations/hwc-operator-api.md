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
