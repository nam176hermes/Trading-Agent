# Trading Agent Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the canonical trading-agent monorepo to a reproducible,
observable, rollback-safe **paper-production** service, collect G0-G4 evidence,
and prepare—but never automatically enable—a separately approved live-limited
release.

**Architecture:** Keep the current strangler architecture. The legacy backend
remains research-only; canonical contracts, PostgreSQL, durable jobs, a new
deterministic risk service, and a single paper execution gateway become the
production path. Every promotion is evidence-driven and fail-closed. Live
broker/exchange adapters remain unreachable until a later ADR binds one exact
account, route, credential policy, and operator-approved risk envelope.

**Tech Stack:** Python 3.11, uv, Pydantic 2, FastAPI, PostgreSQL 16, Alembic,
Psycopg 3, cryptography/Ed25519, pytest, Next.js 16.2.x, React 19, TypeScript 5,
Node 22, npm lockfiles, systemd, Bash, JSON/SHA-256 release manifests.

## Global Constraints

- Canonical source root is `/home/thenam176/projects/trading-agent`; do not
  merge back into the three provenance repositories.
- Start from commit `e304d83da260d11120ac648d67882359645c68a5` on
  `codex/canonical-monorepo`; record a new commit at every green task.
- Requested/effective mode stays `paper/paper`; `LIVE_EXECUTION_ENABLED=false`
  and `LIVE_TRADING_APPROVED=false` through Tasks 0-13, including Task 2A.
- Never probe a provider key, exchange, broker, account, balance, position,
  order, or withdrawal endpoint during implementation or validation.
- Do not push, add a network remote, rewrite history, provision as root, change
  systemd, change Cloudflare, or mutate production until the task containing
  that action reaches its explicit operator-approval checkpoint.
- Preserve the three dependency authorities: root `uv.lock`, backend
  `legacy/research-backend/uv.lock`, and dashboard
  `apps/dashboard/package-lock.json`.
- Adding a dependency, changing CI, changing shared lint/type/test policy, and
  any root or production action require explicit approval immediately before
  execution, even though this plan defines the intended change.
- All behavior changes use RED-GREEN-REFACTOR. Do not weaken a test, safety
  invariant, owner/mode check, body limit, or contract to obtain a pass.
- Protected secrets, model manifests, release authority, database credentials,
  and signing keys stay outside Git with mode `0600` or stricter.
- Dashboard remains loopback-only behind Cloudflare Access and uses one process
  until a shared login limiter replaces the process-local limiter.
- Paper production requires every P0 finding closed, G0-G4 PASS, zero unresolved
  high/critical dependency findings, zero confirmed current-tree secrets, a
  restore drill, a rollback drill, and a clean immutable release attestation.
- Live-limited remains `NO-GO` unless Task 14 receives a separate explicit ADR
  approval after paper production has completed its observation window.

## Delivery graph

```text
Task 0 baseline
  -> Task 1 secret containment
  -> Task 2 mutation sink hardening
  -> Task 2A dashboard login boundary hardening
  -> Task 3 model artifact containment
  -> Task 4 hermetic PostgreSQL validation
  -> Task 5 CI and dependency gates
  -> Task 6 deterministic execution contracts/schema
  -> Task 7 paper execution gateway and reconciliation
  -> Task 8 model/research governance
  -> Task 9 observability and operational SLOs
  -> Task 10 G0-G3 evidence
  -> Task 11 G4 paper soak and drills
  -> Task 12 Release Authority v2
  -> Task 13 paper-production cutover
  -> Task 14 separately approved live-limited ADR and canary
  -> Task 15 final production audit
```

## Milestone gates

| Milestone | Tasks | Exit decision |
|---|---|---|
| M0 - Containment | 0, 1, 2, 2A, 3 | No current secret; every mutation sink and login/model boundary fails closed |
| M1 - Reproducible validation | 4, 5 | Full hermetic test/build/security CI is green from lockfiles |
| M2 - Paper control plane | 6, 7, 8, 9 | Signed PAPER plans, separate risk/execution services, reconciliation, governance, metrics |
| M3 - Quantitative evidence | 10 | G0-G3 PASS on immutable inputs and predefined policy |
| M4 - Operational evidence | 11 | G4 PASS after at least 30 unchanged days and 200 completed paper plans |
| M5 - Paper production | 12, 13, 15 | Release Authority v2, backup/restore, cutover/smoke/rollback, `GO_PAPER_PRODUCTION` |
| M6 - Live-limited | 14, then 15 again | Separate ADR plus two approvals and a bounded canary; otherwise remains `NO_GO` |

No calendar estimate may shorten M4's 30-day evidence window. Tasks 0-10 can
be implemented and reviewed before that window starts; Tasks 12-13 consume the
completed G4 evidence and therefore cannot be declared production-complete in
parallel with M4.

---

### Task 0: Freeze the promotion baseline and decision record

**Files:**
- Track: `docs/superpowers/plans/2026-07-14-production-readiness.md`
- Create: `docs/production/production-readiness-baseline.md`
- Create: `docs/production/promotion-status.json`
- Create: `scripts/capture_production_baseline.py`
- Test: `tests/production/test_capture_production_baseline.py`

**Interfaces:**
- Consumes: canonical Git identity, repository audit, generated contract state,
  and the fixed paper/live invariants.
- Produces: `promotion-status.json` with schema version 1 and an initial
  `NO_GO` decision; every later task updates evidence references, never the
  historical evidence files themselves.

- [ ] **Step 1: Write a failing baseline schema test.**

  ```python
  def test_baseline_is_exactly_bound_to_source_and_paper_only(tmp_path):
      from scripts.capture_production_baseline import build_baseline

      result = build_baseline(
          repo_root=ROOT,
          head="e304d83da260d11120ac648d67882359645c68a5",
          requested_mode="paper",
          effective_mode="paper",
          live_execution_enabled=False,
          live_trading_approved=False,
      )
      assert result["schema_version"] == 1
      assert result["source"]["head"] == "e304d83da260d11120ac648d67882359645c68a5"
      assert result["safety"] == {
          "requested_mode": "paper",
          "effective_mode": "paper",
          "live_execution_enabled": False,
          "live_trading_approved": False,
      }
      assert result["decision"] == "NO_GO"
  ```

- [ ] **Step 2: Run the test and prove RED.**

  Run:

  ```bash
  uv run pytest -q tests/production/test_capture_production_baseline.py
  ```

  Expected: FAIL because `scripts.capture_production_baseline` does not exist.

- [ ] **Step 3: Implement deterministic baseline capture.**

  ```python
  def build_baseline(
      *, repo_root: Path, head: str, requested_mode: str, effective_mode: str,
      live_execution_enabled: bool, live_trading_approved: bool,
  ) -> dict[str, object]:
      observed = subprocess.run(
          ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True,
          capture_output=True, text=True,
      ).stdout.strip()
      if observed != head:
          raise ValueError("source head changed during baseline capture")
      if (requested_mode, effective_mode) != ("paper", "paper"):
          raise ValueError("promotion baseline must remain paper-only")
      if live_execution_enabled or live_trading_approved:
          raise ValueError("live gates must be false")
      return {
          "schema_version": 1,
          "source": {"root": str(repo_root), "head": observed},
          "safety": {
              "requested_mode": requested_mode,
              "effective_mode": effective_mode,
              "live_execution_enabled": False,
              "live_trading_approved": False,
          },
          "decision": "NO_GO",
          "completed_gates": [],
      }
  ```

- [ ] **Step 4: Capture and review the baseline without reading provider or
  broker credentials.**

  Run:

  ```bash
  make audit
  make check-contracts
  uv run python scripts/capture_production_baseline.py \
    --root /home/thenam176/projects/trading-agent \
    --requested-mode paper --effective-mode paper \
    --live-execution-enabled false --live-trading-approved false \
    --output docs/production/promotion-status.json
  ```

  Expected: audits PASS and JSON decision is exactly `NO_GO`.

- [ ] **Step 5: Document scope and commit.**

  `production-readiness-baseline.md` must state that active runtime was not
  probed, list the initial blockers, and identify paper production as the only
  authorized target.

  ```bash
  git add docs/superpowers/plans/2026-07-14-production-readiness.md \
    scripts/capture_production_baseline.py tests/production \
    docs/production/production-readiness-baseline.md \
    docs/production/promotion-status.json
  git commit -m "docs: freeze production readiness baseline"
  ```

- [ ] **Step 6: Prove the committed baseline is release-audit clean.**

  ```bash
  make audit-release
  ```

  Expected: PASS on the clean Task 0 commit.

### Task 1: Remove tracked provider credentials and create publish-safe history

**Files:**
- Create: `legacy/research-backend/provider_config.py`
- Modify: `legacy/research-backend/fallback.py`
- Modify: `legacy/research-backend/news_collector.py`
- Modify: `legacy/research-backend/twelve_data.py`
- Create: `legacy/research-backend/tests/test_provider_config.py`
- Create: `.gitleaks.toml`
- Create: `ops/security/known-rotated-history.json`
- Create: `docs/security/provider-credential-rotation.md`
- Create: `scripts/verify_secret_hygiene.py`
- Test: `tests/security/test_secret_hygiene.py`

**Interfaces:**
- Produces: `provider_key(name: ProviderKey) -> str | None`; callers return
  typed unavailable/no-data behavior when the value is absent.
- Produces: redacted Gitleaks JSON in an external audit directory; no secret
  literal or environment dump may be written to Git.

- [ ] **Step 1: Revoke and rotate the four exposed keys outside Git.**

  Rotate Finnhub, Polygon, Marketaux, and Twelve Data through their provider
  consoles. Do not test the old values. Record only provider name, rotation
  timestamp, operator, and `ROTATED`/`REVOKED` status in
  `docs/security/provider-credential-rotation.md`.

  Expected: all four records are `ROTATED` or `REVOKED`; absence of any record
  stops the task.

- [ ] **Step 2: Write failing configuration and source-hygiene tests.**

  ```python
  def test_provider_key_is_absent_when_environment_is_missing(monkeypatch):
      monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
      assert provider_key(ProviderKey.FINNHUB) is None

  def test_provider_key_strips_nothing_and_rejects_padding(monkeypatch):
      monkeypatch.setenv("FINNHUB_API_KEY", " padded ")
      with pytest.raises(ProviderConfigurationError):
          provider_key(ProviderKey.FINNHUB)

  def test_tracked_provider_modules_contain_no_key_literals():
      for relative in ("fallback.py", "news_collector.py", "twelve_data.py"):
          source = (BACKEND / relative).read_text(encoding="utf-8")
          assert not re.search(r"(?m)^[A-Z0-9_]*(?:KEY|TOKEN)\s*=\s*['\"][^'\"]+", source)
  ```

