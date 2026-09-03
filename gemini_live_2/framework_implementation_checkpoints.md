# Checkpoint triển khai framework mở rộng domain

## Bổ sung kiến trúc sau CP8 — Native capability tool loop

**Trạng thái:** `completed`

- Plan Agent không còn giả lập tool bằng JSON `action="call_capability"`.
- Khi domain có capability, Gemma nhận native
  `FunctionDeclaration(name="call_capability")`, tự gọi bằng `capability_id` và
  `arguments`.
- Gateway kiểm tra capability được manifest cấp quyền rồi mới thực thi handler;
  backend trả `FunctionResponse` đúng call ID về Gemma. Agent có thể gọi tiếp
  capability khác trước khi trả JSON cuối `use_existing_plan` hoặc `create_plan`.
- Chuẩn bị convention `domains/<domain>/tools.py`: mỗi domain tự khai báo
  `CAPABILITIES`, gồm schema công khai và handler backend. Education hiện có
  `tools.py` với tuple rỗng vì chưa có capability.
- Gateway lazy-load `tools.py` khi domain được dùng; manifest vẫn là biên quyền
  duy nhất. Đã có unit test cho native tool loop và lazy loading.

## Phạm vi đã chốt

- Xây mới hoàn toàn trong `gemini_live_2`; không import hay gọi chéo mã runtime từ `gemini_live`.
- Giữ giao diện web app hiện có: panel trực quan bên trái, chat/input/mic/trạng thái bên phải, WebSocket, audio queue, interruption, debug bubble và animation overlay.
- Sao chép vào `gemini_live_2` cơ chế Gemini Live Present đang hoạt động tốt: `present_visual` → validate → marker gắn PCM → AudioContext → effect.
- Không dùng template HTML panel dựng sẵn theo domain. UI panel mới dựng bằng `PanelIR` + CSS Grid + widget renderer.
- `PanelIR` là nguồn chân lý duy nhất cho UI, ASCII map, anchor map và effect hợp lệ.
- Khi Gemini Live gọi `route_request`, Plan Agent nhận history đáng tin cậy do backend lấy bằng `session_id`; `route_request` chỉ nhận `domain_id`, `intent`.
- Plan Agent chỉ dùng prompt chung; capability riêng theo domain đến từ manifest/catalog.
- Plan Agent dùng Gemini API với model Gemma; cấu hình/key chỉ được đọc từ `.env` nội bộ của `gemini_live_2` qua settings, không hard-code và không đọc/ghi/in giá trị secret trong quá trình triển khai.
- `.env` của `gemini_live` và `gemini_live_2` đã tương đương; không sao chép hoặc sửa `.env` trong các checkpoint.
- Gemini Live giữ panel hiện tại bằng cách trả lời/present trực tiếp từ context panel; Plan Agent chỉ được gọi để chọn plan có sẵn hoặc tạo plan mới cho panel thay thế.
- Education là POC đầu tiên; mọi contract và engine phải trung lập để thêm domain sau này.

## Nguyên tắc thực hiện

- Hoàn thành từng checkpoint, chạy kiểm tra phù hợp, cập nhật file này, tóm tắt và dừng chờ xác nhận trước checkpoint tiếp theo.
- Không tự ý mở rộng phạm vi hoặc sửa `gemini_live`.
- Trước khi sao chép từng phần từ `gemini_live`, đọc kỹ class/module liên quan và chỉ sao chép phần cần thiết.
- Không đọc giá trị `.env` nếu chưa có quyền rõ ràng từ người dùng.

---

## CP0 — Audit mã nguồn và lập bản đồ tái sử dụng

**Trạng thái:** `completed`

Đọc kỹ theo luồng thực tế các module/class trong `gemini_live`:

- `web_app.py`, `web/index.html`, `web/app.js`, `web/app.css`;
- `live/gemini_session.py`, `persistent_transport.py`, `orchestrator.py`, `dispatcher.py`, `memory.py`, `session_protocol.py`, `visual_presentation.py`;
- `presentation/` liên quan render/anchor/capability hiện có;
- toàn bộ JS animation/effect;
- bootstrap, settings, registry và tool schema.

