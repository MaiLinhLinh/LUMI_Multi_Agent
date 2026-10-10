# Kế hoạch kiểm thử End-to-End khả năng mở rộng Async Agent Task

## 1. Mục tiêu

Chứng minh `final_framework` thực sự cho phép developer:

1. tự xây một Async Agent Service độc lập;
2. khai báo service bằng một manifest trong `extensions/tasks`;
3. để framework tự discover task, công bố task vào Task Catalog, gọi HTTP,
   theo dõi remote job, validate output, tạo Artifact và ghép kết quả vào
   Surface;
4. không sửa orchestration core, prompt, Surface Plan schema hoặc Task Manager
   Runtime khi thêm task mới.

Task kiểm thử đầu tiên chỉ trả text xác nhận. Không dùng model hoặc video trong
bài test đầu tiên để lỗi provider không bị trộn với lỗi tích hợp framework.

## 2. Kiến trúc bài test

```text
Lumi web_app.py
  -> GenericHttpAsyncAgentExecutor
  -> HTTPS Render Web Service
  -> local_agent_service được deploy độc lập
  -> submit -> job_id
  -> poll status
  -> payload text
  -> Artifact
  -> Assembly
  -> Browser panel
```

## 3. Quyết định kỹ thuật đã chốt

- Fake Agent Service nằm độc lập tại:

  ```text
  D:\RAG_ManageAgent_Lumi\local_agent_service
  ```

- Service không import bất kỳ module nào từ `final_framework`.
- Service local chạy trên cổng `8010`.
- Manifest hiện bắt buộc URL `https://`; vì vậy không dùng trực tiếp
  `http://127.0.0.1:8010` trong manifest.
- Deploy cùng mã nguồn `local_agent_service` thành Render Web Service để nhận
  URL HTTPS công khai, không cần cài công cụ tunnel trên máy local.
- Không thay đổi chính sách HTTPS của framework trong phạm vi bài test này.
- Task kiểm thử có ID `remote_text_task`.
- Output thành công phải chứa marker `ASYNC_TASK_OK` để phân biệt dữ liệu thật
  từ service với nội dung do model tự tạo.

## 4. Theo dõi checkpoint

| Checkpoint | Nội dung | Trạng thái | Bằng chứng |
|---|---|---|---|
| CP1 | Xây fake Async Agent Service | Hoàn thành | `local_agent_service/`; syntax compile thành công; không import `final_framework` |
| CP2 | Kiểm thử service độc lập | Hoàn thành | 4/4 unit test pass; HTTP local submit/poll/cancel/404 đều pass |
| CP3 | Deploy service lên Render và cấp HTTPS URL | Hoàn thành | `https://lumi-multi-agent.onrender.com`; health/submit/poll/cancel qua HTTPS đều pass |
| CP4 | Thêm và validate manifest | Hoàn thành | Loader chấp nhận `remote_text_task`; task xuất hiện trong public catalog |
| CP5 | Kiểm tra activation khi startup | Chưa làm | |
| CP6 | Kiểm thử end-to-end qua Task Manager Agent | Chưa làm | |
| CP7 | Kiểm thử failure, timeout, cancel và supersede | Chưa làm | |

## 5. CP1 — Xây fake Async Agent Service

### Phạm vi

Tạo cấu trúc:

```text
local_agent_service/
├── app.py
├── requirements.txt
├── README.md
└── tests/
    └── test_service.py
```

Service cung cấp:

```http
POST /tasks
GET  /jobs/{job_id}
POST /jobs/{job_id}/cancel
GET  /health
```

### Lifecycle giả lập

```text
POST /tasks
  -> tạo job_id
  -> state=queued

GET lần đầu
  -> state=running

GET lần tiếp theo
  -> state=completed
  -> payload.passage="ASYNC_TASK_OK: ..."
```

### Input service nhận

```json
{
  "task": {
    "run_id": "run_...",
    "task_id": "task_...",
    "task_type": "remote_text_task",
    "evidence_ref": "#E1",
    "instruction": "..."
  },
  "input": {
    "query": "..."
  },
  "framework_context": {
    "knowledge_catalog": [],
    "tool_grants": []
  }
}
```

`target_slots` không được gửi cho external Agent Service. Đây là dữ liệu nội bộ
Lumi dùng để Assembly ghép Artifact vào Surface.

### Tiêu chí hoàn thành

