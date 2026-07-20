Kế hoạch mở rộng Music Agent
Mục tiêu: Manager nhận diện yêu cầu nghe/xem nhạc, chuyển sang Music Agent; Music Agent tìm kiếm nhạc trong ChromaDB local bằng hybrid search và hiển thị YouTube iframe. LLM2 chỉ hỏi lại khi yêu cầu chưa đủ rõ.
1. Luồng tổng thể
Người dùng
   ↓
Manager Agent
   ├─ weather → Weather Agent
   ├─ music   → Music Agent
   └─ news/wiki/...
                 ↓
        Music LLM1 trích xuất
                 ↓
       Phân loại cách truy xuất
       ├─ “mới nhất” → filter + sort theo ngày
       ├─ Tên bài rõ → hybrid search
       └─ Yêu cầu rộng → hybrid search lấy danh sách
                 ↓
       Đánh giá kết quả
       ├─ Một kết quả rõ ràng → completed → YouTube iframe
       ├─ Nhiều kết quả       → LLM2 hỏi chọn bài
       └─ Không có kết quả    → LLM2 hỏi lại
2. Mở rộng Manager Agent
Thêm music vào danh sách topic/domain.
Manager route sang Music Agent với những yêu cầu:
“Bật nhạc Sơn Tùng”
“Cho tôi nghe nhạc thư giãn”
“Mở bài Chúng ta của tương lai”
“Xem MV mới nhất của Đen Vâu”
“Bật bài thứ hai”
“Đổi sang bản live”
Phải phân biệt:
Câu hỏi	Route
“Bật nhạc Sơn Tùng”	Music
“Sơn Tùng là ai?”	Wikipedia
“Tin mới về Sơn Tùng”	News
“Thời tiết Hà Nội và bật nhạc”	Multi-domain

Manager chỉ cần chuyển domain_request và lịch sử liên quan; không tự tìm bài hát.
3. Music Agent theo workflow hai LLM
Music LLM1
Đọc query và relevant_history, sau đó trả structured output:
{
  "action": "play",
  "search_query": "bài hát mới nhất của Sơn Tùng",
  "title": null,
  "artist": "Sơn Tùng",
  "genre": null,
  "mood": null,
  "language": null,
  "version": null,
  "sort_by": "release_date",
  "sort_order": "desc",
  "selection_index": null
}
Các trường chính:
action: play, search, next, replay, stop.
search_query: truy vấn tìm kiếm độc lập, đầy đủ ngữ cảnh.
title: tên bài hát nếu được nói rõ.
artist: ca sĩ/nhóm nhạc.
genre: thể loại.
mood: tâm trạng như thư giãn, buồn, sôi động.
language: ngôn ngữ.
version: official MV, audio, live, remix, acoustic...
sort_by: release_date, popularity hoặc null.
sort_order: asc, desc hoặc null.
selection_index: dùng cho câu “bài thứ hai”.
LLM1 chỉ trích xuất ý định và bộ lọc, không tự sinh tên bài hoặc YouTube URL.
Python validation
Validator kiểm tra:
selection_index có nằm trong danh sách kết quả trước không.
sort_by có thuộc danh sách cho phép không.
action=play có đủ căn cứ tìm kiếm không.
Không cho LLM tự truyền Chroma query/filter như where, where_document, $contains hoặc $regex.
Tất cả filter Chroma phải được backend xây dựng.
Không tin tưởng URL hoặc video_id do LLM sinh ra.
Music LLM2
Chỉ gọi khi:
Chỉ có nghệ sĩ nhưng có nhiều bài.
Người dùng nói “bài đó”, “bài thứ hai” nhưng không còn ngữ cảnh.
Không tìm thấy kết quả.
Kết quả có độ tin cậy thấp hoặc nhiều kết quả gần bằng nhau.
LLM2 chỉ được sử dụng các ứng viên do database trả về, không tự nghĩ ra bài hát.
4. Quy tắc xử lý yêu cầu
Yêu cầu	Cách xử lý
“Bật bài Lạc trôi của Sơn Tùng”	Tìm chính xác, có một kết quả tốt thì phát ngay
“Bật nhạc Sơn Tùng”	Trả danh sách bài và hỏi người dùng chọn
“Bật bài mới nhất của Sơn Tùng”	Filter nghệ sĩ, sort release_date desc, lấy 1
“Bật bài nổi tiếng nhất của Sơn Tùng”	Sort theo popularity_score desc
“Cho tôi nghe nhạc thư giãn”	Hybrid search theo mood, hiển thị danh sách để chọn
“Bật bài thứ hai”	Lấy selection_index=2 từ danh sách trong session
“Đổi sang bản live”	Chỉ phát nếu video chính đã lưu là live; nếu không thì báo phiên bản chưa có
“Bật bài khác”	Loại bài đang phát, lấy kết quả tiếp theo

