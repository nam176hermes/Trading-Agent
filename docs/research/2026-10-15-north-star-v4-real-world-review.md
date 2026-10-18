# North Star V4 — Đánh giá mở rộng từ mã nguồn, người dùng và thực nghiệm

**Ngày kiểm tra: 2026-10-15.** Bổ sung và điều chỉnh [báo cáo đầu](2026-10-15-north-star-v4-oss-research.md), phục vụ [North Star V4](../NORTH-STAR-V4.md). Phạm vi là hệ thống crypto cá nhân; ưu tiên khả năng hoàn thành, kiểm chứng hiệu quả và vận hành được.

## 1. Quyết định sau khi mở rộng tra cứu

Giữ định hướng **quant core + AI research**, nhưng bản đầu phải là một bàn nghiên cứu và paper BTC spot nhỏ, có kết quả đối chiếu được. Chưa có bằng chứng trong tập nguồn đã đọc rằng nhập nguyên một đội agent nổi tiếng sẽ tạo lợi nhuận bền vững. Cũng chưa có bằng chứng để kết luận AI không có ích: có công trình và thử nghiệm cho thấy giá trị ở một số cấu hình, nhưng giá trị đó phụ thuộc dữ liệu, chi phí, cách giao quyền và phương pháp đánh giá.

Khuyến nghị cụ thể:

1. **Giữ engine của candidate hiện tại để đóng phạm vi đã duyệt; đánh giá việc duy trì engine dài hạn riêng.** Stable không đồng nghĩa không có lỗi hoặc sẽ được backport mọi sửa chữa.
2. **Ưu tiên baseline và sổ sách trước đội agent lớn.** Freqtrade là đối chứng thực dụng cho một workflow crypto nhỏ, không phải execution owner thứ hai trong ứng dụng.
3. **Tăng ưu tiên đo chi phí và giá trị bổ sung.** TradeLens đáng học ở tầng attribution và chi phí; chưa cần nhập toàn bộ toolkit hoặc phần LLM chẩn đoán.
4. **TradingAgents, Condor và RD-Agent là nguồn ý tưởng/candidate có điều kiện.** Không đưa thẳng code upstream vào đường giao dịch chỉ vì kiến trúc gần mục tiêu.
5. **Tự cải tiến bắt đầu bằng công cụ nghiên cứu có đầu ra kiểm tra được.** Chưa giao quyền sửa chiến lược đang chạy, risk, evaluator hay dữ liệu đánh giá.

Nếu bắt đầu từ con số không và chỉ muốn chạy một chiến lược crypto cá nhân, Freqtrade đáng được thử trước một nền tảng tùy biến lớn. Với nguồn Nautilus, ledger, jobs và dashboard đã có ở Trading-Agent, chưa đủ căn cứ để yêu cầu viết lại. Quyết định đổi engine phải dựa trên khoảng trống thực tế và chi phí chuyển đổi còn lại, không dựa trên số sao hoặc công sức đã bỏ ra. Đây là suy luận thiết kế, không phải kết quả benchmark hai hệ thống.

## 2. Cách tra cứu và phân loại bằng chứng

Đã dùng skill **agent-reach**: `doctor --json`, GitHub qua `gh CLI`, Reddit qua `rdt-cli`, Jina Reader cho bài web. Exa không sẵn có (`mcporter` chưa cài), X chưa xác thực; dùng web search bổ sung, không cài công cụ hoặc thay đăng nhập. `agent-reach check-update` báo v1.5.0 đang mới nhất.

Đợt bổ sung gồm: metadata mới của **16 repo truy cập được**, **10 issue đọc nội dung và bình luận**, **5 thread Reddit đọc được** sau sáu nhóm truy vấn cộng đồng, cùng các bài gốc và tài liệu liên quan. Một repo và một thread khác không đọc được nên không dùng như bằng chứng đã kiểm tra. Truy vấn Reddit cũng trả nhiều nội dung không liên quan; đã loại thay vì cộng vào “đánh giá người dùng”. Manifest giữ SHA và trạng thái nguồn tại [đây](2026-10-15-north-star-v4-real-world-manifest.json).

Không chấm “độ thành công” bằng điểm cộng sao, số issue hay số nguồn. Dùng các mức bằng chứng sau:

| Loại | Cho phép kết luận | Không cho phép kết luận |
| --- | --- | --- |
| Source đúng phiên bản + phản hồi maintainer | Một chi tiết triển khai hoặc phạm vi lỗi có căn cứ mạnh hơn | Ứng dụng của mình chắc chắn bị ảnh hưởng, hoặc mọi runtime đã sửa |
| Bài có protocol, data window, chi phí và ablation | Tác giả quan sát kết quả trong phạm vi đó | Lợi nhuận sẽ tái diễn ở BTC/venue/capital khác |
| Issue có reproduction nhưng chưa xác nhận | Có giả thuyết lỗi đáng kiểm tra | Bug đã được chứng minh độc lập |
| Người dùng kể trải nghiệm, chưa có artifacts đầy đủ | Gợi ý vấn đề thực tế và câu hỏi kiểm thử | Bằng chứng lợi nhuận, tỷ lệ người dùng thành công |
| Review tổng hợp, README, leaderboard | Phát hiện nguồn và hiểu phạm vi sản phẩm | Kiểm toán runtime hoặc xác nhận alpha |

Đọc cả phản hồi phản bác, trường hợp đã sửa và trường hợp không tái lập. Không coi `OPEN` là chắc chắn chưa sửa, `CLOSED` là chắc chắn đã sửa. Các nguồn có cùng tác giả hoặc cùng dữ liệu không tạo thành nhiều phép kiểm chứng độc lập.

## 3. Phát hiện thực tế làm thay đổi khuyến nghị