- [x] Submit trả `job_id` là string khác rỗng.
- [x] Poll chuyển `queued/running -> completed`.
- [x] Cancel chuyển job sang `cancelled`.
- [x] Job không tồn tại trả HTTP 404.
- [x] Service được cấu hình chạy độc lập trên cổng 8010.
- [x] Service không import `final_framework`.

## 6. CP2 — Kiểm thử service độc lập

### Thực hiện

Chạy service:

```powershell
python local_agent_service/app.py
```

Kiểm thử trực tiếp:

```text
POST /tasks
  -> có job_id

GET /jobs/{job_id}
  -> running

GET /jobs/{job_id}
  -> completed + payload
```

### Terminal response mong đợi

```json
{
  "status": "completed",
  "payload": {
    "passage": "ASYNC_TASK_OK: kết quả từ dịch vụ bên ngoài"
  }
}
```

### Tiêu chí hoàn thành

- [x] Unit test của service pass.
- [x] Submit/status/cancel hoạt động qua HTTP local.
- [x] Terminal payload đúng schema dự kiến.
- [x] Marker `ASYNC_TASK_OK` do service sinh ra.

## 7. CP3 — Deploy service lên Render và cấp HTTPS URL

### Thực hiện

Deploy thư mục `local_agent_service` từ Git repository thành một Render Web
Service với cấu hình:

```text
Root Directory: local_agent_service
Build Command: pip install -r requirements.txt
Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
```

Render cấp URL công khai:

```text
https://<render-service>.onrender.com
```

Kiểm tra trực tiếp qua URL Render:

```text
GET https://<render-service>.onrender.com/health
  -> 200 OK
```

### Tiêu chí hoàn thành

- [x] URL có scheme `https`.
- [x] Framework host có thể truy cập URL.
- [x] `/health` trả 200 qua Render.
- [x] Submit/status/cancel hoạt động qua Render.
- [x] Render URL dùng cho CP4: `https://lumi-multi-agent.onrender.com`.

## 8. CP4 — Thêm manifest vào framework

### Vị trí

```text
final_framework/extensions/tasks/remote_text_task/manifest.json
```

### Manifest dự kiến

```json
{
  "id": "remote_text_task",
  "description": "Lấy một đoạn nội dung xác nhận từ dịch vụ Async bên ngoài.",
  "knowledge": {
    "allowed_tools": []
  },
  "input": {
    "payload": {
      "query": "{{task.instruction}}"
    },
    "schema": {
      "type": "object",
      "required": ["query"],
      "properties": {
        "query": {
          "type": "string",
          "minLength": 1
        }
      },
      "additionalProperties": false
    }
  },
  "executor": {
    "type": "http_async_agent",
    "submit": {
      "url": "https://<render-service>.onrender.com/tasks",
      "method": "POST",
      "job_id_path": "$.job_id"
    },
    "status": {
      "url": "https://<render-service>.onrender.com/jobs/{{job_id}}",
      "method": "GET",
      "state_path": "$.status",
      "states": {
        "queued": ["queued"],
        "running": ["running"],
        "succeeded": ["completed"],
        "failed": ["failed"],
        "cancelled": ["cancelled"]
      },
      "error_path": "$.error"
    },
    "cancel": {
      "url": "https://<render-service>.onrender.com/jobs/{{job_id}}/cancel",
      "method": "POST"
    },
    "poll_interval_seconds": 0.2,
    "timeout_seconds": 15
  },
  "output": {
    "path": "$.payload",
    "schema": {
      "type": "object",
      "required": ["passage"],
      "properties": {
        "passage": {
          "type": "string",
          "minLength": 1
        }
      },
      "additionalProperties": false
    }
  }
}
```

Thay `<render-service>` bằng hostname thực tế của CP3.

### Tiêu chí hoàn thành

- [x] Manifest loader chấp nhận package.
- [x] ID thư mục trùng `manifest.id`.
- [x] `remote_text_task` xuất hiện trong Task Catalog.
- [x] Không sửa Task Manager prompt.
- [x] Không sửa Surface Plan schema.
- [x] Không sửa Task Manager Runtime hoặc Generic Executor.

## 9. CP5 — Kiểm tra activation khi startup

### Thực hiện

Restart ứng dụng:

```powershell
cd final_framework
python web_app.py
```

### Log bắt buộc

```text
[LIVE_TASK_RUNTIME:BOUND]
task_type=remote_text_task
executor=http_async_agent
```

### Tiêu chí hoàn thành