- [ ] **Step 3: Implement one environment-only provider boundary.**

  ```python
  class ProviderKey(StrEnum):
      FINNHUB = "FINNHUB_API_KEY"
      POLYGON = "POLYGON_API_KEY"
      MARKETAUX = "MARKETAUX_API_KEY"
      TWELVE_DATA = "TWELVE_DATA_API_KEY"

  def provider_key(name: ProviderKey, env: Mapping[str, str] | None = None) -> str | None:
      values = os.environ if env is None else env
      value = values.get(name.value)
      if value is None:
          return None
      if not value or value != value.strip():
          raise ProviderConfigurationError(f"{name.value} is empty or padded")
      return value
  ```

  Replace each literal with a call made immediately before the request. A
  missing key must return the module's existing no-data result without placing
  the key in a URL, exception, log record, or report.

- [ ] **Step 4: Add Gitleaks policy and verify both current tree and history.**

  `.gitleaks.toml` may allow only exact test-fixture fingerprints and documented
  SHA-256 manifest fields. It must not allow the four provider files or a broad
  `generic-api-key` rule. `known-rotated-history.json` records only secret
  fingerprints, original commits/files, provider names, and rotation evidence;
  it never stores a secret value. The verifier rejects any historical finding
  outside that exact closed set and rejects every current-tree finding.

  Run:

  ```bash
  out="$HOME/.local/state/security-audits/trading-agent/$(git rev-parse HEAD)"
  tree="$out/current-tree"
  mkdir -p "$tree"
  git archive HEAD | tar -x -C "$tree"
  gitleaks detect --source . --no-banner --redact \
    --report-format json --report-path "$out/history.json" --exit-code 0
  gitleaks detect --source "$tree" --no-git --no-banner --redact \
    --report-format json --report-path "$out/current.json" --exit-code 0
  uv run python scripts/verify_secret_hygiene.py \
    --current "$out/current.json" --history "$out/history.json"
  ```

  Expected: zero unallowlisted current-tree findings. Historical findings are
  permitted only in the private canonical authority and keep publication
  blocked.

- [ ] **Step 5: Create a sanitized publication repository without rewriting
  `codex/canonical-monorepo`.**

  This step requires explicit approval because it creates new Git history.
  It does not add a remote or push.

  ```bash
  export PUB=/home/thenam176/projects/trading-agent-publication
  test ! -e "$PUB"
  mkdir -p "$PUB"
  git archive HEAD | tar -x -C "$PUB"
  git -C "$PUB" init --initial-branch=main
  git -C "$PUB" add -A
  git -C "$PUB" commit -m "chore: publish sanitized trading agent source"
  gitleaks detect --source "$PUB" --no-banner --redact
  ```

  Expected: Gitleaks exits `0`. Do not configure GitHub or push until a later
  explicit remote-mutation approval.

- [ ] **Step 6: Run component regression tests and commit.**

  ```bash
  cd legacy/research-backend
  uv run --frozen --extra test pytest -q tests/test_provider_config.py
  cd ../..
  uv run pytest -q tests/security/test_secret_hygiene.py
  git add legacy/research-backend .gitleaks.toml docs/security \
    ops/security/known-rotated-history.json scripts/verify_secret_hygiene.py \
    tests/security
  git commit -m "security: remove tracked provider credentials"
  ```

### Task 2: Enforce policy at every order, cancel, and close sink

**Files:**
- Modify: `legacy/research-backend/live_execution_policy.py`
- Modify: `legacy/research-backend/broker.py`
- Modify: `legacy/research-backend/exchange/adapter.py`
- Modify: `legacy/research-backend/exchange/ccxt_bridge.py`
- Modify: `legacy/research-backend/exchange/executor.py`
- Create: `legacy/research-backend/tests/test_execution_mutation_policy.py`
- Modify: `legacy/research-backend/tests/test_live_execution_policy.py`

**Interfaces:**
- Produces: `ExecutionOperation`, `ExecutionTarget`,
  `ExecutionAuthorization`, and `authorize_mutation()`.
- Every adapter mutation consumes an authorization whose operation and target
  exactly match the requested action; a mismatched or denied authorization
  raises before network access.

- [ ] **Step 1: Write tests that prove each live sink is unreachable without
  complete authorization.**

  ```python
  @pytest.mark.parametrize("operation", list(ExecutionOperation))
  def test_live_mutation_denies_missing_gate_and_never_calls_sink(operation):
      called = False
      def sink():
          nonlocal called
          called = True
      auth = authorize_mutation(
          operation, ExecutionTarget.LIVE,
          env={"LIVE_EXECUTION_ENABLED": "false", "LIVE_TRADING_APPROVED": "false"},
          kill_switch_reader=lambda: "INACTIVE",
      )
      with pytest.raises(ExecutionBlockedError, match="LIVE_EXECUTION_DISABLED"):
          require_authorized(auth, operation, ExecutionTarget.LIVE)
          sink()
      assert called is False

  def test_authorization_cannot_be_reused_for_another_operation():
      auth = authorize_paper_mutation(ExecutionOperation.PLACE)
      with pytest.raises(ExecutionBlockedError, match="AUTHORIZATION_MISMATCH"):
          require_authorized(auth, ExecutionOperation.CLOSE_ALL, ExecutionTarget.PAPER)
  ```

  Add separate monkeypatched tests for Alpaca close-one/close-all, CCXT cancel,
  `ExchangeAdapter.create_order`, `ExchangeAdapter.cancel_order`,
  `OrderExecutor.execute`, and `OrderExecutor.cancel_order`.

- [ ] **Step 2: Run the focused tests and prove current bypasses fail.**

  ```bash
  cd legacy/research-backend
  uv run --frozen --extra test pytest -q \
    tests/test_execution_mutation_policy.py tests/test_live_execution_policy.py
  ```

  Expected: failures show close/cancel/direct-executor sinks can currently reach
  their network adapter without the new authorization.

- [ ] **Step 3: Add closed operation/target authorization.**

  ```python
  class ExecutionOperation(StrEnum):
      PLACE = "PLACE"
      CANCEL = "CANCEL"
      CLOSE_POSITION = "CLOSE_POSITION"
      CLOSE_ALL = "CLOSE_ALL"

  class ExecutionTarget(StrEnum):
      PAPER = "PAPER"
      LIVE = "LIVE"

  @dataclass(frozen=True, slots=True)
  class ExecutionAuthorization:
      operation: ExecutionOperation
      target: ExecutionTarget
      allowed: bool
      reason_code: str

  def authorize_mutation(operation, target, *, env=None, kill_switch_reader=None):
      if target is ExecutionTarget.PAPER:
          return ExecutionAuthorization(operation, target, True, "PAPER_ALLOWED")
      decision = LiveExecutionPolicy(
          env=env, kill_switch_reader=kill_switch_reader,
      ).evaluate("live")
      return ExecutionAuthorization(operation, target, decision.allowed, decision.reason_code)

  def require_authorized(authorization, operation, target):
      if authorization.operation is not operation or authorization.target is not target:
          raise ExecutionBlockedError("AUTHORIZATION_MISMATCH")
      if not authorization.allowed:
          raise ExecutionBlockedError(authorization.reason_code)
  ```

- [ ] **Step 4: Require authorization at the last possible sink.**

  Change adapter signatures to:

  ```python
  def create_order(self, request: OrderRequest, *, authorization: ExecutionAuthorization) -> OrderResult:
      require_authorized(authorization, ExecutionOperation.PLACE, self.execution_target)
      return self._exchange.create_order(...)

  def cancel_order(self, order_id: str, symbol: str, *, authorization: ExecutionAuthorization) -> OrderResult:
      require_authorized(authorization, ExecutionOperation.CANCEL, self.execution_target)
      return self._exchange.cancel_order(order_id, symbol)
  ```

  Alpaca close methods must derive the target from the selected paper/live URL,
  authorize the exact close operation, validate symbols through the canonical
  asset registry, and only then construct the request.

- [ ] **Step 5: Add a source-level completeness test.**

  Parse the five execution modules with `ast` and assert every call to
  `create_order`, `cancel_order`, and authenticated DELETE construction carries
  an `authorization=` keyword or an immediately preceding
  `require_authorized()` call in the same function.

  Expected: the test fails if a future developer adds another unguarded sink.

- [ ] **Step 6: Run backend and paper-safety tests and commit.**

  ```bash
  cd legacy/research-backend
  uv run --frozen --extra test pytest -q
  cd ../..
  git add legacy/research-backend
  git commit -m "security: enforce policy at execution sinks"
  ```

### Task 2A: Bound dashboard login input and trust only the reviewed proxy

**Files:**
- Create: `apps/dashboard/src/lib/trading/request-body.ts`
- Create: `apps/dashboard/src/lib/trading/trusted-client-ip.ts`
- Modify: `apps/dashboard/src/app/api/auth/session/route.ts`
- Modify: `apps/dashboard/docs/operations/dashboard-security.md`
- Modify: `apps/dashboard/tests/session-login.test.mjs`
- Create: `apps/dashboard/tests/login-request-boundary.test.mjs`

**Interfaces:**
- Produces `readBoundedJsonObject(request, 1024) -> Promise<BoundedJsonResult>`.
- Produces `trustedClientKey(request, env) -> string`; it trusts
  `cf-connecting-ip` only when `TRADING_DASHBOARD_TRUSTED_PROXY=cloudflare`,
  rejects malformed IPs, never trusts `x-forwarded-for`, and otherwise returns
  one closed `unknown` bucket.

- [ ] **Step 1: Write failing streamed-body and proxy-trust tests.**

  ```javascript
  test('login rejects streamed bodies over 1024 bytes before authentication', async () => {
    const request = streamedRequest('/api/auth/session', 1025);
    const response = await POST(request);
    assert.equal(response.status, 413);
    assert.deepEqual(await response.json(), { authenticated: false });
    assert.equal(authenticationCalls, 0);
  });

  test('x-forwarded-for never selects a limiter bucket', () => {
    const request = new Request(URL, { headers: { 'x-forwarded-for': '203.0.113.8' } });
    assert.equal(trustedClientKey(request, {}), 'unknown');
  });

  test('cloudflare address requires explicit proxy mode and a valid IP', () => {
    const request = new Request(URL, { headers: { 'cf-connecting-ip': '198.51.100.8' } });
    assert.equal(trustedClientKey(request, {}), 'unknown');
    assert.equal(trustedClientKey(request, { TRADING_DASHBOARD_TRUSTED_PROXY: 'cloudflare' }), '198.51.100.8');
  });
  ```

