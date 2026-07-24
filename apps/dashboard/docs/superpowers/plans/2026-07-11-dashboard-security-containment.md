# Dashboard Security Containment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the keys API shell injection and put every `/api/trading/**` endpoint behind a server-verified, rate-limited, role-separated session without storing the dashboard password in browser storage.

**Architecture:** Keep the deployed Next.js application self-contained and dependency-free. A signed HMAC session cookie carries the authenticated role, `src/proxy.ts` enforces the common API boundary, and mutation handlers repeat role checks for defense in depth. Python key-management calls use a constant program and JSON over stdin through `spawn`, never a shell or credential-bearing argv.

**Tech Stack:** Next.js 16 `proxy.ts`, Node.js 22 crypto/child_process, TypeScript, Node test runner.

## Global Constraints

- Keep requested/effective execution mode `paper / paper`.
- Keep `LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false`.
- Do not call an exchange or broker, submit/cancel an order, restart a service, change port 3002, or change Cloudflare during implementation.
- Do not log, commit, return, or place credentials in process arguments.
- Do not add a production dependency.
- Every behavior change follows red-green-refactor and receives a behavioral test.
- `reader` may perform read-only API calls; `operator` may perform approved paper/research mutations; `admin` is required for key, service, mode, and kill-switch operations.
- Legacy `TRADING_DASHBOARD_PASSWORD` grants only `reader`; elevated access requires separate `TRADING_DASHBOARD_OPERATOR_PASSWORD` or `TRADING_DASHBOARD_ADMIN_PASSWORD`.
- Session signing requires a distinct `TRADING_DASHBOARD_SESSION_SECRET` of at least 32 characters.
- Authentication failure is fail-closed and emits no credential value.

---

### Task 1: Shell-free Python bridge for key management

**Files:**
- Create: `src/lib/trading/python-bridge.ts`
- Modify: `src/app/api/trading/keys/route.ts`
- Test: `tests/keys-python-bridge.test.mjs`

**Interfaces:**
- Produces: `runPythonJson<T>(program: string, input: unknown, options: PythonBridgeOptions): Promise<T>`.
- The bridge uses `.venv/bin/python`, `['-c', program]`, `shell: false`, JSON stdin, a bounded output buffer, and a timeout.

- [ ] **Step 1: Write failing bridge tests**

Add tests that import `runPythonJson` and use `process.execPath` with constant JavaScript programs to prove:

```js
const payload = { value: "$(touch /tmp/must-not-exist) `id` ' \" \\n" };
const result = await runPythonJson(
  'process.stdin.once("data", b => process.stdout.write(b))',
  payload,
  { executable: process.execPath, args: ['-e'], cwd: process.cwd(), timeoutMs: 1000 },
);
assert.deepEqual(result, payload);
```

Also test non-zero exit, timeout, invalid JSON output, and output larger than 64 KiB.

- [ ] **Step 2: Verify RED**

Run: `node --test tests/keys-python-bridge.test.mjs`

Expected: FAIL because `src/lib/trading/python-bridge.ts` does not exist.

- [ ] **Step 3: Implement the bridge**

Implement one `spawn()` wrapper. It must never set `shell: true`, interpolate input into the program, or copy secret input into an error. Resolve with parsed JSON only after exit code zero; otherwise reject using stable codes `PYTHON_TIMEOUT`, `PYTHON_OUTPUT_LIMIT`, `PYTHON_INVALID_OUTPUT`, or `PYTHON_FAILED`.

- [ ] **Step 4: Verify GREEN**

Run: `node --test tests/keys-python-bridge.test.mjs`

Expected: all bridge tests pass and `/tmp/must-not-exist` is absent.

- [ ] **Step 5: Write failing keys-route source safety test**

Assert that the route contains no `exec`, `execAsync`, `promisify`, `shellEscape`, or template interpolation of `apiKey`, `secret`, and `password`; assert it imports `runPythonJson`.

- [ ] **Step 6: Verify RED**

Run: `node --test tests/keys-python-bridge.test.mjs`

Expected: FAIL on the current `execAsync` implementation.

- [ ] **Step 7: Refactor keys route**

Use constant Python programs that read one JSON object from stdin. Pass `TRADING_MASTER_KEY` through the child environment, preserving only the minimum inherited environment required to start Python. Return structured JSON from Python and expose stable sanitized API errors. Do not execute the connectivity-test branch in tests.

- [ ] **Step 8: Verify and commit**

Run:

```bash
node --test tests/keys-python-bridge.test.mjs
npx tsc --noEmit
npx eslint src/lib/trading/python-bridge.ts src/app/api/trading/keys/route.ts tests/keys-python-bridge.test.mjs
```

