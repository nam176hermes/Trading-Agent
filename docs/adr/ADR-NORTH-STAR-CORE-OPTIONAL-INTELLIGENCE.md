# ADR: North Star — Core, Optional Intelligence và paper theo dependency

**Status:** Accepted roadmap delta; implementation and runtime qualification unchanged
**Date:** 2026-09-20
**Decision source:** Operator approval in the current Codex task:
“Phê duyệt delta roadmap: tách Core và Optional Intelligence, cho phép paper sớm theo dependency.”

## Cập nhật hiệu lực — 2026-10-15

Chủ dự án đã thông qua [North Star V4.0](../NORTH-STAR-V4.md) làm North Star gốc.
V4 là điểm bắt đầu cho định hướng và kế hoạch sản phẩm mới; mục 15 của V4 quy định
delta cùng các ràng buộc được bảo lưu. ADR này tiếp tục là bản ghi quyết định hỗ
trợ về Core/Optional, dependency, an toàn và economic/qualification gates.
Phần dưới lưu bối cảnh quyết định 2026-09-20; các chỉ dẫn dùng V3.1 làm điểm bắt
đầu lập kế hoạch đã được thay bởi V4. Không thay đổi hợp đồng P3 đang áp dụng,
receipt, candidate hay quyền runtime/live.

## Phạm vi và thứ tự ưu tiên

