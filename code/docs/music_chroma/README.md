# Thiết lập ChromaDB local cho Music Agent

Thiết kế này thay thế hoàn toàn MongoDB Atlas. ChromaDB lưu dữ liệu trên máy tại
`code/data/chroma_music` thông qua `chromadb.PersistentClient`; không cần chạy
database server riêng ở giai đoạn hiện tại.

## Kiến trúc đã chốt

- Persistent path: `data/chroma_music`
- Collection: `music_tracks_v1`
- Dense vector: BGE-M3 qua Ollama, 1024 chiều
- Distance: cosine
- Lexical ranking: BM25 chạy trong Python trên chính `documents` từ Chroma
- Fusion: Reciprocal Rank Fusion, `rrf_k=60`
- Kết quả cuối: top 5

Cấu hình máy đọc được nằm trong
[collection_config.json](collection_config.json). Contract record nằm trong
[record_contract.json](record_contract.json).

## Vì sao BM25 không giao hoàn toàn cho Chroma local

Collection API local của Chroma có vector similarity search, metadata filter và
`where_document` cho `$contains`/regex. Các phép này là filter, không phải bảng
xếp hạng BM25. Tài liệu Hybrid Search/Sparse Vector Search hiện nằm trong nhóm
Chroma Cloud Search API.

Để vẫn đáp ứng yêu cầu hybrid search hoàn toàn trên máy:

1. Chroma HNSW trả top 50 dense-vector candidates.
2. Python BM25 xếp hạng top 50 từ cùng trường `document`.
3. Backend hợp nhất hai danh sách bằng RRF.
4. Boost exact title/artist sau RRF rồi trả top 5.

Không cộng trực tiếp cosine distance với BM25 score vì hai thang điểm khác nhau.

## Mô hình record

Theo chính sách hiện tại, mỗi bài chỉ giữ một video YouTube chính. Worker nhóm
các MV/audio/lyric/live/remix có cùng title canonical rồi chọn một nguồn theo
thứ tự: official MV, official audio, lyric video, performance, acoustic, live,
remix và cuối cùng là upload chưa phân loại. Lượt xem và ngày đăng chỉ phân xử
khi hai video cùng mức ưu tiên.

Vì vậy mỗi `track_id` có đúng một Chroma record active và một `video_id`. Các
video không được chọn chỉ xuất hiện trong báo cáo kiểm duyệt, không chiếm chỗ
trong vector database.

Mỗi record gồm:

- `id`: source ID ổn định do backend tạo.
- `document`: title + artist + genre + mood + version + tags đã chuẩn hóa.
- `embedding`: đúng 1024 số thực từ BGE-M3.
- `metadata`: chỉ scalar hoặc mảng scalar, không chứa object lồng nhau.

`artist_names` và `artist_keys` luôn là mảng không rỗng. `genres`, `moods` và
`tags` được bỏ khỏi metadata nếu chưa có dữ liệu, vì ChromaDB 1.5 không chấp
nhận mảng metadata rỗng. Worker không chèn giá trị giả như `unknown`.

Với catalog được sinh tự động, YouTube không cung cấp ngày phát hành canonical,
nên `release_date` tạm lấy từ ngày upload và được đánh dấu
`release_date_origin=youtube_published_at_proxy`. Catalog được biên tập thủ công
có thể dùng ngày phát hành thật với origin `curated`. `published_at` luôn là ngày
video được đăng lên YouTube.

## Việc bạn tự thao tác trên máy

Tôi chưa chạy các lệnh dưới đây. Khi sẵn sàng, mở terminal Conda và chạy:

```powershell
conda activate LumiMultiAgent
python -m pip install chromadb rank-bm25
```

Kiểm tra thư viện:

```powershell
python -c "import chromadb; import rank_bm25; print(chromadb.__version__)"
```

Không cần chạy `chroma run` khi dùng `PersistentClient`. Repository ở bước sau
sẽ khởi tạo bằng:

```python
import chromadb

client = chromadb.PersistentClient(path="data/chroma_music")
collection = client.get_or_create_collection(
    name="music_tracks_v1",
    configuration={"hnsw": {"space": "cosine"}},
)
```

