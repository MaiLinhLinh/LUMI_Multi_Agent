# Education domain — implementation checkpoints

Phạm vi đợt này: chỉ thêm mới `domains/education/` và đăng ký domain sau khi các checkpoint domain đã sẵn sàng. Không sửa class, pipeline, renderer, compiler hoặc frontend dùng chung nếu chưa được phê duyệt riêng.

> Historical checkpoint record. Runtime hiện tại không còn dùng Planner,
> Compiler, scene contract hay `trigger_scene`; Education tạo Fact Pack,
> visual stage map và dùng `present_visual(anchor_id, effect_id)` của Gemini Live.

## CP-EDU-01 — Template Object Group và metadata

- [x] Tạo ba SVG asset thử nghiệm: flower, ball, rocket.
- [x] Tạo template Jinja `object_group_math` cho phép cộng/trừ trong phạm vi 10.
- [x] Khai báo semantic target và effect capability trong `metadata.json`.
- [x] Bảo đảm mỗi semantic target chính có một phần tử DOM xác định.
- [x] Render kiểm tra trong môi trường `LumiMultiAgent`.

Checkpoint tiếp theo: **CP-EDU-02 — model dữ liệu và tool `create_arithmetic_exercise`**, chỉ nằm trong `domains/education/`. Tool nhận đề nghị bài toán từ Gemini, còn code kiểm tra tính hợp lệ và tự tính kết quả chính xác. Phạm vi số là chính sách của bài học/template, không phải giới hạn chung của tool.

## CP-EDU-02 — Math exercise data & tool

- [x] Tạo model bài toán được validate; không khoá phạm vi số vào tool chung.
- [x] Tạo tool `create_arithmetic_exercise`.
- [x] Code chọn asset ngẫu nhiên trong danh sách được phép.
- [x] Viết test cộng/trừ hợp lệ và từ chối dữ liệu sai.

## CP-EDU-03 — EducationLiveDomain

- [x] Tạo `domain.py`, `prompt.py`, `view_model.py`, `adapter.py`.
- [x] Đăng ký Education vào `bootstrap.py` sau khi domain hoạt động độc lập.

## CP-EDU-04 — Grounded facts, Planner và Compiler

- [x] Tạo fact cho nhóm A, toán tử, nhóm B, biểu thức và kết quả.
- [x] Kiểm tra Planner/Compiler chỉ dùng target/effect của template.

## CP-EDU-05 — Gemini Live end-to-end

- [x] Tool `create_arithmetic_exercise` đi qua registry, shared Pipeline và scene state trong integration test.
- [x] Render panel, nhận presentation contract trong integration test.
- [x] Frontend hiện có nhận panel/scene theo contract tổng quát; cần kiểm thử Gemini Live thật trên trình duyệt trước khi coi là nghiệm thu trải nghiệm.

## CP-EDU-06 — Reveal result objects

- [x] Render sẵn số kết quả và nhóm asset kết quả trong template, nhưng ẩn ban đầu.
- [x] Thêm semantic targets `math.result.items` và `math.result.number` cùng capability tương ứng.
- [x] Thêm `reveal_items`: hiện từng asset lần lượt trong vùng kết quả.
- [x] Thêm grounded fact `result_items`; fact `answer` nay focus vào số kết quả ở panel.
- [x] Chạy 14 test Education/presentation/registry và `compileall` thành công.

Checkpoint tiếp theo: kiểm thử thủ công trên trình duyệt với một phép cộng, xác nhận scene kết quả gọi lần lượt `reveal_items` và `reveal`. Nếu Planner chưa chọn đủ hai fact kết quả, chỉ tinh chỉnh prompt/fact selection trong Education sau khi xem log thực tế.