Kết quả cần có:

- sơ đồ dependency của phần sẽ copy giữ nguyên;
- danh sách phần loại bỏ vì gắn template HTML/grounded-fact cũ;
- điểm tích hợp tối thiểu để PanelIR mới thay panel cũ;
- baseline test/command chạy app của project cũ.

**Tiêu chí xong:** có bản đồ module rõ ràng trước khi tạo skeleton runtime.

**Kết quả:** xem [`cp0_audit_tai_su_dung.md`](cp0_audit_tai_su_dung.md). Baseline
project cũ có 38 passed, 9 failed (đều do metadata template Education cũ bị
thiếu), không sửa project cũ.

## CP1 — Tạo skeleton độc lập và giữ nguyên web app/Present

**Trạng thái:** `completed`

- Tạo package, entrypoint và cấu trúc `gemini_live_2`.
- Sao chép đúng các module Live/Present/frontend đã audit từ `gemini_live`.
- Đổi import/path để hoàn toàn nội bộ `gemini_live_2`.
- Tạo settings loader chỉ đọc tên biến cấu hình Plan Agent từ `.env` của chính project; không thay đổi file `.env`.
- Chưa đưa domain/template cũ vào.
- Xác nhận app chạy được tới trang web và WebSocket/Live session theo cấu hình hiện có.

**Tiêu chí xong:** `gemini_live_2` chạy độc lập, giao diện/âm thanh/animation shell hoạt động; chưa có panel nghiệp vụ mới.

**Kết quả:** Đã tạo package độc lập `gemini_live_2`, sao chép web shell,
AudioContext queue, animation controller/effects và Gemini Live persistent transport
vào chính project này. Entry point, settings và registry tạm đều chỉ import nội bộ
`gemini_live_2`; registry không chứa domain/tool cũ. Settings chỉ đọc `.env` của
project lúc runtime và không sửa/hiển thị secret. Đã kiểm tra `compileall`, `/`,
`/api/health` và static animation module đều trả thành công.

## CP2 — Contract lõi và trạng thái panel

**Trạng thái:** `completed`

Tạo schema/dataclass dùng chung:

- `RouteRequest(domain_id, intent)`;
- `PresentationPlan`;
- `DataBundle` và catalog data alias;
- `PanelIR`;
- `ActivePanelState(panel_ir, revision)`;
- `ActivePanelState` là contract lưu panel hiện tại; các contract lựa chọn plan cho panel mới sẽ được hoàn thiện ở CP8.

**Tiêu chí xong:** contract có validator/test đơn vị; chưa cần gọi Plan Agent thật.

**Kết quả:** Đã tạo package `panel` trung lập với các dataclass/validator cho
`RouteRequest`, `PresentationPlan`, `DataBundle` + `DataAlias`, `PanelIR`,
`ActivePanelState`. Các decision `keep_active_panel`/`create_panel` được tạo sớm
ở CP2 nay là contract cũ: `keep_active_panel` sẽ được bỏ khi bắt đầu CP8 vì panel
hiện tại do Gemini Live xử lý trực tiếp; đầu ra Plan Agent sẽ chuyển thành
`use_existing_plan` hoặc `create_plan` cho panel mới.
`ActivePanelState.replace()` tăng revision bất biến. Contract chưa biết widget,
asset, canvas, tool hay Plan Agent; các kiểm tra đó thuộc checkpoint sau. Đã thêm
6 unit test và chạy thành công trong môi trường `LumiMultiAgent`.

## CP3 — Asset Catalog và Domain Manifest

**Trạng thái:** `completed`

- Tạo loader/validator catalog asset đa định dạng: SVG, PNG, JPG/JPEG, WebP.
- Tạo `DomainManifest` trung lập: asset catalog, widget được phép, template catalog, tool capability.
- Tạo POC manifest/catalog Education với asset chó/mèo.

**Tiêu chí xong:** engine nạp tài nguyên theo `domain_id`, không có `if domain == education` trong core.