Không gọi `client.reset()` vì thao tác đó xóa toàn bộ database và không thể hoàn
tác. Không tự tạo collection bằng script riêng ở bước này; worker/repository sẽ
tạo đúng cấu hình để tránh collection có distance khác cosine.

## Lọc và sắp xếp

LLM1 chỉ trả intent và trường lọc. Backend tự xây `where`; không truyền object
lọc từ LLM vào Chroma. Ví dụ điều kiện hợp lệ do backend tạo:

```python
where = {
    "$and": [
        {"artist_keys": {"$contains": "son tung mtp"}},
        {"track_active": {"$eq": True}},
        {"source_active": {"$eq": True}},
        {"embeddable": {"$eq": True}},
    ]
}
```

Với “bài mới nhất”, repository lấy record phù hợp rồi Python sắp
`release_date_epoch` giảm dần, nhóm theo `track_id` và lấy bài đầu tiên. Không
dùng dense score hoặc BM25 score để thay ngày phát hành.

## RAM và tiến trình

PersistentClient phù hợp cho bản local hiện tại. Để tránh tranh chấp ghi dữ liệu,
không chạy worker ghi catalog đồng thời với web app trong giai đoạn đầu. Khi cần
worker nền và web app truy cập đồng thời, có thể giữ nguyên repository interface
nhưng chuyển sang Chroma server local + `HttpClient`.

Thư mục `data/chroma_music` phải được bỏ qua bởi Git và cần backup riêng nếu dữ
liệu catalog quan trọng.

## Điều kiện hoàn tất bước chuyển đổi

- Đã cài được `chromadb` và `rank-bm25` trong environment `LumiMultiAgent`.
- Lệnh kiểm tra import chạy thành công.
- Chưa cần tạo collection hoặc nhập dữ liệu.
- Không còn cấu hình MongoDB/Atlas trong nhánh Music.

## Thu thập tự động từ kênh chính thức

Bạn không cần viết catalog cho từng bài. Chỉ cần tạo một danh sách nhỏ ánh xạ
nghệ sĩ với `official_channel_id`, dựa trên
[music_channels.example.json](music_channels.example.json). Bạn phải mở kênh
trên YouTube, xác nhận đúng nghệ sĩ rồi mới đổi `confirmed_official` thành
`true`. Worker không đoán kênh bằng tên.

Sau khi đặt `YOUTUBE_API_KEY` trong `.env`, collector thực hiện:

1. `channels.list` lấy playlist Uploads của channel ID đã xác nhận.
2. `playlistItems.list` duyệt toàn bộ video, không dùng global Search.
3. `videos.list` lấy status, embeddable, duration, statistics và snippet.
4. Loại private, không embed, quá ngắn, teaser/trailer/reaction/hậu trường.
5. Nhóm video theo title canonical và chọn đúng một nguồn chính cho mỗi bài.
6. Xếp các bài theo lượt xem của video chính và mặc định giữ top 10/nghệ sĩ.
7. Ghi catalog tự động cùng báo cáo các video bị loại/không được chọn.

Collector chỉ tạo file để kiểm tra, chưa gọi Ollama và chưa ghi Chroma:

```powershell
python -m rag_manager.services.music_youtube_collector `
  --channels-file data/music_channels.json `
  --catalog-out data/music_catalog.generated.json `
  --review-out data/music_catalog_review.json
```

Collector mặc định `--max-tracks-per-channel 10`. Nó vẫn phải duyệt toàn bộ
Uploads để tìm đúng 10 bài nhiều lượt xem nhất. `--max-videos-per-channel` chỉ
dùng cho smoke test nhanh; nếu đặt giới hạn này thì top 10 chỉ chính xác trong
phạm vi các upload đã quét. Khi ghi đè kết quả đã tồn tại, collector yêu cầu
`--force` để tránh mất báo cáo cũ.

## Worker nhập catalog và BGE-M3

Worker đọc catalog được collector sinh, gọi `POST /api/embed` của Ollama theo
batch rồi upsert đúng một record cho mỗi bài. File catalog thủ công
[music_catalog.example.json](music_catalog.example.json) vẫn được giữ để bổ sung
ngoại lệ khi cần.

Bạn tự chuẩn bị Ollama; code không tự tải hoặc khởi động model:

```powershell
ollama pull bge-m3
ollama list
```

Kiểm tra catalog tự động mà chưa gọi Ollama/Chroma:

```powershell
python -m rag_manager.services.music_catalog_worker `
  --input-file data/music_catalog.generated.json `
  --dry-run
