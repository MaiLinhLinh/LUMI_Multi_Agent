# Lumi Tool Calling

Chạy bằng đúng môi trường đã có, không cần cài thêm gói:

```powershell
conda run -n LumiMultiAgent python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

Mở `http://127.0.0.1:8000`. Cấu hình model/API được đọc từ `.env` trong chính thư mục này. Ứng dụng không import hay đọc runtime từ `../code`.

Để nạp Redis snapshot thời tiết và Chroma catalog, dùng các worker được copy vào project này:

```powershell
conda run -n LumiMultiAgent python -m rag_manager.services.weather_snapshot_worker --once
conda run -n LumiMultiAgent python -m rag_manager.services.music_catalog_worker
```