- [ ] **Step 2: Implement a bounded streaming JSON reader.**

  ```typescript
  export async function readBoundedJsonObject(
    request: Request,
    maximumBytes = 1024,
  ): Promise<{ ok: true; value: Record<string, unknown> } | { ok: false; status: 400 | 413 }> {
    const declared = request.headers.get('content-length');
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > maximumBytes)) {
      return { ok: false, status: 413 };
    }
    const reader = request.body?.getReader();
    if (!reader) return { ok: false, status: 400 };
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maximumBytes) {
        await reader.cancel();
        return { ok: false, status: 413 };
      }
      chunks.push(value);
    }
    try {
      const value: unknown = JSON.parse(new TextDecoder().decode(Buffer.concat(chunks)));
      return value !== null && typeof value === 'object' && !Array.isArray(value)
        ? { ok: true, value: value as Record<string, unknown> }
        : { ok: false, status: 400 };
    } catch {
      return { ok: false, status: 400 };
    }
  }
  ```

- [ ] **Step 3: Implement explicit trusted-proxy selection.**

  Use `node:net` `isIP()` and accept only the exact lower-case mode
  `cloudflare`. Do not fall back to forwarded headers. The login route limits
  passwords to 256 UTF-16 code units, returns the same body for all auth
  failures, and does not record the client address or password in logs/audit.

- [ ] **Step 4: Update deployment requirements.**

  Require the Next.js listener on loopback, Cloudflare Access/tunnel as the only
  ingress, `TRADING_DASHBOARD_TRUSTED_PROXY=cloudflare`, one process, and a
  reverse-proxy test proving client-supplied forwarding headers are overwritten.
  Horizontal scaling remains forbidden until a shared limiter is implemented.

- [ ] **Step 5: Run dashboard security validation and commit.**

  ```bash
  cd apps/dashboard
  npm test
  ./node_modules/.bin/tsc --noEmit
  npm run lint
  npm run build
  cd ../..
  git add apps/dashboard/src/lib/trading/request-body.ts \
    apps/dashboard/src/lib/trading/trusted-client-ip.ts \
    apps/dashboard/src/app/api/auth/session/route.ts \
    apps/dashboard/docs/operations/dashboard-security.md \
    apps/dashboard/tests/session-login.test.mjs \
    apps/dashboard/tests/login-request-boundary.test.mjs
  git commit -m "security: harden dashboard login boundary"
  ```

### Task 3: Contain unsafe model deserialization and make legacy models shadow-only

**Files:**
- Create: `legacy/research-backend/model_artifacts.py`
- Modify: `legacy/research-backend/ml_regime.py`
- Modify: `legacy/research-backend/dl_predictor.py`
- Create: `legacy/research-backend/tests/test_model_artifacts.py`
- Create: `docs/security/model-artifact-policy.md`

**Interfaces:**
- Produces: `RuntimeProfile` (`DEVELOPMENT`, `PAPER_PRODUCTION`) and
  `verify_model_artifact(path, manifest, profile) -> VerifiedModelArtifact`.
- Paper production refuses legacy pickle completely. Torch loads only tensor
  state with `weights_only=True`; configuration is canonical JSON covered by
  the same manifest digest.

- [ ] **Step 1: Write failing unsafe-load and profile tests.**

  ```python
  def test_paper_production_rejects_pickle_before_open(tmp_path):
      target = tmp_path / "pipeline.pkl"
      target.write_bytes(b"not-safe")
      with pytest.raises(ModelArtifactBlocked, match="LEGACY_PICKLE_DISABLED"):
          verify_model_artifact(target, manifest={}, profile=RuntimeProfile.PAPER_PRODUCTION)

  def test_torch_source_uses_weights_only_and_external_json_config():
      source = (BACKEND / "dl_predictor.py").read_text(encoding="utf-8")
      assert "weights_only=False" not in source
      assert "weights_only=True" in source
      assert "json.loads" in source or "json.load" in source
  ```

- [ ] **Step 2: Run tests and prove RED.**

  ```bash
  cd legacy/research-backend
  uv run --frozen --extra test pytest -q tests/test_model_artifacts.py
  ```

  Expected: FAIL because pickle and unrestricted Torch loads remain.

- [ ] **Step 3: Implement immutable artifact verification.**

  ```python
  @dataclass(frozen=True, slots=True)
  class VerifiedModelArtifact:
      path: Path
      sha256: str
      model_id: str
      model_version: str

  def verify_model_artifact(path, manifest, profile):
      if profile is RuntimeProfile.PAPER_PRODUCTION and path.suffix == ".pkl":
          raise ModelArtifactBlocked("LEGACY_PICKLE_DISABLED")
      info = path.lstat()
      if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
          raise ModelArtifactBlocked("MODEL_ARTIFACT_TYPE_UNSAFE")
      if info.st_mode & 0o022:
          raise ModelArtifactBlocked("MODEL_ARTIFACT_MODE_UNSAFE")
      expected = manifest["artifacts"][path.name]["sha256"]
      observed = sha256_file(path)
      if not hmac.compare_digest(observed, expected):
          raise ModelArtifactBlocked("MODEL_ARTIFACT_DIGEST_MISMATCH")
      return VerifiedModelArtifact(path, observed, manifest["model_id"], manifest["version"])
  ```

- [ ] **Step 4: Convert Torch checkpoints to safe state/config separation.**

  Training writes `<symbol>_lstm.weights.pt` containing tensors/state dict and
  `<symbol>_lstm.config.json` containing only validated primitives. Loading is:

  ```python
  state = torch.load(weights_path, map_location=DEVICE, weights_only=True)
  config_data = json.loads(config_path.read_text(encoding="utf-8"))
  config = LSTMConfig.model_validate(config_data)
  model = PriceLSTM(state["input_dim"], config).to(DEVICE)
  model.load_state_dict(state["state_dict"], strict=True)
  ```

  Existing `.pt` files are `LEGACY_UNVERIFIED` and never auto-converted or
  loaded in `PAPER_PRODUCTION`.

- [ ] **Step 5: Make regime pickle unavailable in paper production.**

  `_load_model()` returns a typed `MODEL_UNAVAILABLE` result in production and
  continues supporting local development only. No production decision may
  assign non-zero weight to this capability until Task 8 registers a safe
  successor artifact.

- [ ] **Step 6: Run tests, scan for unsafe loads, and commit.**

  ```bash
  cd legacy/research-backend
  uv run --frozen --extra test pytest -q
  test -z "$(rg -n 'pickle\.load|weights_only=False' --glob '*.py' . \
    | rg -v '(^|/)tests/' || true)"
  cd ../..
  git add legacy/research-backend docs/security/model-artifact-policy.md
  git commit -m "security: contain legacy model deserialization"
  ```

### Task 4: Make PostgreSQL validation hermetic and mandatory in promotion CI

**Files:**
- Modify: `tests/jobs/_postgres.py`
- Modify: `tests/conftest.py`
- Create: `tests/production/test_postgres_test_authority.py`
- Modify: `Makefile`
- Modify: `README.md`

**Interfaces:**
- Produces: `TRADING_TEST_POSTGRES_MODE=external|ephemeral` and
  `TRADING_REQUIRE_POSTGRES_TESTS=0|1`.
- Promotion uses `ephemeral` and `require=1`; an unavailable database is a
  hard failure rather than 67 setup errors or a silent skip.

- [ ] **Step 1: Write failing authority-selection tests.**

  ```python
  def test_required_external_authority_fails_once_with_redacted_identity(tmp_path):
      env = {"TRADING_TEST_POSTGRES_MODE": "external", "TRADING_REQUIRE_POSTGRES_TESTS": "1"}
      with pytest.raises(PostgresTestAuthorityUnavailable, match="127.0.0.1:55432"):
          resolve_test_authority(env, probe=lambda _: False)

  def test_ephemeral_mode_never_reads_home_admin_env(monkeypatch):
      monkeypatch.setenv("TRADING_TEST_POSTGRES_MODE", "ephemeral")
      authority = resolve_test_authority(os.environ, probe=lambda _: True)
      assert authority.source == "EPHEMERAL"
  ```

- [ ] **Step 2: Implement one session-scoped ephemeral PostgreSQL 16 fixture.**

  Extend the existing `disposable_postgres_cluster()` helper. Use a random
  loopback port, temporary data/socket directories, `initdb`, and `pg_ctl`.
  Set a test-only password before yielding `DatabaseSettings`; stop the child
  cluster in `finally`.

  ```python
  @pytest.fixture(scope="session", autouse=True)
  def postgres_test_authority(tmp_path_factory):
      if os.getenv("TRADING_TEST_POSTGRES_MODE", "external") == "ephemeral":
          with disposable_postgres_cluster() as cluster:
              env_path = write_test_admin_env(tmp_path_factory.mktemp("pg"), cluster)
              previous = postgres_helpers.ADMIN_ENV
              postgres_helpers.ADMIN_ENV = env_path
              try:
                  yield
              finally:
                  postgres_helpers.ADMIN_ENV = previous
          return
      require_external_authority_or_skip_or_fail()
      yield
  ```

- [ ] **Step 3: Add separate fast and promotion targets.**

  ```make
  test-core-fast:
	TRADING_REQUIRE_POSTGRES_TESTS=0 uv run pytest -q --ignore=legacy/research-backend --ignore=apps/dashboard

  test-core-promotion:
	TRADING_TEST_POSTGRES_MODE=ephemeral TRADING_REQUIRE_POSTGRES_TESTS=1 \
	uv run pytest -q --ignore=legacy/research-backend --ignore=apps/dashboard
  ```

  `test-all` must use `test-core-promotion`; developers may explicitly choose
  `test-core-fast` for local iterations.

- [ ] **Step 4: Run the former failure mode and the full promotion suite.**

  ```bash
  TRADING_TEST_POSTGRES_MODE=external TRADING_REQUIRE_POSTGRES_TESTS=1 \
    uv run pytest -q tests/production/test_postgres_test_authority.py
  make test-core-promotion
  make test-all
  ```

  Expected: authority unit tests PASS; the full core suite has zero failures,
  zero errors, and no database-dependent skips.