- [ ] `web_app.py` startup thành công.
- [ ] Manifest không bị reject.
- [ ] Executor được bind tự động.
- [ ] Task Catalog cấp `remote_text_task` cho Task Manager Agent.

Nếu chưa đạt CP5 thì dừng, không tiếp tục kiểm thử qua Gemini Live.

## 10. CP6 — Kiểm thử end-to-end qua Task Manager Agent

### Yêu cầu kiểm thử

```text
Hãy tạo một panel hiển thị mã xác nhận lấy từ dịch vụ kiểm thử Async bên ngoài.
```

### Luồng log cần quan sát

```text
Task Manager Agent
  -> task_type=remote_text_task

Generic Executor
  -> POST submit
  -> nhận job_id
  -> poll status

RemoteJobRegistry
  -> queued
  -> running
  -> completed

Task Result
  -> final Artifact có output_data.passage

Complete Surface
  -> Assembly ghép passage vào target slot

Browser
  -> hiển thị ASYNC_TASK_OK
```

### Tiêu chí hoàn thành

- [ ] Task Manager Agent giao `remote_text_task`.
- [ ] Request thật đi tới `local_agent_service`.
- [ ] Service nhận đúng `task`, `input` và `framework_context`.
- [ ] Framework poll bằng đúng `job_id`.
- [ ] Remote job đạt terminal state `completed`.
- [ ] Output được validate theo manifest schema.
- [ ] Một final Artifact được tạo.
- [ ] Assembly dùng `passage` từ service.
- [ ] Browser hiển thị marker `ASYNC_TASK_OK`.
- [ ] Không có nội dung giả thay thế marker từ service.
- [ ] Không sửa orchestration core để hoàn thành lượt chạy.

## 11. CP7 — Kiểm thử lỗi và lifecycle

### Trường hợp 1: Remote failed

Service trả:

```json
{
  "status": "failed",
  "error": "forced test failure"
}
```

Mong đợi:

- [ ] Task phát `TASK_FAILED`.
- [ ] Không tạo Artifact thành công cho candidate lỗi.
- [ ] Complete Surface không báo ready giả.

### Trường hợp 2: Output sai schema

Service trả terminal payload thiếu `passage`.

Mong đợi:

- [ ] Output validation thất bại.
- [ ] Diagnostic chỉ rõ schema mismatch.
- [ ] Output sai không đi vào Assembly.

### Trường hợp 3: Timeout

Service giữ state `running` lâu hơn `timeout_seconds`.

Mong đợi:

- [ ] Generic Executor báo timeout.
- [ ] Cancel endpoint được gọi nếu manifest khai báo cancel.
- [ ] RemoteJobRegistry lưu terminal diagnostic phù hợp.

### Trường hợp 4: Mất kết nối

Tắt service khi framework đang poll.

Mong đợi:

- [ ] Task thất bại có diagnostic.
- [ ] Runtime không treo.
- [ ] Run khác vẫn có thể hoạt động.

### Trường hợp 5: Supersede

Gửi request mới khi remote task đang chạy.

Mong đợi:

- [ ] Run cũ chuyển sang superseded.
- [ ] Cancel endpoint của remote job được gọi.
- [ ] Kết quả muộn của run cũ không được ghi Artifact hoặc render lên Browser.

## 12. Điều kiện kết luận framework đạt yêu cầu

Chỉ kết luận thành công khi đủ toàn bộ bằng chứng:

```text
Manifest tự được discover
Task tự xuất hiện trong catalog
Task Manager giao đúng task_type
Generic Executor gọi URL thật
job_id được lưu và poll
Output được validate
Artifact được tạo
Assembly ghép đúng output
Browser render thành công
Không sửa orchestration core
```

Kết quả này chứng minh đúng mô hình:

```text
Developer xây Agent Service bên ngoài
  -> thêm manifest theo contract
  -> restart Lumi
  -> framework tự orchestration task mới
```

## 13. Nguyên tắc triển khai

- Thực hiện lần lượt CP1 đến CP7.
- Sau mỗi checkpoint, cập nhật bảng trạng thái và ghi bằng chứng.
- Không chuyển checkpoint nếu tiêu chí hiện tại chưa đạt.
- Không sửa kiến trúc hoặc logic ngoài phạm vi bài test nếu chưa được xác nhận.
- Nếu phát hiện manifest contract hoặc Runtime hiện tại mâu thuẫn với kế hoạch,
  phải dừng và hỏi trước khi thay đổi.
