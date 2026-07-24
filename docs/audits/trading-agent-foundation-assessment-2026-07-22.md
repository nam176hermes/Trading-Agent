# Trading Agent Foundation Assessment

**Ngày đánh giá:** 2026-07-22  
**Repository:** `/home/thenam176/projects/trading-agent`  
**Commit:** `e8166622a181307c5aa5869f5900d9845f294e83`  
**Branch:** `codex/canonical-monorepo`  
**Phạm vi:** Source foundation, paper-only safety, CI, tests, release readiness và runtime proof  
**Nguyên tắc:** Chỉ chấm theo kết quả test và bằng chứng vừa được chạy. Không suy đoán trạng thái production.

## Kết luận

**Điểm foundation hiện tại: 84/100.**

- **Source foundation:** 87.8/100 khi chỉ tính phần source.
- **Trạng thái:** **GO** cho phát triển tiếp và vận hành paper-only.
- **Production/runtime:** **NO-GO** cho cutover hoặc live trading.

Điểm được chấm từ kết quả test chạy tại commit nêu trên và worktree sạch. Điểm không dựa trên tài liệu tự tuyên bố.

## Bằng chứng test

| Gate | Kết quả thực |
|---|---:|
| `make ci` | **PASS**, exit 0 |
| Root Python | **2,058 passed, 226 skipped, 11 deselected**, 1 warning |
| Legacy research backend | **247 passed, 2 skipped** |
| Dashboard | **158 passed, 0 failed** |
| TypeScript | PASS |
| ESLint | PASS |
| Next.js production build | PASS |
| Contract drift | PASS |
| Secret hygiene | PASS |
| Bandit high severity | PASS, exit 0 |
| Production dependency audit | 0 known vulnerabilities |
| Dev dependency audit | 0 known vulnerabilities |
| `make audit-release` | PASS, clean repository |
| Host-coupled release test | **FAIL: 1 failed, 1 skipped** |
| Runtime PostgreSQL parity | Chưa chạy, matrix ghi `PENDING_APPROVAL` |

Nguồn bằng chứng:

- Output trực tiếp của `make ci`.
- Output trực tiếp của `make audit-release`.
- Output trực tiếp của `make test-runtime-release-host`.
- `docs/implementation/d0-closure-matrix.json:239-262`.

## Chấm điểm chi tiết

| Hạng mục | Điểm | Đánh giá |
|---|---:|---|
| Kiến trúc và ranh giới thành phần | **14/15** | Một Git authority; root, backend và dashboard có dependency graph riêng; integration qua PostgreSQL, protected-file, Control API và Job API. |
| Domain và data integrity | **14/15** | Fixed precision, strict contracts, deterministic replay, immutable event set, snapshot hash, outbox/inbox và contract drift đều có executable proof. Thiếu proof runtime PostgreSQL cho migration 0008. |
| An toàn và bảo mật | **17/20** | Worker fail-closed, fixed runtime roots, chống symlink, private modes, bounded body, role policy, same-origin, signed HttpOnly session, secret audit và dependency audit đều được test. Trừ điểm vì legacy source vẫn chứa live execution path. |
| Kiểm thử và bằng chứng | **16/20** | Test sâu, có property tests, security tests, contract tests, state-machine và replay tests. Trừ điểm vì số skip cao, chưa có coverage metric, runtime PostgreSQL bị loại khỏi CI và host release proof đang fail. |
| CI, build và dependency hygiene | **14/15** | Canonical `make ci` thực sự pass; build, lint, typecheck, Bandit và dependency audits đều sạch. Local dùng Node 24.14.1, GitHub Actions khai báo Node 22 nên môi trường chưa hoàn toàn giống nhau. |
| Runtime và release readiness | **5/10** | Hermetic source gates tốt, nhưng release artifact host-coupled chưa được chứng minh và PostgreSQL runtime parity chưa được xác nhận. |
| Maintainability, docs và observability | **4/5** | README, AGENTS, closure matrix, ADR, health/status contracts và logging tốt. Còn warning deprecation, module-type warning, TODO và các nhánh legacy nuốt exception để fallback. |
| **Tổng** | **84/100** | **Foundation tốt, chưa phải production-ready foundation.** |

## Điểm mạnh đã được chứng minh

### 1. Ranh giới paper-only được cưỡng chế bằng code

`services/job_worker/environment.py:243-257` tạo child environment mới và ép:

- `TRADING_MODE=paper`
- `LIVE_EXECUTION_ENABLED=false`
- `LIVE_TRADING_APPROVED=false`
- `LIVE_TRADING_ENABLED=false`

`services/job_worker/safety.py:156-178` chặn mọi trạng thái unknown, non-paper hoặc live gate bật.

### 2. Domain foundation không chỉ là schema

Closure matrix liên kết từng requirement với implementation path, test function và command thực thi. Test closure pass 3/3.

Nguồn: `tests/foundation/test_d0_closure.py:74-130`.

### 3. Dashboard có thiết kế fail-closed

Test xác nhận auth, role, origin, timeout, invalid JSON, oversized payload, symlink path và malformed persisted state đều bị chặn.

Nguồn: 158 dashboard tests trong `make ci`.

### 4. Supply-chain baseline sạch tại thời điểm kiểm tra

Pip-audit production/dev và npm audit đều báo 0 vulnerability. Bandit high severity exit 0.

Nguồn: `make ci`.

### 5. Repository hygiene tốt

`make audit-release` xác nhận đúng ba component, đúng HEAD và worktree sạch.

## Vấn đề thực tế

### P0: Host-coupled release proof đang fail

`make test-runtime-release-host` thất bại tại:

```text
test_actual_locked_app_build_is_offline_copied_symlink_free_and_runnable
```