- [ ] **Step 5: Commit.**

  ```bash
  git add tests Makefile README.md
  git commit -m "test: make postgres promotion validation hermetic"
  ```

### Task 5: Add reproducible CI, dependency, and security promotion gates

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/security.yml`
- Create: `.github/dependabot.yml`
- Modify: `apps/dashboard/package.json`
- Update with npm only: `apps/dashboard/package-lock.json`
- Modify with uv only: `pyproject.toml`, `uv.lock`
- Modify with uv only: `legacy/research-backend/pyproject.toml`, `legacy/research-backend/uv.lock`
- Create: `scripts/check_dependency_audit.py`
- Test: `tests/security/test_dependency_audit.py`

**Interfaces:**
- Produces two required checks: `ci/all-components` and
  `security/no-unresolved-high`.
- Dependency policy fails on any critical/high finding and on any
  unreviewed moderate finding; low findings require a documented expiry.

- [ ] **Step 1: Obtain explicit approval for CI and dependency changes.**

  Approval covers workflow files, pinned audit tooling, Next.js 16.2.10,
  npm overrides `postcss=8.5.19` and `js-yaml=4.2.0`, and test-only
  `pip-audit==2.9.0`. No production dependency is added to runtime imports.

- [ ] **Step 2: Write failing audit-policy tests.**

  ```python
  def test_high_or_critical_always_fails():
      report = {"vulnerabilities": [{"id": "X", "severity": "high"}]}
      assert evaluate(report, policy={}).decision == "FAIL"

  def test_unreviewed_moderate_fails():
      report = {"vulnerabilities": [{"id": "Y", "severity": "moderate"}]}
      assert evaluate(report, policy={}).decision == "FAIL"

  def test_expired_low_waiver_fails():
      policy = {"X": {"severity": "low", "expires_on": "2026-07-13"}}
      assert evaluate({"vulnerabilities": [{"id": "X", "severity": "low"}]}, policy).decision == "FAIL"
  ```

- [ ] **Step 3: Update dashboard dependencies through npm.**

  ```bash
  cd apps/dashboard
  npm install --save-exact next@16.2.10
  npm pkg set overrides.postcss=8.5.19 overrides.js-yaml=4.2.0
  npm install
  npm test
  ./node_modules/.bin/tsc --noEmit
  npm run lint
  npm run build
  npm audit --omit=dev --audit-level=moderate
  cd ../..
  ```

  Expected: tests/build PASS and production dependency audit has no
  moderate/high/critical finding. Do not use `npm audit fix --force`.

  Pin audit tooling in each Python development graph without adding a runtime
  import:

  ```bash
  uv add --dev 'pip-audit==2.9.0'
  cd legacy/research-backend
  uv add --dev 'pip-audit==2.9.0'
  cd ../..
  uv lock --check
  uv lock --check --directory legacy/research-backend
  ```

- [ ] **Step 4: Create deterministic workflows.**

  `ci.yml` checks out with no persisted credential, installs Python 3.11 and
  Node 22, uses `uv sync --frozen`, `npm ci`, then runs:

  ```yaml
  - run: make audit-release
  - run: make check-contracts
  - run: make test-all
    env:
      TRADING_TEST_POSTGRES_MODE: ephemeral
      TRADING_REQUIRE_POSTGRES_TESTS: "1"
      LIVE_EXECUTION_ENABLED: "false"
      LIVE_TRADING_APPROVED: "false"
  - run: make build-dashboard
  ```

  `security.yml` runs Gitleaks on full history with `--exit-code 0`, passes the
  redacted report through `verify_secret_hygiene.py` and the exact
  rotated-history baseline, scans an extracted current tree with zero findings
  required, runs Semgrep with the repository policy, `npm audit --json`, and two
  `pip-audit` runs against lockfile exports. Upload only redacted reports.

  ```yaml
  - run: uv export --frozen --no-dev --output-file /tmp/root-requirements.txt
  - run: uv run pip-audit --requirement /tmp/root-requirements.txt
  - run: uv export --directory legacy/research-backend --frozen --no-dev --output-file /tmp/backend-requirements.txt
  - run: uv run --directory legacy/research-backend pip-audit --requirement /tmp/backend-requirements.txt
  ```

- [ ] **Step 5: Prove workflows do not contain live secrets or mutation
  commands.**

  ```bash
  rg -n 'LIVE_EXECUTION_ENABLED|LIVE_TRADING_APPROVED' .github/workflows
  ! rg -n 'sudo|systemctl|curl .*broker|curl .*exchange|git push|\.env' .github/workflows
  uv run pytest -q tests/security/test_dependency_audit.py
  ```

  Expected: both flags occur only with value `false`; the forbidden scan is
  empty.

- [ ] **Step 6: Commit locally; do not push.**

  ```bash
  git add .github apps/dashboard/package.json apps/dashboard/package-lock.json \
    pyproject.toml uv.lock legacy/research-backend/pyproject.toml \
    legacy/research-backend/uv.lock scripts/check_dependency_audit.py \
    tests/security/test_dependency_audit.py
  git commit -m "ci: add reproducible production promotion gates"
  ```

### Task 6: Introduce deterministic execution contracts and durable lineage

**Files:**
- Create: `packages/execution_contracts/__init__.py`
- Create: `packages/execution_contracts/models.py`
- Create: `packages/execution_contracts/fingerprint.py`
- Create: `packages/execution_contracts/signing.py`
- Create: `packages/execution_contracts/policy.py`
- Create: `alembic/versions/0005_execution_control_plane.py`
- Create: `tests/execution_contracts/test_models.py`
- Create: `tests/execution_contracts/test_signing.py`
- Create: `tests/execution_contracts/test_policy.py`
- Create: `tests/control_api/test_execution_schema.py`

**Interfaces:**
- Produces immutable `TradeIntent`, `RiskDecision`, `UnsignedOrderPlan`, and
  `SignedOrderPlan` Pydantic models.
- Produces `sign_plan(plan, private_key, key_id)` and
  `verify_plan(signed, public_key, now)` using Ed25519.
- Persists exact canonical fingerprints and append-only state transitions.

- [ ] **Step 1: Write strict contract tests.**

  ```python
  def test_trade_intent_is_strict_canonical_and_expiring():
      intent = TradeIntent.model_validate({
          "schema_version": "1.0.0",
          "intent_id": "intent_0123456789abcdef",
          "asset": "BTC",
          "side": "BUY",
          "quantity": "0.00100000",
          "source_decision_id": "decision_1",
          "strategy_version": "paper-v1",
          "model_version": "LEGACY_UNVERIFIED",
          "created_at": "2026-07-14T12:00:00Z",
          "expires_at": "2026-07-14T12:05:00Z",
      })
      assert intent.quantity == Decimal("0.00100000")
      with pytest.raises(ValidationError):
          TradeIntent.model_validate({**intent.model_dump(), "unexpected": True})

  def test_allow_decision_must_bind_exact_intent_fingerprint():
      decision = make_risk_decision(outcome="ALLOW")
      assert decision.intent_fingerprint == fingerprint(intent)
      assert decision.reason_codes == ("WITHIN_PAPER_POLICY",)
  ```

- [ ] **Step 2: Define closed immutable contracts.**

  ```python
  class ExecutionSide(StrEnum):
      BUY = "BUY"
      SELL = "SELL"

  class RiskOutcome(StrEnum):
      ALLOW = "ALLOW"
      DENY = "DENY"

  class TradeIntent(StrictModel):
      schema_version: Literal["1.0.0"]
      intent_id: Id
      asset: AssetSymbol
      side: ExecutionSide
      quantity: PositiveDecimal
      source_decision_id: Id
      strategy_version: Version
      model_version: Version
      created_at: AwareDatetime
      expires_at: AwareDatetime

      @model_validator(mode="after")
      def valid_window(self):
          if not self.created_at < self.expires_at <= self.created_at + timedelta(minutes=5):
              raise ValueError("intent lifetime must be positive and at most five minutes")
          return self

  class RiskDecision(StrictModel):
      schema_version: Literal["1.0.0"]
      risk_decision_id: Id
      intent_fingerprint: Sha256
      outcome: RiskOutcome
      reason_codes: tuple[ReasonCode, ...]
      policy_version: Literal["paper-risk-v1"]
      evaluated_at: AwareDatetime
      max_notional: NonNegativeDecimal
      max_slippage_bps: Annotated[int, Field(ge=0, le=500)]

  class UnsignedOrderPlan(StrictModel):
      schema_version: Literal["1.0.0"]
      plan_id: Id
      intent: TradeIntent
      risk_decision: RiskDecision
      route: Literal["PAPER"]
      nonce: Nonce
      created_at: AwareDatetime
      expires_at: AwareDatetime
  ```

  Canonical fingerprints use UTF-8 JSON, sorted keys, no insignificant
  whitespace, decimal strings, and SHA-256.

- [ ] **Step 3: Implement Ed25519 signing and verification.**

  ```python
  class SignedOrderPlan(StrictModel):
      plan: UnsignedOrderPlan
      key_id: KeyId
      signature: Base64Url

  def sign_plan(plan, private_key, key_id):
      payload = canonical_bytes(plan.model_dump(mode="json"))
      signature = private_key.sign(payload)
      return SignedOrderPlan(
          plan=plan, key_id=key_id,
          signature=base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
      )

  def verify_plan(signed, public_key, now):
      public_key.verify(decode_signature(signed.signature), canonical_bytes(
          signed.plan.model_dump(mode="json")
      ))
      if not signed.plan.created_at <= now < signed.plan.expires_at:
          raise PlanRejected("PLAN_EXPIRED_OR_NOT_YET_VALID")
      if signed.plan.risk_decision.outcome is not RiskOutcome.ALLOW:
          raise PlanRejected("RISK_DENIED")
      if signed.plan.risk_decision.intent_fingerprint != fingerprint(signed.plan.intent):
          raise PlanRejected("INTENT_FINGERPRINT_MISMATCH")
      return VerifiedOrderPlan(signed=signed, fingerprint=fingerprint(signed.plan))
  ```

- [ ] **Step 4: Add migration `0005_execution_control_plane`.**

  Create tables with foreign keys and unique fingerprints:

  ```text
  trade_intents(intent_id PK, intent_fingerprint UNIQUE, asset_id FK,
                side, quantity, source_decision_id FK, strategy_version,
                model_version, created_at, expires_at)
  risk_decisions(risk_decision_id PK, intent_id FK UNIQUE, outcome,
                 reason_codes JSON, policy_version, max_notional,
                 max_slippage_bps, evaluated_at)
  order_plans(plan_id PK, intent_id FK UNIQUE, risk_decision_id FK UNIQUE,
              route, nonce UNIQUE, plan_fingerprint UNIQUE, key_id,
              signature, state, created_at, expires_at)
  execution_events(execution_event_id PK, plan_id FK, sequence,
                   from_state, to_state, reason_code, occurred_at,
                   UNIQUE(plan_id, sequence))
  paper_orders(paper_order_id PK, plan_id FK UNIQUE, client_order_id UNIQUE,
               state, requested_quantity, filled_quantity, average_fill_price,
               created_at, updated_at)
  paper_fills(fill_id PK, paper_order_id FK, fill_sequence,
              quantity, price, commission, filled_at,
              UNIQUE(paper_order_id, fill_sequence))
  reconciliation_runs(run_id PK, started_at, finished_at, outcome,
                      mismatch_count, evidence_sha256)
  reconciliation_mismatches(mismatch_id PK, run_id FK, plan_id FK,
                            code, details JSON, resolved_at)
  ```

  Only the execution service role may insert/update plan/order/fill tables;
  events are append-only even for the owner, matching Phase 4 event protection.

- [ ] **Step 5: Prove schema, signatures, replay rejection, and downgrade
  behavior.**

  ```bash
  TRADING_TEST_POSTGRES_MODE=ephemeral TRADING_REQUIRE_POSTGRES_TESTS=1 \
    uv run pytest -q tests/execution_contracts tests/control_api/test_execution_schema.py
  ```

  Expected: exact Alembic head `0005_execution_control_plane`, invalid
  signatures/expired plans/duplicate nonces fail, and restore-based downgrade
  policy is explicit.

- [ ] **Step 6: Run full core tests and commit.**

  ```bash
  make test-core-promotion
  git add packages/execution_contracts alembic/versions/0005_execution_control_plane.py \
    tests/execution_contracts tests/control_api/test_execution_schema.py
  git commit -m "feat: add deterministic execution contracts"
  ```

### Task 7: Build the deterministic risk service, paper gateway, and reconciliation loop

**Files:**
- Create: `services/risk_engine/__init__.py`
- Create: `services/risk_engine/config.py`
- Create: `services/risk_engine/repository.py`
- Create: `services/risk_engine/engine.py`
- Create: `services/risk_engine/main.py`
- Create: `services/execution_gateway/__init__.py`
- Create: `services/execution_gateway/config.py`
- Create: `services/execution_gateway/repository.py`
- Create: `services/execution_gateway/paper_adapter.py`
- Create: `services/execution_gateway/gateway.py`
- Create: `services/execution_gateway/main.py`
- Create: `services/reconciliation/__init__.py`
- Create: `services/reconciliation/reconciler.py`
- Create: `tests/risk_engine/test_engine.py`
- Create: `tests/execution_gateway/test_gateway.py`
- Create: `tests/execution_gateway/test_reconciliation.py`
- Create: `ops/systemd/trading-risk-engine.service`
- Create: `ops/systemd/risk-engine.env.example`
- Create: `ops/systemd/trading-paper-execution.service`
- Create: `ops/systemd/paper-execution.env.example`

**Interfaces:**
- Risk service consumes `TradeIntent` and canonical safety/market/position
  evidence, then produces a persisted `RiskDecision` and signed PAPER-only plan.
- Execution gateway consumes only `SignedOrderPlan`, verifies with a public key,
  then produces idempotent paper orders/fills and append-only execution events.
- Reconciler produces mismatch evidence. No service imports
  `legacy/research-backend` or accepts broker credentials.

- [ ] **Step 1: Write fail-closed risk tests.**

  ```python
  @pytest.mark.parametrize("fault", [
      "SAFETY_UNKNOWN", "LIVE_GATE_TRUE", "KILL_SWITCH_ACTIVE",
      "STALE_MARKET", "UNKNOWN_ASSET", "EXPIRED_INTENT",
      "POSITION_LIMIT", "DAILY_LOSS_LIMIT", "DUPLICATE_INTENT",
  ])
  def test_fault_denies_without_creating_plan(fault, repository):
      result = evaluate_intent(intent_for(fault), evidence_for(fault), PAPER_POLICY_V1)
      assert result.outcome == "DENY"
      assert repository.count("order_plans") == 0

  def test_allowed_intent_is_idempotent():
      first = risk_service.evaluate_and_sign(intent)
      second = risk_service.evaluate_and_sign(intent)
      assert first.plan_id == second.plan_id
      assert repository.count("order_plans") == 1
  ```

- [ ] **Step 2: Implement deterministic `paper-risk-v1`.**

  Policy inputs are only typed evidence. Enforce existing hard limits:

  ```python
  PAPER_POLICY_V1 = RiskPolicy(
      version="paper-risk-v1",
      max_total_exposure_pct=Decimal("0.50"),
      max_trade_pct=Decimal("0.05"),
      max_group_exposure_pct=Decimal("0.15"),
      daily_loss_limit_pct=Decimal("0.03"),
      max_drawdown_pct=Decimal("0.20"),
      minimum_signal_confidence=Decimal("0.72"),
      maximum_market_age=timedelta(minutes=30),
      maximum_plan_lifetime=timedelta(minutes=5),
      maximum_slippage_bps=200,
  )
  ```

  Any missing/unknown value denies. LLM prose, dashboard input, and legacy
  mutable config never alter these limits.

- [ ] **Step 3: Implement a separate deterministic risk service.**

  ```python
  class RiskService:
      def evaluate_and_sign(self, intent: TradeIntent) -> SignedOrderPlan | RiskDecision:
          existing = self.repository.find_by_intent_fingerprint(fingerprint(intent))
          if existing:
              return existing
          safety = self.safety_provider.snapshot()
          assert_safe(safety)
          decision = self.risk_engine.evaluate(intent, self.evidence.snapshot())
          self.repository.insert_intent_and_decision(intent, decision)
          if decision.outcome is RiskOutcome.DENY:
              return decision
          plan = self.plan_builder.paper(intent, decision)
          return self.repository.persist_and_sign_once(
              plan, private_key=self.signing_key, key_id=self.key_id,
          )
  ```

  Only this service can read the plan-signing private key. It cannot import or
  call an execution adapter.

- [ ] **Step 4: Implement a PAPER-only execution gateway.**

  ```python
  class ExecutionGateway:
      def submit(self, signed: SignedOrderPlan) -> GatewayResult:
          safety = self.safety_provider.snapshot()
          assert_safe(safety)
          verified = verify_plan(signed, self.public_key, self.clock.now())
          if verified.signed.plan.route != "PAPER":
              raise PlanRejected("PAPER_GATEWAY_ROUTE_MISMATCH")
          return self.repository.execute_paper_once(verified, self.paper_adapter)
  ```

  The gateway has the public verification key only. Config rejects any route
  other than `PAPER` and rejects presence of exchange, broker, or withdrawal
  credential variables.

- [ ] **Step 5: Implement deterministic paper fill simulation.**

  Use the immutable market snapshot bound to the intent. The fill engine records
  spread, configured slippage, commission, delay, partial-fill sequence, and
  missed-fill outcome. No current network price is fetched.

  ```python
  fill_price = reference_price * (
      Decimal("1") + side.sign * Decimal(policy.slippage_bps) / Decimal("10000")
  )
  ```

- [ ] **Step 6: Implement reconciliation and fail-closed mismatch handling.**

  Reconciler checks one-to-one intent/decision/plan/order relations, fill sums,
  positions, realized P&L, plan signatures, and event sequence. Any unresolved
  mismatch marks readiness `NOT_READY`, blocks new paper plans, and emits an
  append-only mismatch; it does not silently repair money or history.

- [ ] **Step 7: Verify no live adapter is reachable.**

  ```bash
  ! rg -n 'alpaca|ccxt|coinbase|kraken|API_KEY|SECRET_KEY' \
    services/risk_engine services/execution_gateway
  TRADING_TEST_POSTGRES_MODE=ephemeral TRADING_REQUIRE_POSTGRES_TESTS=1 \
    uv run pytest -q tests/risk_engine tests/execution_gateway
  ```

  Expected: forbidden scan empty; tests cover policy denial, idempotency,
  replay, partial fill, missed fill, stale evidence, and reconciliation halt.

- [ ] **Step 8: Verify unit sandboxing and commit.**

  Both units run dedicated users, loopback only, read-only application releases,
  `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, and no
  external network access. Only the risk unit can read the signing private key;
  only the gateway can mutate paper order/fill tables.

  ```bash
  systemd-analyze verify ops/systemd/trading-risk-engine.service \
    ops/systemd/trading-paper-execution.service
  git add services/risk_engine services/execution_gateway services/reconciliation \
    tests/risk_engine tests/execution_gateway ops/systemd/trading-risk-engine.service \
    ops/systemd/risk-engine.env.example ops/systemd/trading-paper-execution.service \
    ops/systemd/paper-execution.env.example
  git commit -m "feat: add paper execution gateway and reconciliation"
  ```