Commit: `security: remove shell execution from keys API`

---

### Task 2: Signed sessions, roles, and route access policy

**Files:**
- Create: `src/lib/trading/session.ts`
- Create: `src/lib/trading/access-policy.ts`
- Test: `tests/session-policy.test.mjs`

**Interfaces:**
- Produces: `SessionRole = 'reader' | 'operator' | 'admin'`.
- Produces: `issueSession(role, now?)`, `verifySession(token, now?)`, `authenticatePassword(password, env)`, and `requiredRole(pathname, method)`.
- Session tokens are versioned, HMAC-SHA256 signed, base64url encoded, and expire after eight hours.

- [ ] **Step 1: Write failing session tests**

Cover valid tokens, tampering, expiration, malformed payloads, missing/short signing secret, and role ordering. Verify the token payload contains no password or configured secret.

- [ ] **Step 2: Verify RED**

Run: `node --test tests/session-policy.test.mjs`

Expected: FAIL because session modules do not exist.

- [ ] **Step 3: Implement minimal signed sessions**

Use `createHmac`, `timingSafeEqual`, and stable JSON fields `{v, role, iat, exp}`. Reject tokens whose role is not allowlisted or whose timestamps are invalid.

- [ ] **Step 4: Verify GREEN**

Run: `node --test tests/session-policy.test.mjs`

- [ ] **Step 5: Write failing role-policy tests**

The expected matrix is:

```text
GET/HEAD /api/trading/**                         reader
any method /api/trading/keys                    admin
POST /api/trading/service|mode|kill-switch      admin
POST /api/trading/run|close-position|update-stop operator
POST /api/trading/plan|watchlist                 operator
all other non-read methods                       admin
```

Verify admin satisfies operator/reader, operator satisfies reader, and reader cannot mutate.

- [ ] **Step 6: Implement policy and password-to-role authentication**

Match passwords in constant time. Evaluate admin, then operator, then legacy reader. Reject duplicate configured passwords as `CONFIGURATION_ERROR` so privilege separation cannot silently collapse.

- [ ] **Step 7: Verify and commit**

Run:

```bash
node --test tests/session-policy.test.mjs
npx tsc --noEmit
npx eslint src/lib/trading/session.ts src/lib/trading/access-policy.ts tests/session-policy.test.mjs
```

Commit: `security: add signed role-separated dashboard sessions`

---

### Task 3: Rate-limited login and browser session flow

**Files:**
- Create: `src/lib/trading/login-rate-limit.ts`
- Create: `src/app/api/auth/session/route.ts`
- Modify: `src/components/trading/auth-guard.tsx`
- Test: `tests/session-login.test.mjs`
- Test: `tests/browser-auth-source.test.mjs`

**Interfaces:**
- Produces: `checkLoginAttempt(key, now)` and `recordLoginFailure(key, now)`.
- `POST /api/auth/session` accepts `{password}`, sets `trading_session` as `HttpOnly`, `Secure` in production, `SameSite=Strict`, path `/`, and max age eight hours.
- `GET /api/auth/session` reports only `{authenticated, role?}`.
- `DELETE /api/auth/session` clears the cookie.

Security supersession: a successful login must not clear an IP's failure
history. Otherwise a reader credential can reset failed operator/admin guesses
and bypass privilege-separated throttling.

- [ ] **Step 1: Write failing rate-limit tests**

Cover five failures in fifteen minutes, `Retry-After`, window expiry, bounded
stale-entry cleanup, and repeated `4 wrong + reader success` cycles that must
still reach the block.

- [ ] **Step 2: Verify RED**

Run: `node --test tests/session-login.test.mjs`

- [ ] **Step 3: Implement rate limiter and session route**

Key attempts by `cf-connecting-ip`, then the first `x-forwarded-for` address, then `unknown`. Return 429 without credential comparison while blocked. Never return which role password matched.

- [ ] **Step 4: Verify GREEN**

Run: `node --test tests/session-login.test.mjs`

- [ ] **Step 5: Write failing browser-source tests**

Assert `auth-guard.tsx` contains no `localStorage`, `sessionStorage`, `Authorization`, `Bearer`, or password storage key. Assert it calls `/api/auth/session` with `GET` for status and `POST` for login.

- [ ] **Step 6: Verify RED and refactor AuthGuard**

Run the test, confirm it fails on `localStorage`, then update the component to rely on the cookie session. Keep fail-closed behavior on timeout/network errors.

- [ ] **Step 7: Verify and commit**

Run:

```bash
node --test tests/session-login.test.mjs tests/browser-auth-source.test.mjs
npx tsc --noEmit
npx eslint src/lib/trading/login-rate-limit.ts src/app/api/auth/session/route.ts src/components/trading/auth-guard.tsx tests/session-login.test.mjs tests/browser-auth-source.test.mjs
```

Commit: `security: replace browser password storage with sessions`

---

### Task 4: Central API boundary and defense-in-depth mutation authorization

**Files:**
- Create: `src/proxy.ts`
- Modify: `src/lib/trading/auth.ts`
- Modify: all nine mutation route handlers under `src/app/api/trading/`
- Test: `tests/api-access-boundary.test.mjs`
- Test: `tests/mutation-policy.test.mjs`

**Interfaces:**
- Proxy matcher is exactly `/api/trading/:path*`.
- Proxy returns structured 401/403/503 JSON and forwards authorized requests.
- `authorizeMutation(request, action, classification, requiredRole)` verifies the signed cookie again and audits only mutation attempts.

- [ ] **Step 1: Write failing proxy tests**

Use Next.js experimental proxy test helpers when available, otherwise call the exported `proxy()` with `NextRequest`. Cover missing cookie, invalid cookie, reader GET, reader POST denial, operator run permission, operator keys denial, and admin keys permission.

- [ ] **Step 2: Verify RED**

Run: `node --test tests/api-access-boundary.test.mjs`

Expected: FAIL because `src/proxy.ts` does not exist.

- [ ] **Step 3: Implement proxy**

Use `requiredRole()` and `verifySession()`. Add same-origin validation for non-GET/HEAD requests using `Origin` versus `request.nextUrl.origin`; reject absent or mismatched origin with 403 outside explicit non-browser test mode.

- [ ] **Step 4: Verify GREEN**

Run: `node --test tests/api-access-boundary.test.mjs`

- [ ] **Step 5: Write failing defense-in-depth tests**

Update the mutation test to require an explicit role argument for every mutation route. Verify `checkAuth()` is pure and does not append mutation audit events.

- [ ] **Step 6: Refactor mutation authorization**

Map keys/service/mode/kill-switch to `admin`; run/close/update-stop/plan/watchlist to `operator`. Preserve the existing classification in audit events and add the authenticated role, never the token.

- [ ] **Step 7: Verify and commit**

Run:

```bash
npm test
npx tsc --noEmit
npx eslint src/proxy.ts src/lib/trading/auth.ts src/app/api/trading/keys/route.ts src/app/api/trading/service/route.ts src/app/api/trading/mode/route.ts src/app/api/trading/kill-switch/route.ts src/app/api/trading/run/route.ts src/app/api/trading/close-position/route.ts src/app/api/trading/update-stop/route.ts src/app/api/trading/plan/route.ts src/app/api/trading/watchlist/route.ts tests/api-access-boundary.test.mjs tests/mutation-policy.test.mjs
```

Commit: `security: enforce session roles across trading API`

---

### Task 5: Isolated HTTP security smoke and deployment documentation

**Files:**
- Create: `tests/dashboard-security.integration.sh`
- Modify: `package.json`
- Create: `docs/operations/dashboard-security.md`
- Test: `tests/dashboard-security.integration.sh`

**Interfaces:**
- The integration test starts a built dashboard on an ephemeral localhost port with fixture secrets and never points at the live backend.
- It proves unauthenticated trading GET is 401, login sets an HttpOnly/SameSite cookie, reader GET succeeds, reader mutation is 403, and invalid-login rate limiting reaches 429.

- [ ] **Step 1: Write the integration test and verify RED**

Run: `bash tests/dashboard-security.integration.sh`

Expected: FAIL before the proxy/session route is present or before the test is added to `npm test`.

- [ ] **Step 2: Make the isolated smoke pass**

Use a temporary cookie jar and fixture environment values. Bind only `127.0.0.1`; ensure cleanup removes the process and temporary files on every exit.

- [ ] **Step 3: Document deployment requirements**

Document generation and protected placement of the three passwords/session secret, the role matrix, Cloudflare Access as an outer control, rollback to the previous dashboard build, and the fact that no elevated mutation works until explicit operator/admin secrets are configured.

- [ ] **Step 4: Full verification**

Run:

```bash
npm test
npx tsc --noEmit
npm run build
npm audit --omit=dev
```

Confirm the live service PID, port 3002, Cloudflare PID, paper mode, false gates, inactive kill switch, and 30/0 order/trade counts remain unchanged.

- [ ] **Step 5: Commit**

Commit: `test: verify dashboard security boundary end to end`