Chẩn đoán trực tiếp cho thấy:

```text
uv_exit=1
Failed to download anyio==4.14.1
Network connectivity is disabled
requested data wasn't found in the cache
```

Nguyên nhân trực tiếp là offline cache trên host thiếu wheel `anyio==4.14.1`. Đây chủ yếu là vấn đề chuẩn bị release host, chưa đủ bằng chứng kết luận release builder sai.

Kết quả thực vẫn là:

**Không thể tuyên bố release build đã được chứng minh trên host hiện tại.**

Nguồn:

- `tests/runtime_release/test_build.py:185-237`.
- Output chẩn đoán `uv sync --offline`.

### P0: PostgreSQL runtime parity chưa có proof

Matrix ghi rõ:

```json
"runtime_postgres_parity": "PENDING_APPROVAL"
```

Target `make test-event-ledger-runtime-postgres` không được chạy vì yêu cầu disposable PostgreSQL hoặc operator approval. Vì vậy migration 0008, snapshot, retry, retention và inbox permanence mới có source-level proof, chưa có runtime database proof.

Nguồn: `docs/implementation/d0-closure-matrix.json:239-262`.

### P1: Số skip cao

- Root suite: 226 skipped.
- Backend: 2 skipped.

Một phần là intentional approval gates, PostgreSQL authority, missing host capabilities và external integration. Thiết kế skip có lý do an toàn, nhưng pass count lớn không đồng nghĩa toàn bộ behavior đã chạy.

### P1: Live capability vẫn tồn tại trong legacy source

`legacy/research-backend/live_execution_policy.py:44-72` cho phép live khi:

- Hai env gate đều true.
- Kill switch inactive.
- Risk preflight pass.
- Credentials và adapter sẵn sàng.

Paper-only hiện được bảo vệ bởi deployment, config và worker boundary, không phải bằng việc loại bỏ live code.

Đây không phải lỗi đang khai thác được theo test hiện tại. Đây là rủi ro cấu hình và governance cần giữ ở mức critical-control.

### P2: Nợ kỹ thuật quan sát được

- FastAPI TestClient báo Starlette/httpx deprecation.
- Contract generator báo TypeScript factory deprecation.
- Dashboard test báo `MODULE_TYPELESS_PACKAGE_JSON` nhiều lần.
- Chưa có coverage threshold trong canonical CI.
- `legacy/research-backend/reflection_engine.py` còn TODO cho benchmark alpha.
- Một số module legacy bắt exception rộng rồi fallback im lặng.

Các điểm này chưa làm gate fail, nhưng làm giảm khả năng phát hiện lỗi dài hạn.

## Kế hoạch nâng lên trên 90

### 1. Đóng host release proof

**Mức tăng dự kiến:** +3 điểm.

- Seed đầy đủ uv offline cache từ lockfile trong môi trường kiểm soát.
- Chạy lại `make test-runtime-release-host`.

Acceptance:

```bash
make test-runtime-release-host
```

Phải exit 0. Không chấp nhận failure bị giải thích bằng tài liệu.

### 2. Đóng PostgreSQL runtime parity

**Mức tăng dự kiến:** +4 điểm.

- Dùng disposable PostgreSQL fixture với approval record riêng.
- Không chạm operator-managed production DB.
- Chạy target event ledger runtime và các target PostgreSQL liên quan.

Acceptance:

```bash
make test-event-ledger-runtime-postgres
make test-runtime-postgres
make test-runtime-dual-read
```

Sau đó matrix chỉ được chuyển sang `PASS` nếu output test thực sự xanh.

### 3. Biến skip thành inventory được quản lý

**Mức tăng dự kiến:** +2 điểm.

- Phân loại skip theo approval, missing binary, host capability và external integration.
- Mỗi skip có owner và target riêng.
- CI fail nếu xuất hiện skip mới ngoài allowlist.

### 4. Thêm coverage cho vùng critical

**Mức tăng dự kiến:** +2 điểm.

Đặt branch coverage riêng cho:

- `packages/domain`
- `packages/event_ledger`
- `services/job_worker/safety.py`
- Dashboard auth và mutation policy
- Job state machine và transition authority

Không dùng một con số coverage toàn repo để che vùng critical thấp coverage.

### 5. Dọn warning và siết live boundary

**Mức tăng dự kiến:** +1 đến +2 điểm.

- Xử lý các deprecation và module-type warning.
- Thêm executable proof rằng canonical release không đưa live-capable legacy entrypoints vào active command catalog.
- Giữ hai live gate false tại nhiều lớp, không chỉ dựa vào một env file.

## Quyết định

**Foundation source đủ mạnh để tiếp tục xây dựng: GO.**

**Chưa đủ điều kiện gọi là production-ready hoặc cho cutover: NO-GO**, vì:

1. Host release test đang fail.
2. Runtime PostgreSQL parity chưa có bằng chứng.
3. Nhiều test bị skip bởi approval hoặc host capability.
4. Live-capable legacy code vẫn tồn tại và phụ thuộc critical configuration gates.

Sau khi đóng hai P0 bằng test thật, mức hợp lý có thể đạt khoảng **91/100**. Chỉ nên chấm lại sau khi các acceptance command exit 0.

## Giới hạn của lần đánh giá

Một subagent được giao audit tĩnh độc lập nhưng không chạy được vì provider `openai` của model `test1` thiếu credentials và trả HTTP 404. Kết quả subagent không được dùng làm bằng chứng hoặc làm thay đổi điểm số.

Báo cáo này là **single-controller verified**, không phải cross-agent verified. Mọi kết luận pass/fail trong báo cáo dựa trên output lệnh do controller trực tiếp chạy và kiểm tra.
