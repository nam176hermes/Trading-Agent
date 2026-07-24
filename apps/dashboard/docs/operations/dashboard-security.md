# Dashboard security deployment

The dashboard authentication boundary is an application control. Keep Cloudflare
Access in front of the dashboard as an independent outer identity and network
control; it does not replace the dashboard session, role checks, or same-origin
mutation checks.

## Required secrets

Generate four independent, high-entropy values on the deployment host. Do not
reuse a value between roles or environments, put values in shell history, or
commit them to the repository.

```bash
umask 077
openssl rand -base64 48 # reader password
openssl rand -base64 48 # operator password
openssl rand -base64 48 # admin password
openssl rand -base64 48 # session signing secret
```

Place the resulting values in the dashboard service's protected environment
file or secret manager, readable only by the service account (for a file, mode
`0600`), using these names:

```text
TRADING_DASHBOARD_PASSWORD=<independent reader password>
TRADING_DASHBOARD_OPERATOR_PASSWORD=<independent operator password>
TRADING_DASHBOARD_ADMIN_PASSWORD=<independent admin password>
TRADING_DASHBOARD_SESSION_SECRET=<independent signing secret of at least 32 characters>
TRADING_DASHBOARD_TRUSTED_PROXY_SECRET=<independent proxy attribution secret>
```

Restarting after a session-secret rotation invalidates all existing dashboard
sessions. Never print the environment file during deployment or troubleshooting.
Reject a deployment if any two role passwords are equal.

Operator and admin access are deliberately disabled until their explicit secrets
are configured. The reader password does not inherit elevated mutation rights,
and the legacy reader setting must never be copied into either elevated variable.
If all role passwords are absent, login fails closed with a configuration error.

## Role matrix

| Role | Read trading APIs | Operator mutations | Admin mutations and key management |
| --- | --- | --- | --- |
| Reader | Allowed | Denied | Denied |
| Operator | Allowed | Allowed | Denied |
| Admin | Allowed | Allowed | Allowed |

Operator mutations include pipeline execution, close/update-position requests,
plans, and watchlist changes. Admin-only operations include service control, mode
changes, kill-switch changes, and all key-management requests. Every browser
mutation must also carry the exact dashboard origin.

## Deployment controls

The login limiter is process-local: its failure history resets on a process
restart, and independent replicas multiply the effective login allowance. This
containment therefore requires a single-process deployment. A shared rate
limiter must replace it before horizontal scaling or multi-process operation.

1. Build the candidate with `npm ci`, `npm test`, `npx tsc --noEmit`, and
   `npm run build` in an isolated release directory.
2. Keep the application bound to loopback behind the existing reverse proxy.
   Do not expose the Next.js listener directly to the internet.
   Configure the proxy to replace `x-trusted-proxy-secret` with the protected
   `TRADING_DASHBOARD_TRUSTED_PROXY_SECRET` value on every request. Login fails
   closed in production when this attribution secret is not configured.
3. Configure Cloudflare Access for the dashboard hostname with least-privilege
   identity policies and MFA. Preserve the existing tunnel-to-loopback mapping.
4. Install the protected secret environment without logging its contents, then
   restart the dashboard service through the normal service manager.
5. Verify an unauthenticated trading request is rejected, reader access is
   read-only, elevated accounts match the matrix, and the existing paper-mode,
   approval-gate, and kill-switch invariants are unchanged.

Cloudflare Access is defense in depth: an Access bypass or policy mistake must
still encounter the dashboard's signed HttpOnly session and role boundary.

## Rollback

Keep the previous dashboard build/release directory intact until verification is
complete. To roll back, stop the candidate, repoint the service's release link or
unit working directory to the immediately previous build, restore its compatible
protected environment configuration, and restart through the service manager.
Verify the loopback health endpoint and Cloudflare-protected hostname after the
rollback. Do not weaken authentication, reuse reader credentials for an elevated
role, enable live-execution gates, or delete trading data to make a rollback pass.

If the session format or signing secret changed, expect users to log in again.
Retain deployment logs and the mutation authorization audit, but never retain
passwords, session cookies, signing secrets, or exchange credentials in logs.