**Kết quả:** Đã tạo `catalogs/` dùng chung: `AssetCatalog` kiểm tra file,
định dạng/MIME (SVG, PNG, JPG/JPEG, WebP), ID duy nhất và path không thoát
khỏi domain; `DomainRegistry` nạp manifest hoàn toàn theo `domain_id`, không
có nhánh Education trong core. POC Education có manifest khai báo ba widget
được phép và asset catalog chó/mèo; hai ảnh được đặt trong chính
`gemini_live_2/domains/education/assets/`. Catalog đưa cho Plan Agent chỉ có
ID, kind, caption, tags — không lộ đường dẫn filesystem. Đã chạy 9 unit test
thành công trong môi trường `LumiMultiAgent`.

## CP4 — Widget Registry và design system

**Trạng thái:** `completed`

- Tạo widget registry chung gồm tối thiểu `text`, `image`, `object_group`.
- Mỗi widget có renderer DOM, CSS dùng design tokens chung, props schema, anchor policy và effect hợp lệ.
- Compiler/widget sinh anchor; Plan Agent không sinh HTML/CSS/DOM target/anchor.

**Tiêu chí xong:** widget render độc lập trong CSS Grid, anchor policy có test.

**Kết quả:** Đã tạo `widgets/` trung lập với registry cho `text`, `image` và
`object_group`. Mỗi widget xác thực props riêng, khai báo anchor policy/effect
hợp lệ và có renderer DOM + CSS design tokens độc lập trong `web/widgets/`.
Text không có anchor; ảnh có một anchor; object group có anchor cho nhóm và
từng phần tử. Anchor ID/DOM target cuối cùng vẫn chưa được sinh — việc đó thuộc
Compiler ở CP5. Đã thêm 4 unit test cho registry/props/anchor policy.

## CP5 — Layout validator và IR Compiler / Materializer

**Trạng thái:** `completed`

- Chốt canvas CSS Grid `16 × 10`.
- Validate block/widget/asset/props, bounds và overlap.
- Resolve data alias ngắn (`$temp`, `$days`, `$left`) từ `DataBundle`.
- Materialize thành `PanelIR`: dữ liệu thật, DOM target nội bộ, anchor map/effect map.
- Compiler không gọi tool, không chọn layout/asset/nội dung.

**Tiêu chí xong:** plan hợp lệ tạo PanelIR; plan sai bị từ chối có lỗi cấu trúc.

**Kết quả:** Đã tạo `PanelCompiler` trung lập với canvas 16×10. Compiler kiểm
tra domain/widget/asset/props, bounds và overlap; chỉ resolve alias `$...` đã
được `DataBundle` công bố; không tự chọn bố cục, nội dung hay asset. Nó
materialize props thành dữ liệu thật và sinh `PanelIR` cùng `anchor_id` và effect
map từ anchor policy của widget. Đã thêm 3 test cho compile
hợp lệ, alias/anchor map, và các nhánh lỗi layout/widget/asset/alias.

## CP6 — Panel Renderer và ASCII Renderer cùng đọc PanelIR

**Trạng thái:** `completed`

- Thay vùng panel trực quan của web app bằng Panel Renderer CSS Grid.
- Tạo ASCII Renderer từ chính `PanelIR`.
- Bảo đảm widget, dữ liệu materialized, thứ tự bố cục và anchor giữa UI/ASCII map nhất quán.

**Tiêu chí xong:** cùng một PanelIR render được UI và stage map, có test fidelity block/anchor.

**Kết quả:** Đã tạo renderer CSS Grid tổng quát ở browser cho payload `panel_ir`.
Nó chỉ đặt widget theo block/grid của PanelIR, gắn trực tiếp `anchor_id` compiler đã sinh và
dùng asset URL đã whitelist. `web_app` phục vụ asset theo `domain_id + asset_id`
từ catalog, không lộ filesystem path. ASCII Renderer đọc đúng PanelIR đó, biểu
diễn canvas 16×10, nội dung/tọa độ từng block và anchor tương ứng. Đã thêm test
fidelity để kiểm tra UI payload và ASCII map có cùng block, vị trí, anchor.

## CP7 — Domain Gateway và tool boundary

**Trạng thái:** `completed`