### Task 8: Register models and make all legacy/LLM outputs shadow-only

**Files:**
- Create: `packages/research_contracts/__init__.py`
- Create: `packages/research_contracts/models.py`
- Create: `packages/research_contracts/registry.py`
- Create: `alembic/versions/0006_model_research_governance.py`
- Create: `services/model_registry/__init__.py`
- Create: `services/model_registry/registry.py`
- Modify: `services/risk_engine/engine.py`
- Create: `tests/research_contracts/test_models.py`
- Create: `tests/model_registry/test_registry.py`
- Create: `docs/production/model-governance-policy.md`

**Interfaces:**
- Produces `ModelStatus = LEGACY_UNVERIFIED|SHADOW|CANDIDATE|APPROVED|REVOKED`.
- Produces immutable `ResearchPacket`; no prose or model output becomes a
  `TradeIntent` unless the exact model version is `APPROVED` and the strategy
  policy assigns a non-zero deterministic weight.

- [ ] **Step 1: Write governance tests.**

  ```python
  @pytest.mark.parametrize("status", ["LEGACY_UNVERIFIED", "SHADOW", "CANDIDATE", "REVOKED"])
  def test_nonapproved_model_has_zero_execution_weight(status):
      assert execution_weight(model(status=status), policy=MODEL_POLICY_V1) == Decimal("0")

  def test_research_packet_requires_point_in_time_lineage():
      with pytest.raises(ValidationError, match="known_at"):
          ResearchPacket.model_validate(packet_with(known_at=DECISION_TIME + timedelta(seconds=1)))
  ```

