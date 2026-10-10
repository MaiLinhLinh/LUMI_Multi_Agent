# Local fake Async Agent Service

Đây là service kiểm thử độc lập cho giao thức Async Agent Task của Lumi. Service
không import hoặc gọi trực tiếp bất kỳ module nào trong `final_framework`.

## Chạy service

```powershell
python -m pip install -r local_agent_service/requirements.txt
python local_agent_service/app.py
```

Service lắng nghe tại `http://127.0.0.1:8010` và cung cấp:

- `GET /health`
- `POST /tasks`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`

## Lifecycle

`POST /tasks` tạo một job ở trạng thái `queued` và trả `job_id`. Lần poll đầu
trả `running`; lần poll tiếp theo trả `completed` cùng
`payload.passage` chứa marker `ASYNC_TASK_OK`.

Job đang chờ hoặc đang chạy có thể chuyển sang `cancelled` qua cancel endpoint.
Toàn bộ job chỉ được lưu trong bộ nhớ và mất khi service dừng.