Không nên tự phát khi câu hỏi còn rộng, trừ khi người dùng nói rõ “phát ngẫu nhiên”, “phát bài phổ biến nhất” hoặc “bật bài mới nhất”.
5. Thiết kế dữ liệu ChromaDB local
Dùng chromadb.PersistentClient với path data/chroma_music và collection music_tracks_v1.
Mỗi bài chỉ lưu một nguồn YouTube chính. Worker nhóm các version cùng bài và chọn
official MV trước official audio, lyric video, performance/live/remix:
{
  "id": "source_...",
  "document": "Chúng ta của tương lai Sơn Tùng M-TP V-Pop official MV",
  "embedding": [0.012, -0.024],
  "metadata": {
  "schema_version": "music.chroma-source.v1",
  "track_id": "track_...",
  "canonical_key": "...",
  "title": "Chúng ta của tương lai",
  "normalized_title": "chung ta cua tuong lai",
  "artist_names": ["Sơn Tùng M-TP"],
  "artist_keys": ["son tung mtp"],
  "genres": ["V-Pop"],
  "moods": ["tình cảm"],
  "language": "vi",
  "tags": ["son tung", "vpop", "official"],
  "release_date": "2024-03-08T00:00:00Z",
  "release_date_epoch": 1709856000,
  "release_date_precision": "day",
  "popularity_score": 0.92,
  "platform": "youtube",
  "video_id": "...",
  "content_type": "official_mv",
  "channel_id": "...",
  "channel_name": "Sơn Tùng M-TP Official",
  "is_official": true,
  "embeddable": true,
  "published_at": "2024-03-08T12:00:00Z",
  "published_at_epoch": 1709899200,
  "thumbnail_url": "...",
  "duration_seconds": 285,
  "track_active": true,
  "source_active": true,
  "embedding_model": "bge-m3",
  "embedding_version": 1,
  "created_at": "...",
  "updated_at": "..."
  }
}
Phân biệt rõ:
release_date: ngày chính thức phát hành bài hát.
published_at: thời điểm video được đăng lên YouTube.
Khi người dùng hỏi “bài mới nhất”, ưu tiên release_date. Chỉ dùng published_at khi thiếu ngày phát hành chính thức.
6. Collection và index ChromaDB
Chroma tự tạo HNSW index khi collection nhận embeddings.
Collection: music_tracks_v1.
Persistent path: data/chroma_music.
Vector field: embedding 1024 chiều từ BGE-M3.
Distance: cosine.
Metadata filter: track_active, source_active, embeddable, language, artist_keys, content_type.

Chroma local hỗ trợ vector search, metadata filter và full-text filter nhưng không dùng full-text filter làm BM25 ranking. BM25 chạy trong Python trên cùng trường document được lưu ở Chroma. Web app và worker dùng chung một repository interface; giai đoạn đầu không ghi Chroma đồng thời từ hai process.
7. Hybrid Search
Luồng tìm kiếm thông thường:
Chuẩn hóa query.
Sinh query embedding bằng BGE-M3.
BM25 lấy top 30–50 kết quả.
Vector Search lấy top 30–50 kết quả.
Hợp nhất bằng Reciprocal Rank Fusion – RRF.
Boost khớp chính xác title và artist.
Loại video không active hoặc không cho embed.
Trả top 5 kết quả cho Music Agent.
Không cộng trực tiếp BM25 score với cosine score vì hai thang điểm khác nhau.
Runtime truyền `query_embeddings` do cùng BGE-M3 sinh vào Chroma, đồng thời cache
embedding của query lặp lại. BM25 được nạp từ chính documents active trong
Chroma; sau mỗi lần worker cập nhật cần refresh index.
Với truy vấn có tính toán rõ như “mới nhất”, “cũ nhất”, “phổ biến nhất”, không dùng vector để quyết định thứ tự:
where = {
    "$and": [
        {"artist_keys": {"$contains": "son tung mtp"}},
        {"track_active": {"$eq": True}},
        {"source_active": {"$eq": True}},
        {"embeddable": {"$eq": True}}
    ]
}

records = repository.get_by_metadata(where)
result = max(
    records,
    key=lambda record: record.metadata["release_date_epoch"]
)
Hybrid search chỉ hỗ trợ nhận diện đúng nghệ sĩ hoặc bài hát; kết quả cuối cùng được sắp xếp bằng trường dữ liệu có cấu trúc.
8. BGE-M3 với RAM 16 GB
Có thể chạy với RAM 16 GB nếu:
Khởi tạo model một lần khi service bắt đầu.
Không load lại model cho từng request.
Worker embedding dữ liệu theo batch.
Runtime chỉ embedding câu query ngắn.
Cache embedding cho query lặp lại.
Tách EmbeddingService khỏi Music Agent.
Nên benchmark CPU trước. Nếu latency cao, có thể chuyển embedding sang service riêng hoặc máy GPU mà không thay đổi workflow agent.
9. Worker thu thập dữ liệu
Tạo music_youtube_collector và music_catalog_worker:
Danh sách artist → official_channel_id đã được người quản trị xác nhận
        ↓