- Đăng ký tool/capability theo `DomainManifest`.
- Gateway chỉ mở tool của `domain_id` đã route.
- Chuẩn hóa tool result thành `DataBundle + data alias catalog`.
- POC Education cho phép không gọi tool khi chỉ cần asset chó/mèo.

**Tiêu chí xong:** Plan Agent không thể gọi tool domain khác hoặc tự truy cập DB/API.

**Kết quả:** Đã tạo `DomainGateway` dùng chung. Capability phải được đăng ký
ở backend và được manifest của đúng domain cấp quyền. Gateway chỉ trả catalog
capability đã được cấp quyền, từ chối capability domain khác trước khi handler
chạy, và buộc handler trả `DataBundle` có cùng `domain_id`. Luồng Education
chó/mèo hiện tại có capability rỗng và nhận `DataBundle(data={})`, nên không
cần truy cập DB/API. Đã thêm test cho luồng không tool, capability được cấp
quyền, capability cross-domain và manifest cấp quyền nhưng thiếu handler.

## CP8 — Plan Agent và tool loop

**Trạng thái:** `completed`

- Tạo service Plan Agent dùng system prompt chung, native function calling và JSON quyết định cuối.
- Khởi tạo Gemini API client/model từ settings của `gemini_live_2`, không hard-code key/model.
- Input: intent, history backend lấy theo session, manifest, catalog, grid, DataBundle hiện có.
- Tool loop: Plan Agent gọi tool domain → Gateway trả DataBundle/alias → Plan Agent tiếp tục → trả quyết định cuối.
- Trước khi xây service, bỏ contract cũ `keep_active_panel`/`create_panel` ở CP2.
- Quyết định cuối cho panel mới: `use_existing_plan(template_id)` hoặc
  `create_plan({"blocks":[...]})`; backend gắn `domain_id` đã kiểm chứng để dựng `PresentationPlan`.

**Tiêu chí xong:** test mock được cả panel không cần tool và panel cần dữ liệu tool.

**Kết quả:** Đã bỏ hoàn toàn contract cũ `keep_active_panel`/
`create_panel`/`PanelAction` từ `panel.contracts` và test của nó. Đã tạo package
`plan_agent/` trung lập, nhận `domain_id`, `intent`, history tin cậy do backend
cung cấp và `DataBundle` ban đầu. Service tải manifest/asset catalog và catalog
capability qua `DomainGateway`; sau đó gọi model Gemini API được cấu hình qua
`Settings`. Khi có capability, Gemma nhận native `FunctionDeclaration`
`call_capability`; mỗi `FunctionResponse` được trả đúng call ID. Agent tự gọi
capability khi cần rồi kết thúc bằng JSON `use_existing_plan(template_id)` hay
`create_plan({"blocks":[...]})`. Backend gắn `domain_id` từ route đã kiểm chứng để dựng
`PresentationPlan`. Mỗi tool result đi qua Gateway, được kiểm tra domain/quyền và hợp nhất vào
`DataBundle` mà không ghi đè data alias. CP9 sẽ bổ sung template catalog để xác
minh `template_id` được chọn. Đã thêm 5 test mock: asset-only, tool loop, plan
có sẵn, tool không được cấp quyền và history không hợp lệ. Toàn bộ 25 unit tests
pass và `compileall` pass trong môi trường `LumiMultiAgent`; không đọc/in/sửa
`.env`.

## CP9 — Template Catalog (plan lưu sẵn)

**Trạng thái:** `completed`

- Catalog chỉ chứa ID, mô tả và `plan_path`.
- Template plan lưu block grid/widget/slot alias, không phải HTML.
- Plan Agent tự chọn `use_existing`; compiler materialize plan từ DataBundle.

**Tiêu chí xong:** plan có sẵn và plan tạo mới đều đi qua cùng Compiler → PanelIR.