- [ ] **Step 2: Define strict research and registry contracts.**

  `ResearchPacket` includes packet ID, asset, event time, known-at time, source
  hash, source type, model ID/version, prompt version when applicable, generated
  time, structured factors, confidence, and `shadow=True`. Raw credentials,
  hidden prompts, free-form tool output, and executable paths are forbidden.

- [ ] **Step 3: Add migration `0006_model_research_governance`.**

  ```text
  model_versions(model_version_id PK, model_id, version, artifact_sha256,
                 config_sha256, code_commit, training_data_cutoff,
                 status, approved_by, approved_at, revoked_at,
                 UNIQUE(model_id, version))
  model_metrics(metric_id PK, model_version_id FK, dataset_id,
                split, metric_name, metric_value, measured_at)
  research_packets(packet_id PK, asset_id FK, event_time, known_at,
                   source_hash, model_version_id FK, prompt_version,
                   factors JSON, confidence, shadow, created_at)
  model_status_events(event_id PK, model_version_id FK, sequence,
                      from_status, to_status, actor_id, reason, created_at)
  ```

  Status events are append-only. Promotion to `APPROVED` requires a G2/G3
  evidence reference and operator identity.

- [ ] **Step 4: Import existing models only as `LEGACY_UNVERIFIED`.**

  The importer records hashes and metadata without loading model bytes. Missing
  lineage stays `UNKNOWN`; it is never inferred from file timestamps.

- [ ] **Step 5: Enforce zero execution weight in the gateway.**

  ```python
  if registry.status(intent.model_version) is not ModelStatus.APPROVED:
      return RiskDecision.deny(intent, "MODEL_NOT_APPROVED", policy_version)
  if research_packet.shadow:
      return RiskDecision.deny(intent, "SHADOW_RESEARCH_CANNOT_EXECUTE", policy_version)
  ```

- [ ] **Step 6: Test migrations/governance and commit.**

  ```bash
  TRADING_TEST_POSTGRES_MODE=ephemeral TRADING_REQUIRE_POSTGRES_TESTS=1 \
    uv run pytest -q tests/research_contracts tests/model_registry \
      tests/control_api/test_execution_schema.py
  git add packages/research_contracts services/model_registry \
    services/risk_engine/engine.py alembic/versions/0006_model_research_governance.py \
    tests/research_contracts tests/model_registry docs/production/model-governance-policy.md
  git commit -m "feat: add model and research governance"
  ```

### Task 9: Add operational metrics, readiness SLOs, and alert evidence

**Files:**
- Create: `packages/telemetry/__init__.py`
- Create: `packages/telemetry/metrics.py`
- Create: `packages/telemetry/readiness.py`
- Modify: `apps/control_api/control_api/app.py`
- Modify: `apps/job_api/app.py`
- Modify: `services/job_worker/worker.py`
- Modify: `services/job_scheduler/scheduler.py`
- Modify: `services/risk_engine/engine.py`
- Modify: `services/execution_gateway/gateway.py`
- Modify: `services/reconciliation/reconciler.py`
- Create: `ops/monitoring/paper-production-alerts.json`
- Create: `docs/production/paper-production-slos.md`
- Create: `tests/telemetry/test_metrics.py`
- Create: `tests/telemetry/test_readiness.py`

**Interfaces:**
- Produces bounded OpenMetrics text on loopback-only `/metrics` endpoints.
- Produces one aggregate readiness decision with reason codes; `READY` requires
  current safety evidence, database revision, worker heartbeat, scheduler
  heartbeat, reconciliation, and source/release identity.

- [ ] **Step 1: Write failing metrics and readiness tests.**

  ```python
  def test_metric_labels_are_closed_and_never_contain_ids_or_secrets():
      registry = MetricRegistry(allowed_labels={"service", "outcome", "job_type"})
      with pytest.raises(ValueError, match="unsupported label"):
          registry.increment("jobs_total", labels={"token": "secret"})

  @pytest.mark.parametrize("reason", [
      "SAFETY_STALE", "DATABASE_NOT_READY", "WORKER_STALE",
      "SCHEDULER_STALE", "RECONCILIATION_MISMATCH", "RELEASE_MISMATCH",
  ])
  def test_any_critical_reason_makes_service_not_ready(reason):
      result = evaluate_readiness(evidence_with(reason))
      assert result.status == "NOT_READY"
      assert reason in result.reason_codes
  ```

- [ ] **Step 2: Implement a bounded standard-library registry.**

  Expose counters/gauges only for fixed names and fixed low-cardinality labels:

  ```text
  trading_jobs_total{job_type,outcome}
  trading_job_queue_depth{job_type}
  trading_worker_heartbeat_age_seconds
  trading_scheduler_heartbeat_age_seconds
  trading_reconciliation_mismatches
  trading_paper_orders_total{outcome}
  trading_policy_denials_total{reason_code}
  trading_market_freshness_age_seconds
  trading_safety_snapshot_age_seconds
  trading_release_identity_match
  ```

  Trace IDs, job IDs, plan IDs, symbols, user input, paths, and credentials are
  excluded from metric labels.

- [ ] **Step 3: Add loopback metrics and aggregate readiness.**

  Metrics routes return `text/plain; version=0.0.4`, cap output at 256 KiB, and
  add `Cache-Control: no-store`. Readiness returns 503 when any required
  evidence is missing, unknown, stale, or mismatched.

- [ ] **Step 4: Define exact paper-production SLOs and alerts.**

  `paper-production-slos.md` and JSON policy use:

  ```text
  Control/Job API availability: >= 99.5% over 30 days
  Scheduler heartbeat age: <= 180 seconds
  Worker heartbeat age: <= 120 seconds
  Safety snapshot age: <= 120 seconds
  Market snapshot age: <= 1800 seconds
  Unresolved reconciliation mismatch: 0
  Orphan paper order: 0
  Policy breach: 0
  Queue oldest age: <= 900 seconds
  Backup age: <= 24 hours
  ```

  Alerts identify reason codes and redacted service identity only.

- [ ] **Step 5: Test cardinality, stale states, and secret redaction.**

  ```bash
  uv run pytest -q tests/telemetry tests/control_api tests/jobs
  ! rg -n '(password|token|secret)[A-Za-z0-9_]*[[:space:]]*[:=]' \
    ops/monitoring
  ```

  Expected: tests PASS and the monitoring policy contains no secret-like
  assignment.

- [ ] **Step 6: Commit.**

  ```bash
  git add packages/telemetry apps/control_api apps/job_api services/job_worker \
    services/job_scheduler services/risk_engine services/execution_gateway \
    services/reconciliation ops/monitoring \
    docs/production/paper-production-slos.md tests/telemetry
  git commit -m "feat: add production readiness telemetry"
  ```

### Task 10: Turn G0-G3 into executable evidence gates

**Files:**
- Create: `packages/promotion_evidence/__init__.py`
- Create: `packages/promotion_evidence/models.py`
- Create: `packages/promotion_evidence/gates.py`
- Create: `scripts/run_promotion_gates.py`
- Create: `ops/promotion/paper-policy-v1.json`
- Create: `tests/promotion/test_g0_code_contracts.py`
- Create: `tests/promotion/test_g1_point_in_time.py`
- Create: `tests/promotion/test_g2_oos_utility.py`
- Create: `tests/promotion/test_g3_robustness.py`
- Create: `docs/production/evidence/README.md`

**Interfaces:**
- Consumes immutable dataset/model/policy/source identities and completed test
  reports.
- Produces canonical `gate-result-v1.json` documents containing gate, outcome,
  policy hash, source commit, input hashes, metrics, failure reasons, and result
  SHA-256. Evidence is append-only and stored outside Git for real runs.

- [ ] **Step 1: Write a strict evidence-envelope test.**

  ```python
  def test_gate_result_binds_every_input_and_never_accepts_unknown_fields():
      result = GateResult.model_validate({
          "schema_version": "1.0.0",
          "gate": "G1",
          "outcome": "PASS",
          "source_commit": SOURCE_COMMIT,
          "policy_sha256": POLICY_SHA,
          "input_sha256": [DATASET_SHA],
          "metrics": {"future_known_rows": 0},
          "reason_codes": [],
          "generated_at": "2026-07-14T12:00:00Z",
      })
      assert result.outcome == "PASS"
      with pytest.raises(ValidationError):
          GateResult.model_validate({**result.model_dump(), "note": "unbound"})
  ```

- [ ] **Step 2: Implement G0 exactly.**

  G0 runs `make audit-release`, contract drift, all tests, dashboard build,
  Gitleaks current/history policy, dependency audits, schema head, and release
  identity. PASS requires all commands exit zero and no current-tree secret.
  Historical-secret publication remains blocked unless the sanitized repository
  from Task 1 is used.

- [ ] **Step 3: Implement G1 point-in-time validation.**

  For every research packet, feature, signal, decision, and intent:

  ```python
  if row.known_at is None or row.known_at > decision.as_of:
      failures.append((row.identity, "FUTURE_KNOWN_DATA"))
  if row.source_hash not in immutable_snapshot_hashes:
      failures.append((row.identity, "UNBOUND_SOURCE_SNAPSHOT"))
  ```

  PASS requires zero future-known rows, zero missing lineage, zero mutable
  snapshot hashes, and all declared data gaps within the policy.

- [ ] **Step 4: Implement G2 predefined out-of-sample utility.**

  Policy `paper-policy-v1.json` requires:

  ```json
  {
    "minimum_assets": 5,
    "minimum_nonoverlapping_oos_windows": 3,
    "minimum_oos_trades": 100,
    "minimum_positive_window_fraction": 0.60,
    "minimum_excess_net_return": 0.0,
    "maximum_drawdown_pct": 20.0,
    "baseline": "buy-and-hold-or-cash-by-asset-class",
    "selection_rule": "fixed-before-oos"
  }
  ```

  PASS requires positive aggregate net utility versus the predefined baseline,
  at least 60% positive OOS windows, no single asset contributing over 50% of
  aggregate excess utility, and all 100+ trades occurring after the training
  cutoff.

