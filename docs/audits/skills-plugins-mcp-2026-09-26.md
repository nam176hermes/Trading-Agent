# Đánh giá và tối ưu skills, plugins, MCP — 2026-09-26

## Cập nhật: loại trừ công cụ theo yêu cầu operator

Operator đã yêu cầu loại các công cụ không cần thiết khỏi Trading-Agent.
Đã thêm 112 skill exclusions vào `.codex/config.toml`: 67 life-science/clinical/
lab/physical-science, 3 personal/screen-analysis, 11 marketing/design, 3 workflow/
PDF trùng lặp, 21 skill của Canva/CircleCI/Hyperframes/Supabase/Android và 7 skill
artifact thuộc bốn plugin đã tắt trước đó. Không xóa thư mục skill dùng chung.

DeepWiki tiếp tục disabled; Sequential Thinking chuyển sang opt-in. Hai app ID
Canva/Supabase được đặt `enabled=false` riêng cho project. Giữ Codebase Memory,
Serena, Context7, GitHub read-only, Playwright, host tools và các skill quantitative.
Các plugin do workspace quản lý không bị uninstall hoặc đổi trạng thái toàn cục.

**Cách mở phiên CLI đã lọc:**

```bash
python3 scripts/codex_project.py
```

CLI 0.147.0 đọc project skill config nhưng vẫn đưa skill bị tắt vào prompt.
Đã tái hiện cả path thư mục và path `SKILL.md`; override qua CLI loại đúng skill.
Launcher 42 dòng dùng stdlib, giữ user skill denials và chỉ đưa các project tool
denials lên CLI arguments. Không thay model, sandbox, approval policy hoặc file
config global. Profile này không tự thay phiên desktop đang chạy; chưa có bằng
chứng desktop mới áp dụng cùng bộ lọc. Không gọi đây là gỡ plugin khỏi account.

Native probe ở worktree ghi nhận đủ 112 skill có `enabled=false`; prompt render
còn 91 mục và các core-skill assertions PASS. `skills/list` còn có catalog remote
nên tổng mục API và tổng mục prompt là hai đại lượng khác nhau. Sau tích hợp,
catalog CLI local có 183 mục, 91 disabled và 92 enabled; 21 exclusion của plugin
remote không nằm trong lần liệt kê local đó. Prompt có 91 mục. Cả 19 focused
checks và Python static (Ruff, root/legacy Basedpyright) PASS ở checkout chính.
Kết quả và giới hạn desktop được lưu ở evidence directory:
`/home/thenam176/projects/trading-agent-optimization-evidence/tool-scope-20260926-t9xc9o8g`.
Không suy token/quota/quality gain từ số mục giảm. Skill paths gắn với host/version;
cần kiểm tra lại exclusions khi nâng cấp plugin hoặc chuyển máy.

Kiểm tra bảo toàn: config global, hooks, helper PR đã vá và toàn bộ file skill
thuộc danh sách loại trừ giữ nguyên. Bốn `SKILL.md` dưới `.system` (imagegen,
openai-docs, plugin-creator, skill-creator) có hash khác snapshot trước native
probe; không có thao tác sửa tay những file đó. Chưa quy kết chính xác cơ chế
refresh; giữ nguyên trạng thái do hệ thống quản lý, không rollback chúng.

Phần đánh giá ban đầu và trạng thái “chưa thay cấu hình” dưới đây được giữ làm
lịch sử, đã được cập nhật bởi mục này. Không commit/push/deploy hoặc đổi runtime.

> Cập nhật sau phê duyệt ngày 26/09/2026: đã cài đúng candidate vào shared
> `gh-address-comments/scripts/fetch_comments.py`; hash trước cài khớp và đã
> sao lưu. Cả 5 kiểm tra offline chạy trên **file đã cài** đều PASS; chưa gọi
> GitHub thực. Thread trên 100 comment vẫn fail rõ ràng, chưa có nested pagination.
> Receipt và backup: evidence directory bên dưới, `install-buccpj0i/receipt.json`.
> Các trạng thái “chưa cài/chờ duyệt” trong phần đánh giá ban đầu dưới đây là lịch sử.

## Kết luận và phần đã thực hiện

Trading-Agent đã có đủ năng lực engineering cốt lõi. Ưu tiên là chọn đúng skill,
kiểm tra công cụ có hoạt động trong phiên và sửa bộ thu thập bằng chứng PR.
Chưa có căn cứ để cài thêm một bộ harness, gỡ toàn bộ scientific skills hoặc
đổi model nhằm tiết kiệm quota.