```

Sau khi dry-run thành công, bỏ `--dry-run` để embedding và upsert:

```powershell
python -m rag_manager.services.music_catalog_worker `
  --input-file data/music_catalog.generated.json
```

Lần upsert đầu tiên mới tạo `data/chroma_music`. Khi chạy lại, worker giữ
`created_at` và tái sử dụng embedding nếu `document`, model và phiên bản
embedding không đổi. Không chạy worker ghi cùng lúc với web app khi còn dùng
`PersistentClient` nhúng trực tiếp.

Biến môi trường có thể cấu hình: `MUSIC_CHROMA_PATH`,
`MUSIC_CHROMA_COLLECTION`, `MUSIC_CATALOG_FILE`, `OLLAMA_BASE_URL`,
`MUSIC_EMBEDDING_MODEL`, `MUSIC_EMBEDDING_DIMENSIONS`,
`MUSIC_EMBEDDING_BATCH_SIZE`, `MUSIC_EMBEDDING_TIMEOUT_SECONDS`.

Nếu bạn có YouTube Data API key, đặt `YOUTUBE_API_KEY` trong `.env` rồi thêm
`--verify-youtube`. Chế độ này cập nhật kênh, ngày upload, thumbnail, thời lượng,
trạng thái public và `embeddable`; video bị xóa/private/không còn được trả về sẽ
được đánh dấu inactive. Nó không ghi đè tên bài, nghệ sĩ, ngày phát hành hoặc
`is_official`, vì các trường canonical đó cần người quản trị xác nhận.

```powershell
python -m rag_manager.services.music_catalog_worker `
  --input-file data/music_catalog.json `
  --verify-youtube
```

## Hybrid Search runtime

`MusicSearchService` nạp các record active từ Chroma và xây BM25 trong bộ nhớ
trên đúng trường `document`. Với truy vấn thông thường:

1. Query được embedding bằng Ollama BGE-M3; kết quả được cache LRU trong process.
2. Chroma nhận `query_embeddings` 1024 chiều và trả cosine ranking.
3. BM25 xếp hạng lexical độc lập, hỗ trợ tiếng Việt có dấu/không dấu.
4. RRF cộng `1 / (60 + rank)` từ hai danh sách; không cộng cosine distance với
   BM25 score.
5. Khớp title/artist chính xác được boost sau RRF; trả tối đa top 5 mặc định.

Các yêu cầu `sort_by=release_date` hoặc `sort_by=popularity` không gọi embedding.
Backend lọc theo artist/title/language/version rồi sắp trực tiếp bằng
`release_date_epoch` hoặc `popularity_score`. Vì catalog tự động dùng ngày upload,
kết quả “mới nhất” vẫn mang `release_date_origin=youtube_published_at_proxy`.

Sau khi worker cập nhật catalog trong cùng process, gọi `refresh_index()` để
BM25 đọc lại documents. Giai đoạn PersistentClient hiện tại vẫn không chạy web
app và worker ghi dữ liệu đồng thời.

## Tài liệu chính thức

- Chroma clients/PersistentClient: <https://docs.trychroma.com/docs/run-chroma/clients>
- Collection HNSW configuration: <https://docs.trychroma.com/docs/collections/configure>
- Query collection: <https://docs.trychroma.com/docs/querying-collections/query-and-get>
- Full-text filter: <https://docs.trychroma.com/docs/querying-collections/full-text-search>
- Metadata filter: <https://docs.trychroma.com/docs/querying-collections/metadata-filtering>
- Chroma Cloud hybrid search: <https://docs.trychroma.com/cloud/search-api/hybrid-search>
- Ollama embeddings: <https://docs.ollama.com/capabilities/embeddings>
- Ollama embed endpoint: <https://docs.ollama.com/api/embed>
- YouTube channels.list: <https://developers.google.com/youtube/v3/docs/channels/list>
- YouTube playlistItems.list: <https://developers.google.com/youtube/v3/docs/playlistItems/list>
- YouTube videos.list: <https://developers.google.com/youtube/v3/docs/videos/list>