- [ ] **Step 5: Implement G3 robustness matrix.**

  Run each candidate with:

  ```text
  slippage: base, 25 bps, 50 bps, 100 bps
  costs: base, 1.5x, 2.0x
  execution delay: 0, 1, 3 bars
  missed fills: 0%, 10%, 25%
  partial fills: 100%, 75%, 50%
  ```

  PASS requires no lookahead violation, no negative account balance, no
  position/policy breach, and positive aggregate net utility under the
  `50 bps + 2x cost + 1 bar + 10% missed-fill` combined scenario. More severe
  scenarios are recorded but not silently discarded.

- [ ] **Step 6: Prove deliberate failures and a deterministic evidence hash.**

  ```bash
  uv run pytest -q tests/promotion
  uv run python scripts/run_promotion_gates.py \
    --policy ops/promotion/paper-policy-v1.json \
    --source-commit "$(git rev-parse HEAD)" \
    --output "$HOME/.local/state/trading-agent/promotion/dry-run"
  ```

  Expected: fixture tests include both PASS and intentional FAIL cases; repeated
  runs over identical fixtures yield the same evidence-content hash.

- [ ] **Step 7: Commit code and fixture evidence only.**

  ```bash
  git add packages/promotion_evidence scripts/run_promotion_gates.py \
    ops/promotion/paper-policy-v1.json tests/promotion \
    docs/production/evidence/README.md
  git commit -m "feat: automate G0 through G3 evidence gates"
  ```

### Task 11: Collect G4 paper-operation evidence and run failure drills

**Files:**
- Create: `scripts/capture_g4_evidence.py`
- Create: `scripts/verify_g4_window.py`
- Create: `tests/promotion/test_g4_paper_operation.py`
- Create: `docs/production/runbooks/kill-switch-drill.md`
- Create: `docs/production/runbooks/worker-recovery-drill.md`
- Create: `docs/production/runbooks/reconciliation-drill.md`
- Create: `docs/production/runbooks/backup-restore-drill.md`
- Create: `docs/production/g4-operator-checklist.md`

**Interfaces:**
- Consumes: 30 consecutive calendar days of immutable paper-production candidate
  evidence from one release identity.
- Produces: one signed/hash-bound G4 packet and a `PASS` or `FAIL`; any release,
  policy, strategy, model, schema, or safety-authority change restarts the
  observation window.

- [ ] **Step 1: Write G4 window tests.**

  ```python
  def test_g4_requires_thirty_days_and_two_hundred_completed_plans():
      assert verify_g4(window(days=29, completed_plans=500)).outcome == "FAIL"
      assert verify_g4(window(days=30, completed_plans=199)).outcome == "FAIL"

  def test_any_policy_breach_or_orphan_is_hard_failure():
      assert verify_g4(window(policy_breaches=1)).outcome == "FAIL"
      assert verify_g4(window(orphan_orders=1)).outcome == "FAIL"
      assert verify_g4(window(unresolved_reconciliation=1)).outcome == "FAIL"
  ```

- [ ] **Step 2: Define the exact G4 threshold.**

  PASS requires all of:

  ```text
  one unchanged release/policy/model/schema identity for >= 30 days
  >= 200 completed signed paper plans
  0 live route attempts
  0 policy breaches
  0 orphan orders
  0 unresolved reconciliation mismatches
  0 invalid/replayed/expired plan executions
  100% intents linked to RiskDecision and SignedOrderPlan
  100% P&L replay equality to the cent/asset precision policy
  >= 2 successful kill-switch drills on different days
  >= 1 worker crash/recovery drill
  >= 1 reconciliation fault-injection drill
  >= 1 backup/restore drill from the candidate schema
  SLO compliance for >= 99% of five-minute observation buckets
  ```

- [ ] **Step 3: Run drills only against the isolated paper candidate.**

  Kill-switch drill proves new plans stop, in-flight paper work reaches a known
  state, readiness becomes 503, audit event is written, and recovery requires an
  operator reason. Worker drill kills only the candidate child process and
  proves lease fencing/idempotency. Reconciliation drill injects a fixture-only
  mismatch and proves execution halts. Backup drill restores to a separate
  temporary database and compares schema/count/hash evidence.

- [ ] **Step 4: Capture evidence daily without editing past records.**

  ```bash
  evidence="$HOME/.local/state/trading-agent/promotion/g4/$(date -u +%Y-%m-%d)"
  uv run python scripts/capture_g4_evidence.py \
    --database-env "$HOME/.config/trading-agent/paper-candidate-reader.env" \
    --release-authority /etc/trading-agent/runtime-authority.json \
    --output "$evidence"
  ```

  The reader role is read-only. Output excludes DSNs, credentials, raw order
  payloads, prompts, and personal data.

- [ ] **Step 5: Verify the completed window.**

  ```bash
  uv run pytest -q tests/promotion/test_g4_paper_operation.py
  uv run python scripts/verify_g4_window.py \
    --evidence-root "$HOME/.local/state/trading-agent/promotion/g4" \
    --policy ops/promotion/paper-policy-v1.json
  ```

  Expected final decision: `G4 PASS`. Anything else stops production promotion.

- [ ] **Step 6: Commit tooling/runbooks, not mutable evidence.**

  ```bash
  git add scripts/capture_g4_evidence.py scripts/verify_g4_window.py \
    tests/promotion/test_g4_paper_operation.py docs/production/runbooks \
    docs/production/g4-operator-checklist.md
  git commit -m "ops: add G4 paper evidence and drills"
  ```

### Task 12: Build Release Authority v2 from the canonical monorepo

**Files:**
- Modify: `packages/runtime_release/config.py`
- Modify: `packages/runtime_release/manifest.py`
- Modify: `packages/runtime_release/provisioning.py`
- Modify: `packages/runtime_release/backend_policy.py`
- Create: `packages/runtime_release/v2.py`
- Create: `ops/release-v2/build-stage.sh`
- Create: `ops/release-v2/verify-stage.py`
- Create: `ops/release-v2/provision-root.sh`
- Create: `ops/release-v2/rollback.sh`
- Create: `tests/runtime_release/test_v2.py`
- Create: `tests/runtime_release/test_v2_provisioning.py`
- Create: `docs/production/release-authority-v2.md`

**Interfaces:**
- Produces a sealed offline stage containing application, backend research,
  dashboard, lockfiles, generated contracts, unit files, semantic/model policy,
  and exact interpreter identities from one canonical commit.
- Produces a protected runtime authority document with no digest supplied by
  mutable service environment variables.

- [ ] **Step 1: Write v2 rejection tests before changing release code.**

  ```python
  @pytest.mark.parametrize("mutation", [
      "extra_file", "missing_file", "symlink", "hardlink", "xattr",
      "wrong_owner", "writable_mode", "wrong_commit", "wrong_lock_hash",
      "wrong_python", "wrong_node", "wrong_contract_hash", "wrong_policy_hash",
  ])
  def test_v2_rejects_mutated_stage(mutation, staged_release):
      mutate(staged_release, mutation)
      assert verify_v2(staged_release).decision == "REJECT"
  ```

- [ ] **Step 2: Define the v2 canonical authority.**

  Authority binds:

  ```text
  canonical Git commit and tree
  component subtree hashes
  root/backend uv.lock hashes
  dashboard package-lock hash
  CPython and Node identities
  generated OpenAPI/JSON Schema/TypeScript hashes
  Alembic exact head
  command manifest
  safety source fingerprint
  paper-risk-v1 policy hash
  model registry/policy hash
  systemd unit hashes
  standalone verifier hash
  stage absolute path and seal version
  ```

- [ ] **Step 3: Build offline without copying the mutable worktree.**

  `build-stage.sh` exports the exact Git object, installs only from lockfiles,
  builds the dashboard, removes caches, creates complete-set manifests, verifies
  zero links/special files, and seals read-only. No code inside the stage runs
  after the final seal.

  ```bash
  bash ops/release-v2/build-stage.sh \
    --repo /home/thenam176/projects/trading-agent \
    --commit "$(git rev-parse HEAD)" \
    --output "$HOME/.cache/trading-agent-release-v2"
  /usr/bin/python3 -I ops/release-v2/verify-stage.py \
    "$HOME/.cache/trading-agent-release-v2" \
    "$HOME/.cache/trading-agent-release-v2/staging-metadata.json"
  ```

- [ ] **Step 4: Test provisioning in an isolated fake root.**

  Verify atomic install, owner/mode enforcement, unit hash checks, authority
  recheck, partial-failure cleanup, old-release retention, and rollback. Tests
  must prove a rejected stage can never become the active symlink target.

- [ ] **Step 5: Run the complete local release gate.**

  ```bash
  make audit-release
  make check-contracts
  make test-all
  make build-dashboard
  uv run pytest -q tests/runtime_release
  ```

  Expected: all PASS from a clean worktree at one commit.

- [ ] **Step 6: Commit release tooling.**

  ```bash
  git add packages/runtime_release ops/release-v2 tests/runtime_release \
    docs/production/release-authority-v2.md
  git commit -m "ops: add canonical release authority v2"
  ```

  Root provisioning is not part of this commit and still requires Task 13
  approval.

### Task 13: Cut over to paper production with atomic rollback

**Files:**
- Create: `docs/production/paper-cutover-checkpoint.md`
- Create: `docs/production/paper-cutover-results.md`
- Create: `docs/production/paper-rollback-results.md`
- Create: `ops/production/preflight.sh`
- Create: `ops/production/smoke-paper.sh`
- Create: `ops/production/verify-rollback.sh`

**Interfaces:**
- Consumes: Tasks 0-12 PASS, sealed Release Authority v2, protected backups,
  protected env/signing keys, and explicit root/cutover approval.
- Produces: one paper-production deployment identity and a verified immediately
  previous release. It never changes either live flag to true.

- [ ] **Step 1: Run read-only preflight and stop on any drift.**

  ```bash
  bash ops/production/preflight.sh \
    --expected-commit "$(git rev-parse HEAD)" \
    --stage "$HOME/.cache/trading-agent-release-v2" \
    --require-gates G0,G1,G2,G3,G4
  ```

  Preflight verifies paper/paper, false/false live gates, inactive canonical kill
  switch, G0-G4 hashes, database head, backup freshness, loopback port plan,
  current service identities, disk capacity, previous release, and rollback
  command. It prints no environment values.