| Nguồn đọc trực tiếp | Phát hiện, trạng thái tại ngày kiểm tra | Hệ quả cho V4 |
| --- | --- | --- |
| [Nautilus #5113](https://github.com/nautechsystems/nautilus_trader/issues/5113) | Lỗi `OrderInitialized.reconciliation` của v1.231; maintainer xác nhận v2 đúng và không backport, đóng “not planned”. Đã đọc tag v1.231.0: getter thực sự trả `False` | Giữ pin cho candidate, nhưng thêm đánh giá support và lỗi liên quan trước lựa chọn runtime dài hạn. Chưa xác minh đường gọi của ứng dụng có dùng field này |
| [Nautilus #5280](https://github.com/nautechsystems/nautilus_trader/issues/5280) | Reporter mô tả v2 prerelease/develop coi position reports rỗng là flat, có thể sinh reconciliation order cho sandbox/spot; issue mở, chưa có maintainer phản hồi trong bản đọc | Không lấy nhãn “sandbox” hoặc “v2” làm bảo đảm. Đây chưa phải lỗi đã xác minh trên v1 hoặc Trading-Agent |
| [TradingAgents #1251](https://github.com/TauricResearch/TradingAgents/issues/1251) | Memory có thể đưa kết quả tương lai vào ngày quá khứ; maintainer nói đã sửa tại `8db41f6` | Giữ thời điểm outcome được biết trong memory. Không liệt kê lỗi lịch sử này như lỗi chưa sửa ở 0.6.0 |
| [TradingAgents #1196](https://github.com/TauricResearch/TradingAgents/issues/1196) | Người dùng làm ablation hẹp thấy debate đẩy trường hợp mơ hồ về hướng mua; maintainer xác nhận prompt tạo áp lực tránh Hold và sửa tại `a4acd8a`, không tuyên bố loại mọi bias | Nhiều agent không mặc nhiên tốt hơn. Cho phép Hold/abstain; so cấu hình với cùng thông tin và giữ giới hạn của mẫu |
| [TradingAgents #1518](https://github.com/TauricResearch/TradingAgents/issues/1518) | Báo cáo ngày 13/10 về news sau giờ đóng cửa được dùng nhưng return tính từ close cùng ngày; chưa có phản hồi xác nhận | Timestamp quyết định, giới hạn dữ liệu và giá bắt đầu chấm phải thống nhất; không chỉ lọc theo ngày. Báo cáo nói cổ phiếu Mỹ, chưa chứng minh lỗi giống hệt với BTC |
| [Condor #151](https://github.com/hummingbot/condor/issues/151) | Báo cáo đường `manage_bots` từng vượt dry-run. Maintainer nhắc sửa file chưa tác động process đang chạy nếu chưa restart. Source hiện tại đã liệt kê các bot mutation và có kiểm tra risk | Không nói lỗi lịch sử vẫn tồn tại chỉ vì issue còn mở. Học yêu cầu kiểm chứng policy có hiệu lực trong đúng process; chưa xác nhận mọi đường tool đều an toàn |
| [RD-Agent #1451](https://github.com/microsoft/RD-Agent/issues/1451) | Source HEAD vẫn có khoảng trắng cuối key annualized return; biến tên `sharpe` tính return/drawdown. Đã đối chiếu `bandit.py`, không chạy pipeline | Metric của evaluator phải được kiểm chứng trước khi cho agent tối ưu theo nó; không dùng tên metric làm bằng chứng đúng công thức |
| [RD-Agent #1372](https://github.com/microsoft/RD-Agent/issues/1372) | Báo cáo timeout, nhưng người đăng sau đó nói cài lại đã hết và maintainer ghi nhận | Không dùng issue mở này để kết luận bản mới chắc chắn bị lỗi timeout |
| [Freqtrade #13661](https://github.com/freqtrade/freqtrade/issues/13661) | Maintainer bác bỏ báo cáo là AI hallucination; đợt này không tái lập | **Loại khỏi căn cứ kết luận bug.** Một report chi tiết vẫn có thể sai |
| [Condor #284](https://github.com/hummingbot/condor/issues/284) | Câu hỏi ngày 15/10 về ai chấm sửa đổi chiến lược, kèm giới thiệu dự án của người hỏi; không có phản hồi | Chỉ là câu hỏi thiết kế, không phải bằng chứng Condor thiếu mọi kiểm tra hoặc bằng chứng hiệu quả của dự án được quảng bá |

Các đối chiếu source thêm: [Nautilus v1 getter](https://github.com/nautechsystems/nautilus_trader/blob/v1.231.0/nautilus_trader/model/events/order.pyx#L480), [RD-Agent metric extraction](https://github.com/microsoft/RD-Agent/blob/484776c211e4fbbeef03e0ec00d6bbee7362a4f4/rdagent/scenarios/qlib/proposal/bandit.py#L44), [Condor dangerous tool classification](https://github.com/hummingbot/condor/blob/45e60d6067bd311c154d7890c1ecc46f207b17e2/condor/runtime/danger.py#L79).

## 4. Người dùng nói gì, và tin được đến đâu?

- [Nautilus vs LEAN](https://www.reddit.com/r/algotrading/comments/1op0rel/nautilustrader_vs_quantconnect_lean/): có người kể trải nghiệm QuantConnect Python thuận tiện nhưng quản lý nhiều chiến lược khó hơn. Bình luận tổng quát được nhiều vote còn bị người khác nghi là AI viết. Thread không có hồ sơ live PnL để xếp hạng engine. Dùng để định hướng câu hỏi usability, không dùng làm bằng chứng lợi nhuận.
- [Freqtrade vs Hummingbot](https://www.reddit.com/r/algotrading/comments/1h7uyhg/freqtrade_vs_hummingbot_2024/): phản hồi lịch sử giúp phân biệt nhu cầu chiến lược candle và market making. Đây là thread 2024, không xác nhận bản 2026 hoạt động giống hệt.
- [Thử TradingAgents với local Qwen3 32B](https://www.reddit.com/r/financije/comments/1tbslo0/testirao_sam_tradingagents_ai_simulacija_hedge/): người đăng báo thời gian dài, đầu ra lạc đề và nghi thiếu price tools. Một cấu hình local không xác định đủ version; không suy ra mọi model hoặc bản 0.6.0 đều thất bại. Bài học thực dụng là xác nhận tool/data trước khi thêm tranh luận.
- [Momentum bot dựa trên Freqtrade](https://www.reddit.com/r/ai_trading/comments/1w5hz60/built_a_freqtradebased_momentumtrend_bot_and/): chính tác giả thừa nhận lợi nhuận tập trung vào một giao dịch ZEC và kết quả yếu khi loại nó. Repo dẫn chứng gặp vấn đề truy cập theo phản hồi tác giả; không xác nhận các con số. Gợi ý cần xem concentration và kết quả theo từng giai đoạn, không chỉ headline return.
- [Các agent/skill người dùng đang dùng](https://www.reddit.com/r/Trading/comments/1x04kxr/what_trading_agents_or_skills_are_you_actually_using/): có mô tả dùng AI cho nghiên cứu, đề xuất và paper; một số bình luận quảng bá sản phẩm riêng. Chưa có đủ bằng chứng để kết luận các setup đó sinh lợi hoặc giảm công sức.

Review [InvestingStack ngày 09/10](https://investingstack.ai/research/open-source-ai-trading-agents-audit) tự mô tả là đọc tài liệu/source tree của 20 dự án, **không chạy backtest** và không giữ historical commit hashes. Hữu ích để tìm ứng viên; không phải một thử nghiệm độc lập về hiệu suất. Đã đối chiếu một phần với repo chính chủ thay vì dùng bảng của họ làm kết quả kiểm toán.

Một bài Reddit về 571 chiến lược không đọc được qua backend trong lượt này. Repo `wiktorj137/btc-strategy-lab` từ bài khác không truy cập được qua GitHub API. Không dùng hai nguồn đó để kết luận “không chiến lược nào thắng thị trường”. Trang Alpha Arena cũng không tải được trong lần kiểm tra này; không dùng điểm leaderboard từ bài tổng hợp để chọn model.

## 5. Nghiên cứu bổ sung đáng áp dụng

| Công trình và phiên bản đọc | Kết quả có ích | Giới hạn khi đưa vào quyết định |
| --- | --- | --- |
| [TradeLens v3, 07/10/2026](https://arxiv.org/html/2607.10286v3) | Tách hệ thống có lãi và phần quyết định AI có bù được chi phí; gắn cost/profit với traces. Source có lớp tính toán và lớp LLM diễn giải riêng | Các thị trường/cửa sổ cụ thể; phần practitioner feedback chỉ 13 người và định tính. Không phải chứng minh giảm công sức hoặc model thắng BTC; chính tác giả hạn chế khái quát hóa |
| [Building crypto portfolios with agentic AI, 08/10/2026](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2026.1871825/full) | Có so sánh portfolio, ngoài mẫu và kiểm định | §6.3 không tìm thấy khác biệt có ý nghĩa thống kê của daily returns; §7 bỏ qua spread/phí; code theo yêu cầu. Không dùng Sharpe đẹp làm bằng chứng edge sau chi phí |
| [Execution Realism review v3, 07/10/2026](https://arxiv.org/abs/2606.08285v3) | Đánh giá PIT, split, costs, execution semantics và artifacts riêng | Bản mới giữ kết quả coding chi tiết ở trạng thái chờ author validation. Không lấy con số sơ bộ của bản cũ thành tỷ lệ kết luận cuối cùng |
| [AlphaForgeBench v2, 27/05/2026](https://arxiv.org/abs/2602.18481v2) | Tách AI tạo factor/strategy khỏi thực thi xác định trước; báo cáo biến thiên lớn giữa các lượt agent | Đợt này đọc abstract và phạm vi, chưa tái lập. Hỗ trợ giả thuyết thiết kế, không xác nhận alpha |
| [LATTICE, 29/04/2026](https://arxiv.org/abs/2604.26235) | Chấm utility hỗ trợ quyết định của crypto copilots, không chỉ PnL | Dùng LLM judge không dựa vào ground truth chuyên gia/dữ liệu ngoài cho việc chấm. Không dùng điểm này làm ground truth tài chính hoặc tự chấp nhận skill |
| [WebCryptoAgent, 08/01/2026](https://arxiv.org/html/2601.04687v1) | Tách nghiên cứu và risk nhanh; dùng nhiều nguồn và reflection | [Repo hiện tại](https://github.com/AIGeeksGroup/WebCryptoAgent/tree/7d479169501fdd21b6e0c529494a4c3070762544) nói rõ offline backtesting; phần Node.js chi tiết cần liên hệ. Không coi toàn hệ thống mô tả trong paper là source runnable công khai đầy đủ |

Các công trình SwingGuard, MintEval, Can AI Make Money in Crypto?, Propose Don't Judge và KTD-Fin từ [báo cáo đầu](2026-10-15-north-star-v4-oss-research.md) vẫn là bằng chứng bổ sung với giới hạn đã ghi; không tính việc trích lại chúng như phép tái lập mới.

Điểm mới quan trọng nhất từ TradeLens là **AI có thể tăng lợi nhuận gộp mà vẫn giảm giá trị ròng**. Đề xuất cho chiến dịch mới: so PnL sau phí giao dịch của baseline và cấu hình có AI trên cùng điều kiện, sau đó trừ phần chi phí AI/data/compute tăng thêm. Đây là phép đo so sánh, không tự là ước lượng nhân quả nếu hai cấu hình khác thêm yếu tố khác. Với paper, chi phí mô phỏng và hóa đơn thật phải ghi riêng.

Ví dụ minh họa ngân sách, không phải dự báo: $30/tháng chi phí thêm tương đương 3% vốn $1.000, hoặc 0,3% vốn $10.000, chỉ để bù khoản chi đó. Model local vẫn có compute, latency và công vận hành. Tăng vốn không chữa được chiến lược có edge âm.

## 6. Phiên bản và lựa chọn công nghệ sau vòng tra cứu này

| Dự án | Snapshot mới kiểm tra | Ưu tiên / quyết định |
| --- | --- | --- |
| NautilusTrader | develop `ce453eb9`; prerelease 2.0.0rc6; stable 1.231.0 đã đối chiếu ở báo cáo đầu | Giữ candidate; đánh giá support/adapter/known issues trước lựa chọn runtime dài hạn |
| Freqtrade | stable 2026.9; develop đã tiến tới `b3306894` | Ưu tiên tham chiếu workflow crypto và đối chứng offline; không tự nhập code develop |
| Hummingbot / Condor | 2.17.0 / `45e60d60` | Tham khảo điều phối và executor; chưa cần cho BTC D1 |
| TradingAgents | 0.6.0 / `1394a3f7` | Research worker có cấu trúc, kiểm tra data và abstention; không thay risk core |
| RD-Agent / Qlib | 1.0.0 / 0.9.8 | Lab cải tiến sau; kiểm tra evaluator trước khi tự động tìm kiếm |
| [TradeLens](https://github.com/ParadooxAI/TradeLens/tree/a2d299c7cfdd96adff387e6e5a8584ea089d920a) | `a2d299c7`, chưa có GitHub release | Tăng ưu tiên phương pháp đo cost/profit; reuse schema/phép tính có kiểm tra |
| [Jesse](https://github.com/jesse-ai/jesse/tree/5e642997f9623d67b63543962eab46caca30e613) | Source version 3.3.4, chưa có GitHub release | Ứng viên research UX/routes/diagnostics; chưa có lý do chuyển core |
| [LEAN](https://github.com/QuantConnect/Lean/tree/80e7843f645673bcbeaab963049f76f20f6785e1) | Commit `80e7843f`, 06/10/2026 | Nên xét lại khi mở đa tài sản. GitHub release cũ không có nghĩa project ngừng phát triển |
| [vectorbt](https://github.com/polakowo/vectorbt/releases/tag/v1.1.2) | 1.1.2, 09/10/2026 | Chỉ thêm khi profiling cho thấy việc sàng lọc thí nghiệm là bottleneck; nhiều cấu hình tăng selection bias |
| [HftBacktest](https://github.com/nkaz001/hftbacktest/tree/5f3ec40b2afb764e0fea112f941ed85523ef4e88) | Rust release 0.9.4; commit `5f3ec40b` | Tốt để nghiên cứu queue/latency khi thật sự làm market making; quá rộng cho MVP D1 |
| [SharpeBench](https://github.com/general-liquidity/sharpebench/tree/26d9a2a91cfcffe6053c25f9b563830e0fe2de56) | Release 0.28.0; HEAD `26d9a2a9` | Học kiểm soát chọn mẫu và lặp thí nghiệm. README nói bảng mẫu là teaching field, paper chưa có LLM trading agent tham gia; chưa nhập thêm bộ gate |
| [AI-Trader](https://github.com/HKUDS/AI-Trader/tree/d03ff6c056b32ced735adf7c19ed8175adb1c8df) | `d03ff6c0`, chưa có GitHub release | Source hiện hướng agent-native platform, paper/copy/signal. Không lấy review cũ về một benchmark làm mô tả đảm bảo của repo hiện tại |
| [NostalgiaForInfinity](https://github.com/iterativv/NostalgiaForInfinity/releases/tag/v18.0.116) | 18.0.116; HEAD `e244837d` | Kho ý tưởng strategy, không phải alpha có sẵn đã xác minh. Nhiều pair/position adjustments lệch mandate một BTC; chưa chọn làm chiến lược mặc định |
| WebCryptoAgent | `7d479169`, chưa có GitHub release | Học tách nhịp và evidence workflow; phạm vi public implementation hẹp hơn ý tưởng paper |

CCXT, FinRobot và Awesome-TradingAI giữ kết luận của báo cáo đầu; metadata của ba repo này không được truy vấn lại trong vòng bổ sung. Các commit mới chỉ xác nhận source identity, không chứng minh đã build, tương thích, chạy ổn định hoặc có hiệu quả kinh tế.

## 7. Bổ sung vào V4 ở mức thiết kế, không mở rộng P3

**Mục tiêu ngắn hạn:** một session có thể tái hiện và giải thích được. Với cùng mandate, phải trả lời được: data đã biết lúc nào; tại sao signal xuất hiện; tại sao risk cho/chặn; fill được tính ra sao; tiền/vị thế có khớp; AI có làm thay đổi điều gì và tốn bao nhiêu.

| Việc tiếp theo | Tiêu chí có ích | Cách giữ phạm vi nhỏ |
| --- | --- | --- |
| Chốt mandate và baseline | Có câu hỏi kinh tế cụ thể, benchmark và định nghĩa dữ liệu/fill/costs | BTC spot, một venue, timeframe hiện tại; không thêm altcoin để làm đẹp kết quả |
| Một vòng NS1 bằng fixture/replay | Đường happy path, bị chặn, no-trade và phục hồi ra sổ đối soát được | Mock AI; dùng jobs/ledger/API hiện có; không nhập thêm agent framework |
| Ghi AI shadow | So tính đúng nguồn, thông tin mới, abstention, thời gian và chi phí | Một vai trò nghiên cứu và một vai trò phản biện; lưu input/output, chưa tác động lệnh |
| So sánh giá trị bổ sung | So cấu hình không AI với có AI trên cùng budget/risk/data; lưu cả thất bại | Đánh giá mới theo quyền và protocol; không tiêu thụ lại P3 holdout |
| Skill đầu tiên | Cải thiện một công việc kiểm tra được: phát hiện data stale, tóm tắt thay đổi, chẩn đoán mismatch | Có oracle độc lập và review; không sửa risk hay strategy runtime |
| Engine dài hạn | Phiên bản/adapter đáp ứng use case và có cách xử lý known issues | Candidate riêng khi được phép; giữ nguyên kết quả/receipt candidate hiện tại |

Không biến mỗi issue hoặc paper thành một gate mới. Trước hết ánh xạ vào hợp đồng hiện có; chỉ thêm kiểm tra cho một failure mode thực sự chưa được bao phủ và nằm trong phạm vi task mới. Không hạ gate cũ để đi nhanh, cũng không thêm gate không liên quan để cảm thấy chắc chắn.

Đặt thời hạn và ngân sách cho từng thử nghiệm trước khi chạy. Đến hạn phải có một kết luận: giữ, bỏ, hoặc chưa đủ bằng chứng vì nguyên nhân cụ thể. Một giả thuyết bị bác bỏ đúng cách giúp tránh lãng phí; không phải lý do mở thêm một framework. Nếu đội AI chưa tạo thêm giá trị, core vẫn phải hữu ích như hệ thống quant/paper cá nhân.

## 8. Giới hạn và kiểm tra của đợt nghiên cứu

- Không tái lập PnL, không chạy upstream test suite hoặc paid LLM, không kết nối broker/exchange/account/order endpoint. Đọc issue và source không được gắn nhãn runtime PASS.
- Không có track record kiểm toán độc lập đủ để gán xác suất thành công cho dự án hoặc xếp model theo lợi nhuận dài hạn. Điều này mô tả tập bằng chứng đã đọc, không khẳng định trên thế giới không tồn tại track record như vậy.
- Reddit có self-promotion, nội dung bị xóa, thread cũ và khả năng văn bản do AI viết. Review được dùng để phát hiện câu hỏi, không làm mẫu đại diện người dùng.
- Bằng chứng tải về nằm ở `/tmp/trading-v4-reviews-20261015-vjetqyqt`; manifest chỉ lưu thông tin chọn lọc và hash, không đưa session metadata hay bản sao toàn bộ bình luận vào repo.
- Checkout canonical HEAD `8ee1c18a5b99bca78738b6d5ca572bd0afb7c0b3`, đã có thay đổi cục bộ trước lượt này. Không audit lại trạng thái P3, sửa candidate, receipt, runtime hoặc quyền.
- Kiểm tra tài liệu trong lượt này: `git diff --check` đạt; kiểm tra Python 3 xác nhận JSON hợp lệ, 22 liên kết local tồn tại, code fences cân bằng và SHA-256/kích thước của 61 artifacts khớp manifest. Đã đối chiếu phần sửa V4 với bản lưu đầu lượt. Không chạy product tests vì chỉ thay đổi tài liệu; các kiểm tra này không chứng minh source/runtime qualification.

Kết quả mong đợi của tài liệu là chọn được một thí nghiệm nhỏ có khả năng thay đổi quyết định phát triển. Báo cáo bổ sung không thay thế việc thực hiện và đo thí nghiệm đó.