ADR này bổ sung [North Star V3.1, revision 2026-08-30](https://drive.google.com/file/d/1UBVyBNMkE7Sipq6oo5YgyLh-Yoy6jWco/view).
Nó thay đổi phạm vi sản phẩm đầu tiên, quan hệ phụ thuộc giữa các phase và cách
đọc Definition of Done. Những phần không được thay đổi rõ dưới đây vẫn giữ nguyên.
Đây không phải North Star V3.2 hoàn chỉnh và không phải receipt qualification.

Tại thời điểm quyết định 2026-09-20, kế hoạch dùng V3.1 cùng ADR này. Ba quyết định
được phê duyệt dưới đây có ưu tiên hơn thứ tự tuyến tính và DoD tổng hợp của V3.1;
từ 2026-10-15, điểm bắt đầu lập kế hoạch là V4 như cập nhật ở trên.
Các gate hiện có trong source vẫn phải được đáp ứng; ADR không tự sửa hoặc
bỏ qua validator, policy, phase receipt hay hợp đồng chiến dịch đã chấp nhận.

## Quyết định 1: Core Trading Product

Sản phẩm cốt lõi giúp người dùng kiểm chứng chiến lược trên dữ liệu đáng tin cậy,
xác định hiệu quả sau chi phí theo tiêu chí đóng băng trước, tái lập kết quả và
vận hành chiến lược đạt chuẩn trong phạm vi rủi ro có thể kiểm tra.

Core bao gồm:

- Canonical data identity, PIT/revisions, snapshot và provenance cần cho mandate.
- Baselines, deterministic alpha, OOS/holdout, costs, robustness và native parity
  theo hợp đồng chiến dịch được duyệt.
- TargetPortfolio, deterministic RiskDecision và execution qua engine được
  qualified; sizing và portfolio constraints cần thiết cho mandate.
- Event Ledger, independent accounting/reducer, reconciliation, kill switch,
  idempotency, recovery và bằng chứng có thể replay.
- Durable jobs, artifact lineage, operator control và khả năng quan sát cần thiết;
  tái sử dụng CLI/dashboard và owner boundaries hiện có.
- Paper/shadow qualification cho phạm vi triển khai được duyệt.

Không viết lại nền tảng đã có để thực hiện việc tách Core. Các yêu cầu P1-H/LTS,
P2 và Pre-P3 đang áp dụng không được miễn bởi quyết định này.

## Quyết định 2: Optional Intelligence

Các phần sau không còn là điều kiện bắt buộc để sản phẩm Core đầu tiên hoàn tất:

| Phạm vi | Thành phần tùy chọn |
|---|---|
| P4 | Forecasting, classical ML, TimesFM và mở rộng quant models |
| P5 | Portfolio optimizer/RL nâng cao; không bao gồm sizing/risk cơ bản của Core |
| P6 | Institutional financial/document research |
| P7 | Multi-agent research desk |
| P8 | Semantic decision graph, backend memory mới và context optimization; không bao gồm lineage và durable recovery của Core |

“Optional” không có nghĩa là tự động được phép triển khai, cũng không yêu cầu
xóa source đang tồn tại. Mỗi mở rộng cần use case, baseline, giả thuyết giá trị
tăng thêm, tiêu chí nghiệm thu đóng băng trước và kiểm chứng chi phí/rủi ro.
Thành phần chưa dùng không tạo nghĩa vụ có production memory backend hoặc
benchmark AI chỉ để hoàn thành Core. Thành phần đã dùng vẫn phải được qualified
và có provenance theo tác động thực tế của nó.

Quy tắc P3 không có alpha đạt chuẩn thì giữ mở rộng P4/P5/P7 ở HELD vẫn áp dụng.
Kết quả NONE_QUALIFIED/FAIL có thể hoàn tất công việc nghiên cứu và hồ sơ, nhưng
không làm P3 economic completion thành PASS.

## Quyết định 3: Paper/shadow sớm theo dependency

Roadmap không buộc nhánh paper của Core phải chờ toàn bộ P4–P8 nâng cao.
Đường phụ thuộc của sản phẩm đầu tiên là:

```text
Foundation + active engine/data/authority qualification
  → chiến dịch P3 có kết luận hợp lệ
  → nếu primary đạt economic gate: paper/shadow theo mandate hẹp
  → live eligibility cho phạm vi đã chứng minh
  → operator review và explicit activation riêng
```

Nếu không có primary đạt chuẩn, lưu kết luận nghiên cứu và giữ economic/live
progression ở HELD. Không tự thay family, sửa tiêu chí hoặc dùng lại holdout để
chọn chiến lược khác. Nghiên cứu epoch mới cần quyết định riêng theo hợp đồng.

Kiểm tra kỹ thuật bằng synthetic fixtures hoặc baseline có thể được lập kế
hoạch sớm theo quyền hiện có. Chúng không phải bằng chứng economic acceptance,
không tự mở official holdout và không cấp quyền paper qua broker.

Trước khi chạy nhánh paper/shadow có giá trị qualification, phải có:

1. Strategy/primary, source, data, environment, engine và approvals đúng binding.
2. Mandate rõ instrument, timeframe, sizing, risk limits và owner vận hành.
3. Tiêu chí costs, drawdown, execution, reconciliation, recovery, stale data,
   stop conditions và thời gian/độ phủ quan sát được duyệt trước kết quả.
4. Đường risk, ledger, accounting, recovery và actual-UID/readback cần thiết đã
   được kiểm chứng; bất định execution/reconciliation phải fail closed.
5. Quyền cụ thể cho dịch vụ, dữ liệu, network/broker và runtime nếu đường chạy
   cần chúng. Việc duyệt roadmap không cấp các quyền này.

Nhánh paper hẹp không được tự gán P9 đầy đủ PASS. P9 completion và P10 eligibility
phải ghi rõ scope cùng tiêu chí áp dụng trong kế hoạch được duyệt tiếp theo.
P10/P11 không phụ thuộc vào Optional Intelligence chưa triển khai chỉ vì nó
xuất hiện trong DoD dài hạn, nhưng mọi điều kiện an toàn và kinh tế áp dụng cho
mandate thực tế vẫn bắt buộc.

## Bất biến và giới hạn không đổi

- Giữ I01–I15 của North Star V3.1 và các canonical owner hiện tại.
- LLM/model/agent không sở hữu risk, execution, accounting hoặc live authority.
- Không đổi cửa sổ dữ liệu, family, costs, threshold, holdout exposure rules,
  replica requirements hoặc acceptance criteria của chiến dịch P3 hiện tại.
- Không biến TEST PASS, probe PASS hoặc paper PASS thành economic/live authority.
- Hai live approvals vẫn false; P11 vẫn cần explicit operator authorization.
- Không miễn R05 qualification, R06 rehearsal, R07 review/freeze hoặc R08
  exposure reconciliation của kế hoạch P3 đang thực hiện. Unknown exposure
  tiếp tục chặn disclosure theo hợp đồng hiện tại.
- Không đổi protected store, service, database, scheduler, CI hoặc security
  policy; không cấp quyền push/merge/deploy qua ADR này.

## Áp dụng và bằng chứng hoàn thành

Các kế hoạch mới phải phân loại đầu ra là Core hay Optional và giải thích
dependency nào thực sự chặn milestone đang làm. DoD được tách thành:

- **Research outcome:** kết luận hợp lệ, replay được, có thể là NONE_QUALIFIED.
- **Core paper-qualified:** đủ economic gate và qualification của paper mandate.
- **Optional module-qualified:** chứng minh giá trị và an toàn của module cụ thể.
- **Live eligible / live activated:** hai quyết định độc lập như V3.1.

R05 hiện tại tiếp tục trong phạm vi đã duyệt. Việc điều chỉnh validator hoặc
tiêu chí phase completion để biểu diễn roadmap mới cần kế hoạch thay đổi cụ thể,
review và kiểm chứng tương ứng; không sửa cờ trạng thái thủ công.

ADR được ghi ở canonical source checkout. Không tự chèn nó vào candidate đang
qualification: khi tích hợp thay đổi tài liệu vào một candidate, phải đánh giá
source-closure drift và tạo lại bằng chứng mà binding yêu cầu. Không đổi nhãn
receipt cũ sang commit mới.

Đây là bản ghi quyết định đã duyệt, không phải xác nhận R05/P3/P9 đã hoàn tất.