- [ ] **Step 2: Create and restore-test the production database backup.**

  Use the protected owner environment and `ops/postgres/backup.sh`; restore to a
  separate temporary database, verify Alembic head, table counts, sample hashes,
  roles, append-only triggers, and then drop only the temporary restore database.
  Record path, SHA-256, mode, size, and results—not the password.

- [ ] **Step 3: Obtain explicit approval for root provisioning and service
  cutover.**

  The approval must identify the stage metadata digest, target commit, backup
  digest, affected units, previous release, and rollback command. Without this
  exact approval, stop after preflight.

- [ ] **Step 4: Provision and start in dependency order.**

  Operator runs the reviewed root script. Start order is PostgreSQL readiness,
  Control API, Job API, safety exporter, semantic/model refresher, scheduler,
  worker, risk engine, paper execution gateway, dashboard. Every service must
  report the exact release identity before the next starts.

  Live flags remain false in every protected environment. Broker and exchange
  credentials are absent from the paper execution service.

- [ ] **Step 5: Run focused smoke without external trading calls.**

  ```bash
  bash ops/production/smoke-paper.sh \
    --expected-commit "$(git rev-parse HEAD)" \
    --expected-mode paper
  ```

  Smoke checks loopback listeners, health/readiness, dashboard auth roles,
  fixed-source metadata, one idempotent SNAPSHOT job, one signed paper intent,
  paper fill, reconciliation, metrics, and append-only audit. It asserts that
  no process opened a broker/exchange connection.

- [ ] **Step 6: Roll back on any failed smoke or identity mismatch.**

  Stop only candidate units, atomically repoint to the previous immutable
  release, restore compatible protected config, restart in dependency order,
  and run `verify-rollback.sh`. Database restore is used only when the reviewed
  schema rollback explicitly requires it; never improvise a downgrade.

- [ ] **Step 7: Record results and commit documentation.**

  ```bash
  git add docs/production/paper-cutover-checkpoint.md \
    docs/production/paper-cutover-results.md \
    docs/production/paper-rollback-results.md \
    ops/production
  git commit -m "ops: record paper production cutover"
  ```

  Final paper decision is `GO` only when current smoke and rollback proof both
  PASS. Otherwise record `NO_GO` without deleting evidence.

### Task 14: Prepare a separately approved live-limited ADR and canary

**Files:**
- Create only after G4 and paper cutover: `docs/adr/ADR-live-limited-v1.md`
- Create only after ADR approval: `packages/execution_contracts/live_policy.py`
- Create only after ADR approval: `services/execution_gateway/live_adapter.py`
- Create only after ADR approval: `tests/execution_gateway/test_live_policy.py`
- Create only after ADR approval: `tests/execution_gateway/test_live_adapter_contract.py`
- Create only after ADR approval: `docs/production/runbooks/live-emergency-stop.md`

**Interfaces:**
- Consumes: successful paper production observation plus a human-approved ADR
  containing one exact account identity hash, one route, one asset allowlist,
  no-withdrawal credential proof, leverage disabled, and explicit caps.
- Produces no live-capable release until every test and manual approval gate is
  satisfied. This task never flips live flags automatically.

- [ ] **Step 1: Draft the ADR with mandatory exact fields.**

  The ADR is rejected unless it includes:

  ```text
  broker/exchange legal entity and jurisdiction review
  dedicated subaccount identity hash
  credential capability proof: trade only, withdrawal disabled
  leverage/margin/derivatives disabled
  exact asset and order-type allowlist
  maximum per-order notional
  maximum daily gross notional
  maximum open positions and exposure
  daily loss and drawdown halt thresholds
  manual approval identity and expiry
  kill-switch owner and emergency contacts
  reconciliation interval and mismatch response
  canary duration, rollback rule, and evidence retention
  ```

  Missing any field means `ADR REJECTED`; no code work starts.

- [ ] **Step 2: Obtain separate legal/risk/operator approval.**

  Source approval, paper-production approval, and repository ownership do not
  count as live authorization. Approval must name the exact ADR revision and
  expire if account, credential, route, asset, cap, policy, or code changes.

- [ ] **Step 3: Write adapter contract tests using a local fake server only.**

  ```python
  def test_live_adapter_requires_valid_signed_plan_and_manual_approval():
      with pytest.raises(PlanRejected, match="MANUAL_APPROVAL_MISSING"):
          adapter.place(valid_plan, approval=None)

  def test_live_adapter_has_no_withdrawal_or_transfer_surface():
      public = {name for name in dir(LiveAdapter) if not name.startswith("_")}
      assert public <= {"place", "cancel", "close", "status", "reconcile"}
  ```

  Fake-server tests cover replay, duplicate, timeout ambiguity, partial fill,
  rejected order, stale approval, price band, cancel/close authorization, and
  response redaction. No provider endpoint is contacted.

- [ ] **Step 4: Implement one live adapter behind the existing gateway.**

  It consumes only verified signed plans, an unexpired manual approval, current
  safety evidence, and the one approved route. Credentials are injected into
  this service only, never dashboard/research/job processes. Unknown responses
  halt and reconcile; they are never retried as new orders.

- [ ] **Step 5: Build a live-capable immutable release but keep it disabled.**

  Release metadata binds the ADR hash, account identity hash, public signing
  key, caps, allowlist, adapter code, credential capability evidence hash, and
  both live flags as `false`. Complete offline/fake-server validation and a live
  emergency-stop tabletop drill.

- [ ] **Step 6: Request a second explicit approval for the canary.**

  The request states the exact maximum possible loss under the ADR caps, release
  digest, observation window, operator, rollback, and kill-switch command. No
  approval means live remains disabled indefinitely.

- [ ] **Step 7: If approved, run the smallest ADR-defined canary and stop on
  first anomaly.**

  Only the operator changes the protected live flags. One approved plan is
  allowed at a time. Any mismatch, timeout ambiguity, SLO breach, auth failure,
  unexpected balance/position, or policy denial reactivates the kill switch,
  returns to paper, and requires a new approval after investigation.

- [ ] **Step 8: Record evidence and return live flags to false after the
  canary window.**

  Live continuation is a new promotion decision, not an automatic consequence
  of one successful order. Preserve redacted audit/reconciliation evidence and
  keep withdrawal capability absent.

### Task 15: Run the final centralized production audit and promotion decision

**Files:**
- Create: `docs/production/final-production-assessment.md`
- Create: `docs/production/final-risk-register.md`
- Create: `docs/production/final-promotion-decision.json`
- Update: `docs/production/promotion-status.json`

**Interfaces:**
- Consumes all task evidence and the exact candidate release.
- Produces exactly one decision: `GO_PAPER_PRODUCTION`,
  `GO_LIVE_LIMITED` with an approved ADR, or `NO_GO`.

- [ ] **Step 1: Re-run the complete clean-source gate.**

  ```bash
  make audit-release
  make check-contracts
  make test-all
  make build-dashboard
  out="$HOME/.local/state/security-audits/trading-agent/final-$(git rev-parse HEAD)"
  tree="$out/current-tree"
  mkdir -p "$tree"
  git archive HEAD | tar -x -C "$tree"
  gitleaks detect --source "$tree" --no-git --no-banner --redact
  gitleaks detect --source /home/thenam176/projects/trading-agent-publication \
    --no-banner --redact
  uv run pytest -q tests/security tests/promotion tests/runtime_release \
    tests/execution_contracts tests/risk_engine tests/execution_gateway tests/telemetry
  ```

  Before this command, rebuild the publication repository from the final HEAD
  using Task 1's approved archive/init process so it contains the complete final
  source snapshot. Expected: zero failure/error, zero current-tree secret, and
  zero secret in the sanitized publication history. The private canonical
  history remains unchanged and is validated against the exact rotated-history
  baseline rather than erased.

- [ ] **Step 2: Complete centralized security validation and attack-path
  analysis.**

  Reconcile all durable discovery candidates, validate source-to-sink reachability,
  record suppressions with evidence, model credential compromise, dashboard-to-job,
  research-to-model, plan-to-gateway, and gateway-to-adapter attack paths, and
  produce a final security report. Discovery artifacts alone do not satisfy this
  step.

- [ ] **Step 3: Verify current runtime against the immutable candidate.**

  Read-only checks compare service executable/cwd, commit, manifest, interpreter,
  database head, safety evidence, policy/model hashes, dashboard identity,
  worker/scheduler heartbeats, reconciliation, backup age, and SLOs. Values must
  match the candidate exactly.

- [ ] **Step 4: Close the risk register.**

  Every P0 is `CLOSED` with evidence. P1 items are either closed or explicitly
  accepted by a named owner with expiry and compensating control. No security,
  data-integrity, reconciliation, or live-safety P1 may be accepted for
  convenience.

- [ ] **Step 5: Emit the promotion decision.**

  `GO_PAPER_PRODUCTION` requires Tasks 0-13, including Task 2A, to PASS.
  `GO_LIVE_LIMITED` additionally requires Task 14's two explicit approvals and
  current evidence. Any missing, stale, mismatched, or unverifiable input emits
  `NO_GO` with reason codes.

- [ ] **Step 6: Commit the final report; remote publication remains a separate
  approved action.**

  ```bash
  git add docs/production/final-production-assessment.md \
    docs/production/final-risk-register.md \
    docs/production/final-promotion-decision.json \
    docs/production/promotion-status.json
  git commit -m "docs: record final production promotion decision"
  ```

## Definition of Done

Paper production is complete only when:

- current source and sanitized publication snapshot contain no confirmed secret;
- all mutation sinks enforce authorization and no live adapter is deployed;
- all locked dependency graphs pass the approved security policy;
- every test, typecheck, lint, contract, migration, and build gate passes in CI;
- deterministic intent/risk/plan/signature/idempotency lineage is complete;
- reconciliation, metrics, alerts, backup/restore, and rollback drills pass;
- G0-G4 evidence is current and bound to one exact release;
- Release Authority v2 attests the canonical monorepo;
- paper cutover and rollback proof are recorded; and
- final decision is `GO_PAPER_PRODUCTION`.

Live-limited is complete only after all paper criteria plus an approved exact
ADR, fake-server security tests, immutable live-capable release, two separate
operator approvals, a bounded canary, and final `GO_LIVE_LIMITED`. At no point
does a passing test or G4 result itself enable live trading.