Đợt này đã tối ưu bảng chọn skill và cách xử lý xung đột trong
[development workflow](../development-workflow.md#project-tool-and-skill-selection).
Đã chuẩn bị bản vá có kiểm tra offline cho helper `gh-address-comments`, nhưng
**chưa cài vào thư mục skill dùng chung**. Không thay cấu hình máy, permissions,
hook, MCP server, phiên bản dependency, source sản phẩm, CI hoặc authority.

| Phạm vi | Định danh / giới hạn |
| --- | --- |
| Checkout | `/home/thenam176/projects/trading-agent` |
| HEAD | `8ee1c18a5b99bca78738b6d5ca572bd0afb7c0b3` |
| Tree của HEAD | `2152a2e882d1fe8e2ef0392b1c38b18e78be7929` |
| Baseline thực tế | HEAD cộng dirty/untracked có sẵn; hash trong `baseline.json` |
| File dự án sửa trong đợt này | `docs/development-workflow.md` và báo cáo này |
| CLI đã kiểm tra | `codex-cli 0.147.0` |
| CODEX_HOME của shell | `/mnt/c/Users/thenam/.codex` |
| Evidence ngoài Git | `/home/thenam176/projects/trading-agent-optimization-evidence/skills-mcp-20260926-5ti9wqg2` |
| Đánh giá | Source/config inspection và kiểm tra offline; không phải benchmark chất lượng hoặc audit bảo mật toàn bộ toolchain |

Báo cáo harness trước đó là đầu vào đối chiếu, không phải kết quả chạy mới.
Các kết luận bên dưới dựa trên file và công cụ đọc trong phiên này. Không dùng
thống kê lịch sử để khẳng định tool nào thường xuyên được sử dụng.

## 1. Kiểm kê: phân biệt file, cấu hình và công cụ thực tế

| Lớp | Kết quả quan sát |
| --- | --- |
| Skill trực tiếp trong Codex custom root | 23 thư mục; quét đệ quy gồm system tìm thấy 29 `SKILL.md` |
| Scientific/shared agent skills | 137 `SKILL.md` trong `/home/thenam176/.agents/skills` |
| Plugin cache | 142 `SKILL.md`; cache không chứng minh plugin đang enabled |
| Tổng file skill được kiểm kê | 308; có path, tên, byte size và SHA-256 trong `skills-inventory.json` |
| Tool catalog của phiên | 512 tên tool; 383 thuộc connector namespace `mcp__codex_apps__` |
| Cấu hình user đang được shell dùng | Astra `max`; 11 plugin entry enabled; 9 MCP entry, gồm `cua_repl` disabled |
| Cấu hình WSL ở `~/.codex/config.toml` | Astra `high`, Superpowers enabled; là file khác với CODEX_HOME hiện tại |
| Cấu hình project | Astra `high`, giữ `goals=false`, Serena theo cwd, DeepWiki và bốn artifact plugin disabled |

Số file skill không phải số skill đã được load đầy đủ. Theo tài liệu chính thức,
skill được nạp dần: catalog bắt đầu bằng tên/mô tả; nội dung đầy đủ được đọc khi
sử dụng. Không quy đổi các số đếm này thành token tính phí, quota hoặc phần trăm
tăng chất lượng. [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills).

Các mức trạng thái cần giữ riêng: **có trên đĩa → được cấu hình → callable trong
phiên → đã gọi thành công → hành vi đã được kiểm chứng**. Metadata hay cấu hình
không tự nâng lên mức cuối. Không thu thập nội dung account, email, Drive, Jira,
Linear hoặc dữ liệu vận hành để kiểm kê tool.

## 2. Findings có bằng chứng

| Ưu tiên | Finding | Bằng chứng / tác động | Xử lý |
| --- | --- | --- | --- |
| P1 | Helper PR dùng repo đầu nhánh thay vì repo nhận PR | `get_current_pr_ref()` đọc `headRepositoryOwner/headRepository`; fixture fork trả `contributor/fork/7` thay vì `base/repo/7` | Candidate lấy destination từ canonical PR URL; chưa cài global |
| P1 | Helper PR khởi động lại connection đã đọc hết | Fixture comments/reviews/threads có 2/3/1 trang: 6 query; trả 6/6/6 item, nhưng unique chỉ 2/3/1 | Candidate đánh dấu connection hoàn tất và giữ cursor; 3 query, đúng 2/3/1 item |
| P1 | Thread dài bị cắt mà không có completeness signal | Nested `comments(first: 100)` không có `pageInfo` và không phân trang tiếp | Candidate thêm `hasNextPage`, từ chối kết quả thiếu; nested pagination đầy đủ còn chưa triển khai |
| P2 | Tool engineering quan trọng chưa callable | Phiên có 0 tool Serena và 0 tool GitHub, dù `codex mcp list --json` báo enabled | Dùng source/rg và read-only gh theo task; xác minh phiên mới trước khi tin MCP |
| P2 | CLI plugin inventory lỗi qua WSL | `codex plugin list --json` exit 1 vì marketplace Windows không có manifest mà CLI đọc được | Không dùng lệnh lỗi để kết luận plugin chưa cài; giữ riêng cache/config/runtime evidence |
| P2 | Context7 chưa ghim phiên bản thực thi | Wrapper chạy `npx -y @upstash/context7-mcp` không có version | Đề xuất pin wrapper dùng chung sau phê duyệt và kiểm tra compatibility; giữ usage guard |
| P2 | Workflow skill có chỉ dẫn không đồng nhất | Superpowers yêu cầu nạp skill rất rộng; Code Work có adaptive parallelism; property skill có `effort: low`; comment skill yêu cầu escalation | Workflow dự án nay xác định precedence, một workflow chính và giữ operator selection |
| P3 | Trùng tên `pdf`, nhiều design skill cạnh tranh | Có `pdf` trong cả Codex custom root và `.agents/skills`; taste v1 tồn tại cạnh v2 | Chọn exact path theo task; không xóa bản dùng cho dự án khác |
| P3 | Catalog gồm nhiều nhóm không phục vụ Core engineering | Scientific/lab/clinical/design/artifact/service skills cùng được giới thiệu | Dùng theo yêu cầu cụ thể; không tự đọc toàn bộ hoặc cài thêm thư viện |

P1 ở đây là độ ưu tiên sửa bộ công cụ thu thập bằng chứng, không phải phân loại
lỗ hổng sản phẩm hay sự cố giao dịch. Fixture PR hoàn toàn giả, không gọi GitHub.
GraphQL yêu cầu phân trang từng connection và dùng `hasNextPage/endCursor` để
xác định còn dữ liệu. [GitHub pagination](https://docs.github.com/en/graphql/guides/using-pagination-in-the-graphql-api).
CLI có trường JSON `url` để xác định PR. [gh pr view](https://cli.github.com/manual/gh_pr_view).

## 3. Bộ skill nên dùng cho dự án

| Skill / nhóm | Quyết định | Áp dụng cụ thể / giới hạn |
| --- | --- | --- |
| Code Work | Workflow mặc định khi triển khai | Scope, trace, sửa nguyên nhân, kiểm tra hẹp; không tự sinh agent |
| Code Verification | Review theo nhiệm vụ | Không gọi self-review là independent; không dùng summary của worker thay source |
| Ponytail full | Giữ | Reuse/stdlib/minimal diff; không giảm kiểm tra money, security, recovery hoặc authority |
| Superpowers | Theo nhu cầu hoặc chỉ định | Chọn stage debugging/TDD/design phù hợp; không ghép thêm cả lifecycle lên Code Work |
| `property-based-testing` | Ưu tiên cao khi có invariant | Decimal canonicalization, roundtrip, replay determinism, provenance/path parsing; giữ selected effort |
| `gh-fix-ci` | Khi PR có GitHub Actions failure | Xác định PR/head/run, lấy log trước sửa; skill không cấp quyền thay CI hoặc remote |
| `gh-address-comments` | Giữ mục tiêu, hạn chế helper hiện tại | Chỉ xử lý scope đã được chọn; collector cần đúng destination, unique IDs và đủ pagination |
| Codex Security | Khi diff chạm boundary nhạy cảm | Dùng scope phù hợp; không gửi findings ra issue tracker nếu chưa được phép |
| `planning-contract` | Khi yêu cầu plan/spec hoặc design authority chưa được duyệt | Không thêm vòng xin phép cho implementation đã được chấp thuận |
| `repo-researcher` | Audit/architecture có chủ đích | Không biến lookup một file thành whole-repo review |
| React/Next.js/Playwright | Khi sửa dashboard | Installed guide và component AGENTS trước; kiểm tra workflow UI được sửa |
| `design-taste-frontend` | Không dùng mặc định cho dashboard | Bản hiện tại ghi rõ dành landing page/portfolio/redesign và loại trừ dashboard/data table |
| taste v1, brutalist, brandkit, gpt-taste, image-to-code, imagegen-frontend | Chỉ khi có brief thiết kế tương ứng | Không cộng dồn aesthetic rules lên màn hình điều khiển |
| scientific/statistical/forecasting/ML | Thí nghiệm research đã có mục tiêu | Chọn đúng thư viện đang dùng; không suy từ chữ trading thành nhu cầu ML/quantum/bio |
| lab/clinical/molecular/genomics skills | Ngoài scope Core hiện tại | Có thể phục vụ dự án khác; không gỡ global chỉ vì không phù hợp repo này |
| PDF/docx/xlsx/pptx/visualization | Artifact được yêu cầu | PDF chọn đúng source; không tự mở plugin tạo slide cho task source |
| `full-output-enforcement` | Explicit complete-output task | Giữ yêu cầu đầy đủ, bỏ suy diễn rằng mọi task đều production-critical |
| ECC/operator-system | Tham khảo khi đánh giá harness | Không cài nguyên bộ; không thêm planner/hook/eval framework khi thiếu nhu cầu |
| `autoskill` | Không dùng trong đợt này | Skill dựa trên screenpipe; user không yêu cầu phân tích dữ liệu màn hình |
| Skill installer/creator, Plugin Management, OpenAI Docs | Bảo trì tooling theo task | Chọn capability đang có; không tìm/cài plugin chỉ vì catalog lớn |

Hypothesis đã có trong root dev group, pin `6.151.9`; không cần dependency mới.
Repo hiện có sáu file root tests import Hypothesis, gồm
`tests/domain/test_decimal_properties.py`, `tests/domain/test_instrument_definitions.py`,
`tests/event_ledger/test_replay.py`, `tests/event_ledger/test_reducer.py`,
`tests/p1_nautilus/test_target_planner.py` và
`tests/governance/nautilus_pin_inventory/test_text_and_path.py`.
Đây là discovery bằng import text và đọc mẫu source, không phải coverage report.

Không sửa nội dung cached plugin: update có thể thay thế file và ảnh hưởng dự án
khác. Không thêm 137 absolute-path disable entries vào cấu hình portable của repo.
Tài liệu hỗ trợ `skills.config`, nhưng cần kiểm tra effective discovery trước khi
coi disable là đã giảm catalog; hiện chưa thực hiện thay đổi đó.
[OpenAI configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## 4. MCP: giữ năng lực nào và kiểm chứng ở đâu

| MCP / capability | Phiên này | Quyết định |
| --- | --- | --- |
| Codebase Memory | 15 tool callable; `list_projects` và `check_index_coverage` thành công | Giữ cho architecture/call graph; luôn bind đúng checkout |
| Serena | Config enabled, binary có trên PATH, 0 tool callable | Giữ `--project-from-cwd`; chưa quy kết nguyên nhân mất tool; dùng source fallback |
| Context7 | 2 tool callable; không query thư viện trong đợt này | Giữ cho version gap sau installed docs; không gửi private source |
| GitHub MCP | Config enabled; wrapper dùng `--read-only` và bốn toolset; 0 tool callable | Giữ quyền đọc; gh fallback với PR/repo/SHA rõ ràng |
| Playwright | 23 tool callable; pin `@playwright/mcp@0.0.76`, headless | Giữ; chỉ local isolated UI workflow, chưa chạy browser ở đợt này |
| Sequential Thinking | 1 tool callable; pin `2025.12.18` | Dùng cho vấn đề nhiều giả thuyết thực sự, không bắt buộc cho task đơn giản |
| DeepWiki | Project disabled; 0 tool callable | Tiếp tục opt-in khi nghiên cứu upstream cụ thể |
| Node/Computer Use | Host plumbing có trong config/catalog | Không tắt dựa trên số đếm; có thể phục vụ browser/app, không kiểm thử desktop ở đợt này |
| Codex Security MCP | 22 tool callable | Giữ cho review boundary; inventory này không phải security review PASS |
| Connector service tools | Nhiều tool callable qua workspace | Khả năng gọi không phải quyền đọc account hoặc mutation; chưa truy cập dữ liệu dịch vụ |

Codebase Memory báo metadata match cho `scripts/dev.py`,
`tests/foundation/test_agent_harness.py`, `packages/runtime_risk/evaluator.py`.
Generation `2026-09-26T09:05:05Z`, coverage recorded `2026-09-26T15:07:56Z`.
Chỉ ba path này được kiểm tra; metadata freshness là best effort, không chứng
minh graph completeness. Không rebuild index hoặc thêm watcher.

`codex mcp list --json` exit 0 chứng minh CLI đọc effective MCP config, gồm
Serena arguments mới và DeepWiki disabled. `auth_status=unsupported` cho stdio
không đồng nghĩa authentication failed. Không initialize server ngoài phiên,
gọi gh auth token, login hoặc làm lộ credential để kiểm tra kết nối.

Context7 usage guard đang đếm local requests và dùng threshold nội bộ; không
coi đó là provider billing/quota hiện hành. Source dùng read/modify/write không
có lock liên tiến trình, nên độ chính xác khi nhiều process cùng ghi chưa được
chứng minh. Chưa chạy reproduction concurrency cho guard; không gọi đây là bug
đã tái hiện hoặc sửa ngoài phạm vi.

## 5. Plugin và hook: điều gì nên giữ nguyên

| Nhóm | Xử lý đề xuất |
| --- | --- |
| Engineering Guardrails, Codex Security, Ponytail | Giữ; vai trò khác nhau nhưng chỉ một workflow điều phối |
| Superpowers | Stage theo nhu cầu; Codex cached manifest `hooks: {}` không chứng minh có SessionStart injection của chính plugin |
| presentations, spreadsheets, LaTeX, template-creator | Giữ bốn project override disabled sẵn có; không nhận đây là thay đổi mới |
| documents, PDF, visualize | Theo artifact cụ thể; chưa đo lợi ích tắt nên giữ cấu hình |
| computer-use, unified-computer-use, codex-app-tools | Giữ host integration; không suy hai tên computer-use là hai process trùng chức năng |
| Build Web Apps / Data Visualization | Chỉ phần phù hợp dashboard; không scaffold app mới hoặc kéo thêm Stripe/Supabase |
| Sentry | Có `sentry-sdk[fastapi]` trong root manifest; dùng read-only cho incident được yêu cầu |
| CircleCI, Cloudflare, Supabase, Android, Hyperframes | Không bật workflow mặc định cho Core; chưa thấy dependency tương ứng trong ba manifest đã kiểm tra |
| Canva, Google Drive, templates, Slack và app integrations | Chỉ task có yêu cầu dịch vụ; cache hiện diện không chứng minh connected hoặc được dùng |
| OpenAI Developers / usage / Plugin Management | Theo yêu cầu tooling/API/usage; không tự setup API key hay tiêu reset credit |

Plugin key dùng `plugin-name@marketplace-name`, không suy từ tên thư mục cache.
Project `enabled=false` không uninstall và có thể vẫn cho phép refresh cache;
connector runtime còn có lớp quản lý riêng.
[OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins).

Lỗi inventory đã kiểm tra trên cả `codex plugin list --json` và
`codex plugin list --marketplace ponytail --json`: exit 1, marketplace source
`\\?\C:\Users\thenam\.cache\codex-runtimes\codex-primary-runtime\plugins\openai-primary-runtime`
không có manifest mà CLI nhận ra. Thử override source sang `/mnt/c/...` bằng `-c`
cũng chưa giải quyết. Không sửa global path: cùng cấu hình đang phục vụ Windows
desktop, đổi đường dẫn tùy tiện có thể làm hỏng phía đang dùng được. Chưa xác
định root cause giữa path conversion, schema và marketplace packaging.

Hook đã đọc ở mức configuration:

- Codex global: Serena `SessionStart activate`, `PreToolUse remind`, `Stop cleanup`.
- Ponytail: `SessionStart`, `UserPromptSubmit`, `SubagentStart`, timeout 5 giây.
- Claude global: observational-memory tại `SessionStart`, `SessionEnd`,
  `UserPromptSubmit`, `PreCompact`; enabled Ponytail và Superpowers.

Không có bằng chứng mới về latency thực, tần suất hook fire hoặc correctness của
observational-memory. Không chạy hook thủ công, không tự gỡ hay nhân đôi hook.
Giữ quy tắc không gắn full suite/HELD retry vào lifecycle hooks.

## 6. Bản vá helper dùng chung: sẵn sàng xem xét, chưa cài

Target duy nhất đề nghị ghi đè sau phê duyệt:
`/mnt/c/Users/thenam/.codex/skills/gh-address-comments/scripts/fetch_comments.py`.

| Artifact trong evidence directory | Nội dung |
| --- | --- |
| `before/fetch_comments.py` | Snapshot helper đang cài |
| `candidate/fetch_comments.py` | Candidate sửa destination, pagination, malformed URL/stuck cursor và phát hiện long thread |
| `gh-address-comments.patch` | Unified diff để review |
| `test_fetch_comments.py` | Năm kiểm tra unittest offline, stdlib-only |
| `before-tests.log` / `candidate-tests.log` | RED: 5 failures, exit 1; GREEN: 5 passed, exit 0 |
| `candidate-manifest.json` | Hash nguồn, candidate, patch và trạng thái PROPOSED_NOT_INSTALLED |

Original SHA-256: `d7bc64f6b26f7482f9ca9dd7c923a5b8b20e1ef13491ae2d9052cd208a7c1ad1`.
Candidate SHA-256: `be7d8a7cd4e1930b5d891fc42ba0c4db52f6cee7836092b4e415d5ef9be38b6a`.

Giới hạn có chủ đích: thread hơn 100 comment sẽ fail rõ ràng; candidate chưa
thu toàn bộ nested pages. Vì vậy bản vá khôi phục tính trung thực của evidence,
không tuyên bố hỗ trợ mọi kích thước PR. Completed top-level connections vẫn
nằm trong query chung nhưng không được append/restart lại. Chưa kiểm tra GitHub
live, GHES hoặc repo thực. Không có mutation route mới.

Nếu được duyệt: kiểm tra installed hash còn khớp, backup, áp đúng candidate,
chạy lại năm kiểm tra với installed path; báo rõ phiên bản được local-patch.
Nếu hash drift thì review thay đổi mới trước, không ghi đè. Rollback bằng đúng
snapshot trước cài, chỉ khi không có chỉnh sửa tiếp theo cần giữ. Không auto-update
hoặc reinstall skill vì có thể ghi đè local patch.

Quyền cài chưa có: mục **Ask first** trong global AGENTS do operator cung cấp
yêu cầu hỏi trước khi “Changing global tool behavior, shared config”. Việc sửa
workflow riêng của Trading-Agent không tự cấp quyền thay script dùng bởi mọi repo.

## 7. Kiểm chứng, tiêu chí nghiệm thu và việc còn lại

Đã chạy mới: đọc TOML/JSON; Codex version; effective MCP listing; plugin-list
failure reproduction; Codebase Memory coverage trên ba path; helper fixtures;
năm RED/GREEN checks và AST parse cho candidate/test. Diff, relative links và
hash preservation được kiểm tra riêng, kết quả trong `validation.json`.

Lệnh tái chạy candidate, không gọi network:

```bash
python3 /home/thenam176/projects/trading-agent-optimization-evidence/skills-mcp-20260926-5ti9wqg2/test_fetch_comments.py /home/thenam176/projects/trading-agent-optimization-evidence/skills-mcp-20260926-5ti9wqg2/candidate/fetch_comments.py
```

Không chạy product tests/static/build/aggregate: source sản phẩm không đổi;
docs-only thay đổi đã có checks phù hợp. Không tái sử dụng kết quả 156/21 test
từ audit trước thành kết quả phiên này. Không commit, stage, push, publish, đổi
database/service, query broker hoặc thay live approvals. Source qualification,
hosted CI, runtime và live status không được nâng lên bởi audit tooling này.

Thứ tự đề nghị:

1. Phê duyệt candidate helper dùng chung hoặc tiếp tục explicit paginated read
   queries; tiêu chí là đúng PR destination, không duplicate, không bỏ comment.
2. Kiểm tra Serena/GitHub trong một phiên mới và sửa lỗi plugin inventory theo
   đúng host. Chỉ đạt khi tool thực sự callable/initialize thành công và root đúng.
3. Pin Context7 sau khi chọn phiên bản đã kiểm tra, giữ guard và rollback. Đây
   là shared-tool change riêng, chưa có candidate version được qualified.
4. Chỉ giảm catalog/plugin khi có task mẫu cho thấy chọn nhầm hoặc overhead.
   So sánh cùng task, checkout, model/effort, quyền, acceptance checks; đo lỗi
   bỏ sót, diff ngoài scope, số lần hỏi, duration và tool calls. Không hứa quota gain.

Không cần cài thêm skill/framework ở thời điểm này. Optimization đã áp dụng là
routing cụ thể và hạn chế nguồn evidence bị lỗi; candidate global vẫn chờ duyệt.