**Kết quả:** Đã tạo `TemplateCatalog` dùng chung. Mỗi catalog chỉ công bố cho
Plan Agent `id` và mô tả tự nhiên; đường dẫn plan luôn ở server-side, bị kiểm
tra phải nằm trong domain. Education POC có plan lưu sẵn
`two_subject_comparison`, gồm các block Grid/widget và không chứa HTML/CSS.
Plan Agent nay nhận catalog thật, có thể tự chọn `use_existing_plan`, còn
backend chỉ xác minh ID thuộc catalog — không xếp hạng hay tự chọn template.
Khi nạp, plan được gắn `template_id` theo entry catalog và đi qua đúng
`PanelCompiler` đang dùng cho `create_plan`, nên cùng tạo `PanelIR`, anchor và
effect map. Đã thêm test catalog an toàn, plan lưu sẵn qua compiler và ID lạ;
đồng thời bổ sung test Plan Agent từ chối ID không có trong catalog. Toàn bộ
29 unit tests pass và `compileall` pass trong môi trường `LumiMultiAgent`.

## CP10 — Gemini Live routing và tích hợp panel mới

**Trạng thái:** `completed`

- Thêm tool `route_request(domain_id, intent)` vào Gemini Live.
- Với câu hỏi tiếp nối về panel hiện tại, Gemini Live dùng stage map/present_visual trực tiếp; không gọi `route_request` hay Plan Agent.
- Khi Gemini Live gọi `route_request`, backend lấy history tin cậy bằng `session_id` rồi gọi Plan Agent.
- Nếu Plan Agent chọn plan có sẵn hoặc tạo plan mới hợp lệ: compile/render PanelIR mới, tăng `revision`, gửi presentation context cho Gemini.

**Tiêu chí xong:** voice → route → plan → panel mới hoạt động end-to-end.

**Kết quả:** Đã đăng ký duy nhất tool Live `route_request(domain_id, intent)`;
`domain_id` bị giới hạn theo các manifest đã đăng ký, còn Gemini Live không được
gọi capability domain trực tiếp. `LiveSessionOrchestrator` nay nhận route request,
lấy history tin cậy theo `session_id`, gọi Plan Agent và materialize cả hai nhánh
`use_existing_plan`/`create_plan` qua cùng `PanelCompiler`. `PlanAgent.plan()` trả về
`PlanAgentResult`, gồm cả decision và `DataBundle` cuối cùng cần cho alias khi compiler
materialize; không còn API `plan_with_bundle()` riêng. PanelIR mới được lưu
theo session, revision tăng khi thay panel, payload CSS Grid được gửi browser và
function response gửi lại Gemini chứa `presentation_instruction`, VISUAL STAGE MAP
và effect catalog. Các lượt chỉ nói về panel hiện có không có đường code tự gọi
Plan Agent; Gemini phải tự không gọi `route_request` theo instruction/tool contract.
Đã thêm 4 test CP10 cho tool registry, route→plan→compile→panel, revision và domain
lạ; toàn bộ 33 unit tests và `compileall` pass trong môi trường `LumiMultiAgent`.
Chưa thực hiện cuộc gọi Plan Agent/Gemini Live thật, nên việc đó được để cho POC thủ
công sau khi CP11 nối `present_visual` với PanelIR.

## CP11 — Nối PanelIR vào luồng Present hiện có

**Trạng thái:** `completed`

- `present_visual` validate bằng anchor/effect map từ ActivePanelState PanelIR.
- Giữ nguyên marker → PCM → AudioContext → effect.
- Cập nhật stage map/presentation context sau khi PanelIR mới render.
- Giữ context panel mới trong Gemini Live để các lượt tiếp theo có thể tương tác trực tiếp mà không gọi Plan Agent.
- `revision` chặn marker/cue cũ chạy vào panel mới.
- Prompt trình bày được khai báo theo manifest của domain, không hard-code trong core.

**Tiêu chí xong:** Gemini gọi anchor trong PanelIR, effect đúng vùng và đồng bộ audio như project cũ.

**Kết quả:** `present_visual` nay xác thực trực tiếp trên `ActivePanelState.panel_ir.anchor_map`:
anchor phải tồn tại trong PanelIR hiện hành, effect phải thuộc `allowed_effect_ids`, rồi backend
trả target compiler đã tạo. Lớp `ActivePresentationState` cũ đã bị gỡ để không còn hai nguồn
anchor map. Cue giữ nguyên cơ chế server marker → PCM → AudioContext → effect, đồng thời mang
`panel_id` và `panel_revision`. Payload PanelIR gửi browser cũng mang revision; browser chỉ arm
cue khi revision của cue trùng panel hiện đang render, nên marker/cue trễ từ panel bị thay thế bị
bỏ. Render panel mới vẫn clear animation đang hoạt động như trước. Đã thêm test cho resolve
anchor PanelIR, revision browser payload và từ chối anchor lạ. Toàn bộ 34 unit tests,
`compileall` và import `web_app` pass trong môi trường `LumiMultiAgent`.