Lấy playlist Uploads của kênh bằng YouTube Data API
        ↓
Lấy metadata từ kênh chính thức
        ↓
Kiểm tra video có embeddable không
        ↓
Chuẩn hóa title, artist, version
        ↓
Ghép trùng các bản ghi
        ↓
Tạo search_text
        ↓
BGE-M3 embedding theo batch
        ↓
Upsert ChromaDB local
Người quản trị chỉ cần xác nhận channel ID chính thức, không nhập từng bài.
Collector phân biệt official MV, audio, lyric, live, remix; loại teaser,
reaction, hậu trường, video private/không embed và giữ một video chính mỗi bài.
Sau khi nhóm, mặc định xếp theo view_count của video chính và chỉ giữ 10 bài
nhiều lượt xem nhất cho mỗi nghệ sĩ.
Vì YouTube không cung cấp ngày phát hành canonical, ngày upload được dùng làm
proxy và gắn `release_date_origin=youtube_published_at_proxy`. Worker cần:
Chỉ lấy playlist Uploads của channel ID đã xác nhận, không dùng global Search.
Không coi teaser/reaction/re-upload là bài phát hành chính thức.
Định kỳ kiểm tra video bị xóa, private hoặc tắt embedding.
Không tải và lưu file nhạc/video; chỉ lưu metadata và video_id.
10. Quản lý lịch sử hội thoại
Session của Music Agent nên lưu:
{
  "last_music_request": {},
  "last_candidate_ids": ["...", "...", "..."],
  "selected_track_id": "...",
  "current_artist": "Sơn Tùng M-TP",
  "current_track_id": "...",
  "current_source_id": "..."
}
Nhờ đó có thể xử lý:
“Bài thứ hai.”
“Bật bản live.”
“Bài khác của ca sĩ này.”
“Quay lại bài trước.”
“Cho tôi bài mới hơn.”
Query mới ghi đè trường được nhắc đến; trường còn thiếu được kế thừa nếu rõ ràng là câu tiếp nối.
11. Payload trả về giao diện
Khi hoàn tất:
{
  "status": "completed",
  "ui_type": "youtube_player",
  "music": {
    "track_id": "...",
    "title": "Chúng ta của tương lai",
    "artist": "Sơn Tùng M-TP",
    "video_id": "...",
    "thumbnail_url": "...",
    "content_type": "official_mv"
  }
}
Không trả HTML hoặc URL tùy ý từ database. Frontend lấy video_id và tự tạo:
https://www.youtube-nocookie.com/embed/{video_id}
Cần kiểm tra định dạng video_id và chỉ cho phép domain YouTube đã cấu hình.
12. Giao diện
Khi có kết quả Music:
Bên trái hiển thị YouTube iframe và thông tin bài hát.
Bên phải giữ lịch sử chat và ô nhập.
Khi người dùng nói chuyện xã giao, player vẫn giữ nguyên.
Khi chọn bài mới, player thay video_id.
Khi có nhiều kết quả, hiển thị danh sách card để người dùng chọn.
Không phụ thuộc hoàn toàn vào autoplay vì trình duyệt có thể chặn tự phát có âm thanh.
Weather và Music có thể dùng chung vùng hiển thị bên trái:
ui_type=weather         → Weather template
ui_type=youtube_player  → YouTube iframe
13. Cấu trúc code đề xuất
rag_manager/
  agents/
    music.py
    music_structured_schema.py

  services/
    music_repository.py
    music_search_service.py
    music_embedding_service.py
    music_result_validator.py
    music_catalog_worker.py

  llm/
    prompts.py

web/
  app.js
  app.css
  index.html
Chỉ sửa các điểm tích hợp cần thiết trong Manager, workflow graph và web UI; không thay đổi logic Weather.
14. Kiểm thử và benchmark
Cần đo:
Music routing accuracy.
LLM1 extraction accuracy.
Recall@5 của hybrid search.
MRR@5 hoặc NDCG@5.
Tỉ lệ chọn nhầm bài.
Tỉ lệ hỏi lại đúng lúc.
Search latency p50/p95.
Embedding latency p50/p95.
Tổng latency từ query đến iframe.
Test case quan trọng:
Tên bài có dấu/không dấu.
Nghệ sĩ có nghệ danh tương tự.
Một bài có nhiều bản live/remix.
“Bài mới nhất”, “bài nổi tiếng nhất”.
“Bài thứ hai”, “bài khác”, “bản live”.
Video bị xóa hoặc không cho nhúng.
LLM tự sinh video_id không có trong database.
Query độc hại cố truyền Chroma where/where_document hoặc iframe URL.
15. Thứ tự triển khai
Thêm route music vào Manager.
Tạo schema và prompt cho Music LLM1.
Thiết kế ChromaDB local collection, record contract và BM25 phía Python.
Viết worker nhập metadata và tạo embedding.
Xây BM25, Vector Search và RRF.
Viết Music Agent workflow và validator.
Thêm session cho danh sách bài và bài đang phát.
Thêm payload youtube_player.
Tích hợp iframe vào giao diện hai cột.
Viết unit test, integration test và benchmark.
Sau khi ổn định mới bổ sung playlist, lịch sử nghe, gợi ý cá nhân và lệnh điều khiển player.

Đúng, cách bạn hiểu hợp lý và tối ưu hơn.
Luồng nên là:
Query
→ LLM1 trích xuất
→ Database tìm kiếm
→ Lưu danh sách kết quả vào session
→ Yêu cầu người dùng chọn
→ Lấy trực tiếp kết quả đã lưu
Database không “trả về từ LLM1”. LLM1 chỉ tạo điều kiện tìm kiếm; database trả candidates và backend lưu chúng:
{
  "music_candidates": [
    {
      "track_id": "track_01",
      "title": "Lạc trôi",
      "artist": "Sơn Tùng M-TP",
      "version": "official_mv",
      "video_id": "..."
    },
    {
      "track_id": "track_02",
      "title": "Chúng ta của tương lai",
      "artist": "Sơn Tùng M-TP",
      "version": "official_mv",
      "video_id": "..."
    }
  ]
}
Không cần truyền toàn bộ Chroma records, embedding hoặc metadata dài vào LLM2. Việc đó vừa thừa prompt vừa tăng latency.
Với trường hợp có nhiều bài, thực tế có thể không cần LLM2. Backend trả câu hỏi cố định cùng danh sách:
Bạn muốn nghe bài nào của Sơn Tùng?

Người dùng trả lời:
“Bài 2” → lấy trực tiếp candidate thứ hai, không gọi LLM1 và không search lại.
“Chúng ta của tương lai” → đối chiếu với danh sách đã lưu rồi phát.
“Bản live” → LLM1 nhận diện bộ lọc mới, kế thừa tên bài/nghệ sĩ và search lại.
“Không, cho tôi bài của Đen Vâu” → LLM1 nhận diện yêu cầu mới và search lại.
LLM2 chỉ thật sự cần khi phải tạo câu hỏi tự nhiên cho lỗi phức tạp. Khi đó chỉ truyền dữ liệu tối thiểu:
{
  "reason": "multiple_matches",
  "field": "title",
  "candidate_summaries": [
    "Lạc trôi — official MV",
    "Chúng ta của tương lai — official MV"
  ]
}
Tóm lại: danh sách kết quả phải được lưu trong session phía backend. Người dùng chọn kết quả có sẵn thì lấy trực tiếp; chỉ gọi lại LLM1 và truy xuất database khi người dùng bổ sung hoặc thay đổi điều kiện tìm kiếm. LLM2 không nên giữ hoặc quyết định dữ liệu bài hát.


<iframe width="560" height="315" src="https://www.youtube.com/embed/FN7ALfpGxiI?si=S8jMrl6gmpN4SHg6" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>
Đây là 1 iframe 1 video youtube, 
tức là code sẽ mặc định trước các trường khác, còn src thì sẽ được render vào sau khi có kết quả đúng không






11:01 AM






Đúng. Code giao diện định nghĩa sẵn cấu trúc iframe và các thuộc tính an toàn; sau khi tìm được bài hát, chỉ cập nhật video_id hoặc src.
Ví dụ database trả về:
{
  "video_id": "FN7ALfpGxiI"
}
Frontend tạo URL:
const embedUrl =
  `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}`;
Sau đó gán vào iframe:
<iframe
  id="youtube-player"
  width="560"
  height="315"
  title="YouTube video player"
  frameborder="0"
  allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture"
  referrerpolicy="strict-origin-when-cross-origin"
  allowfullscreen>
</iframe>
document.getElementById("youtube-player").src = embedUrl;
Nên lưu và trả về video_id, không lưu toàn bộ src hoặc iframe trong Chroma metadata. Backend cần kiểm tra video_id đúng định dạng YouTube trước khi gửi cho frontend. Các thuộc tính khác như allow, referrerpolicy, allowfullscreen được cố định trong code để tránh chèn URL hoặc HTML độc hại.