**Bổ sung sau CP11:** `DomainManifest` nay có `presentation_prompt_path` và
`presentation_prompt_constant`; `DomainRegistry` generic nạp prompt thành
`DomainResources.presentation_instruction`. Education đã khai báo
`prompt.py:EDUCATION_PRESENTATION_INSTRUCTION`. Sau `route_request`, function response
trả prompt domain + ASCII map + effects; khi Live mở/kết nối lại, system instruction
khôi phục history gần + prompt domain + ASCII map + effects của ActivePanel hiện tại.
Đã chạy 36 unit tests, `compileall` và import `web_app` thành công.

**Bổ sung sau CP11 — ASCII và anchor cho Live:** Compiler nay cấp anchor ngắn
theo thứ tự trực quan `a`, `b`, `c`… cùng effect policy kỹ thuật của widget.
ASCII renderer không còn xuất ma trận
`A/B/C` hay ID kỹ thuật; nó dựng khung vùng trực quan từ chính PanelIR, đặt
anchor ngay trong vùng và thêm `ANCHOR LEGEND`. UI browser không đổi. Đã cập
nhật test Compiler/renderer/Live và chạy lại 36 unit tests cùng `compileall`.

## CP12 — POC Education end-to-end

**Trạng thái:** `pending`

Kiểm thử ba yêu cầu:

1. “Cho bé xem con chó.”
2. “Dạy bé phân biệt chó và mèo.”
3. “Cho bé làm phép cộng bằng hình chó.”

**Tiêu chí xong:** UI/ASCII/anchor đúng; lời trình bày gọi `present_visual` đúng; hỏi tiếp về panel không tạo panel mới khi không cần.

## CP13 — Bộ test và hardening

**Trạng thái:** `pending`

- Test contract, catalog, widget, compiler, data alias, UI–ASCII fidelity, active revision và present validation.
- Test tool boundary, native function-call loop và JSON quyết định cuối của Plan Agent với mock.
- Rà import để bảo đảm `gemini_live_2` không phụ thuộc runtime vào `gemini_live`.

**Tiêu chí xong:** bộ test pass; đường triển khai domain mới được ghi rõ.

## CP14 — Tài liệu thêm domain mới

**Trạng thái:** `pending`

Viết hướng dẫn thêm một domain mới chỉ bằng:

- manifest;
- asset catalog;
- widget riêng khi cần;
- tool capability;
- template catalog/plan lưu sẵn;
- prompt Gemini Live để trình bày.

**Tiêu chí xong:** có checklist mở rộng ngang, không yêu cầu sửa Live transport, compiler, renderer, ASCII renderer hay animation pipeline.

## Chặng bổ sung — Widget Index và `describe_widgets`

**Trạng thái:** `in_progress` — theo dõi chi tiết tại
[`tracking_widget_index_va_describe_widgets.md`](tracking_widget_index_va_describe_widgets.md).

- Plan Agent ban đầu chỉ nhận Widget Index ngắn (`widget_id`, `purpose`).
- Template Catalog vẫn được gửi trực tiếp theo một tầng (`id`, `purpose`, `supports`,
  `domains`), không thêm `describe_templates`.
- Khi cần tự tạo plan, Plan Agent gọi native tool chung `describe_widgets(widget_ids)`;
  tool lọc widget theo `allowed_widget_ids` của domain và chỉ trả contract props chi
  tiết của widget được yêu cầu.
- Output `create_plan` cuối cùng chỉ chứa `plan.blocks` gồm `widget_id`, `grid`, `props`;
  `domain_id` lấy từ `route_request` đã kiểm chứng, không do Plan Agent sinh. Compiler sinh
  block ID tuần tự và anchor.
