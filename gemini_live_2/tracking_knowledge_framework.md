# Tracking — Knowledge Framework và hai Knowledge Worker

> Tài liệu này ghi lại kiến trúc đã chốt cho kho knowledge mở rộng được theo
> package, cách ingest/lưu trữ/embedding/truy xuất và luồng Plan Agent giao việc
> cho hai Gemini Flash worker.
>
> Đây là file tracking thiết kế. Ở checkpoint khởi tạo này chưa sửa luồng
> `route_request`, Plan Agent, Gemini Live, Compiler, Browser hoặc Lumi Studio.

## 0. Trạng thái tổng quan

| Mục | Trạng thái | Ghi chú |
|---|---|---|
| Kiến trúc registry dùng chung | Đã chốt về mặt ý tưởng | `KnowledgeRegistry` nằm cùng tầng với `DomainRegistry` và `SharedResourceRegistry`. |
| Mô hình KnowledgePackage | Đã chốt về mặt ý tưởng | Package chứa manifest, schema và provider; không thuộc riêng một domain. |
| Hai worker Gemini Flash | Đã chốt về mặt ý tưởng | Một worker tìm nội dung/text, một worker tìm media; đều không biết dữ liệu nằm ở nguồn nào. |
| Kho Wikipedia đầu tiên | Phạm vi triển khai đầu tiên | Provider đầu tiên là Wikipedia/Wikimedia; framework không khóa vào Wikipedia. |
| Storage tạm thời | Đã chốt cho giai đoạn hiện tại | Chroma cho text/media index, PostgreSQL cho catalog/quan hệ/provenance, filesystem cho file ảnh. |
| Canonical `.comp.gz` | Chưa triển khai | Để sau khi cần lưu revision/source-of-truth; không phải điều kiện của worker hiện tại. |
| Code runtime | Chưa bắt đầu | Chỉ triển khai sau khi các contract và các điểm cần tích hợp được duyệt. |

## 1. Phạm vi và các nguyên tắc không thay đổi

### 1.1. Mục tiêu

Xây dựng một framework knowledge local có thể thêm nhiều kho dữ liệu theo chiều
ngang, chẳng hạn:

```text
Wikipedia tiếng Việt
Tài liệu công ty
Kho truyện cổ tích
Kho dữ liệu sản phẩm
Kho dữ liệu được crawl từ Internet
```

Mỗi kho là một `KnowledgePackage` có provider riêng. Knowledge package nằm
ngoài domain package: domain không sở hữu dữ liệu, không sở hữu Chroma
collection và không phải khai báo hay viết lại worker khi thêm một kho mới.

### 1.2. Giữ nguyên entry point hiện có

Luồng người dùng vẫn bắt đầu bằng:

```text
Gemini Live
  → route_request(domain_id, intent)
  → Plan Agent run
```

Việc chuyển sang worker không tạo một loại route mới và không thay thế
`route_request`. Thay đổi tương lai chỉ mở rộng phần Plan Agent có thể giao
nhiệm vụ truy xuất, sau khi đã chốt contract tích hợp cụ thể.

Các phần sau không nằm trong checkpoint tài liệu này:

- thay prompt hiện tại của Gemini Live;
- thay logic quyết định create/patch của Plan Agent;
- thay Compiler, Surface Plan, Browser hoặc `SURFACE_READY`;
- buộc mọi knowledge package phải có cùng schema nghiệp vụ;
- buộc mọi kết quả phải là fact, mốc thời gian hoặc event;
- gộp text và media ở Runtime thành một kết quả đã chọn sẵn.

### 1.3. Quy tắc trách nhiệm

```text
Plan Agent        quyết định cần tìm gì, giao worker nào, song song hay tuần tự,
                  đánh giá result và quyết định có tìm bổ sung hay không.

Worker Gemini     nhận nhiệm vụ cụ thể, truy xuất, tự kiểm tra acceptance
                  criteria, trả result và provenance; không tự thiết kế Surface.

Runtime           điều phối worker, chuyển result nguyên vẹn về Plan Agent,
                  không tự chọn fact/media thay Plan Agent.

KnowledgePackage  mô tả dữ liệu, schema, ingestion provider và retrieval
                  provider; không biết hội thoại hay bố cục panel.

KnowledgeRegistry đăng ký và tra cứu package/provider; không phải nơi chứa toàn
                  bộ binary hay thay worker quyết định nội dung.
```

Không có worker gọi trực tiếp worker còn lại. Nếu media cần `entity_id` mà Text
Worker mới tìm ra, Plan Agent nhận result đó rồi quyết định giao một nhiệm vụ
media thứ hai với `entity_id` tương ứng.

## 2. Registry dùng chung

### 2.1. Vị trí kiến trúc

```text
REGISTRY DÙNG CHUNG

DomainRegistry
└─ Domain packages
   └─ manifest + presentation prompt + plan prompt

KnowledgeRegistry
└─ Knowledge packages
   ├─ wikipedia_vi
   ├─ company_docs
   └─ fairy_tales_vi

SharedResourceRegistry
└─ asset catalog + template catalog

ExtensionLoader
└─ widget/effect/browser modules
   + knowledge provider modules
```

`KnowledgeRegistry` là sibling của `DomainRegistry` và
`SharedResourceRegistry`. `ExtensionLoader` là cơ chế nạp/validate module; nó
không phải một knowledge store và không thay thế `KnowledgeRegistry`.

### 2.2. Knowledge độc lập với domain

`KnowledgePackage` hoàn toàn độc lập với `DomainPackage`:

- Không nằm trong thư mục/package của domain.
- Không được khai báo bằng trường `knowledge_packages` trong manifest domain.
- Không bị gắn với một domain cụ thể và có thể được dùng bởi nhiều domain.
- Tự đăng ký, tự khai báo capability và provider với `KnowledgeRegistry`.

Domain chỉ cung cấp ngữ cảnh và prompt cho trải nghiệm. Khi cần dữ liệu,
Plan Agent nêu yêu cầu truy xuất; runtime resolve package/provider qua
`KnowledgeRegistry` theo `package_id` hoặc capability. Quan hệ sử dụng này
không biến knowledge thành tài sản hay dependency của domain.

Nếu cần giới hạn package nào được phép dùng trong một môi trường, quy tắc đó
đặt ở policy của `KnowledgeRegistry` hoặc runtime configuration riêng, không
đặt trong `DomainPackage`.

### 2.3. Cấu trúc package mục tiêu

```text
knowledge_packages/
└─ wikipedia_vi/
   ├─ manifest.json
   ├─ schemas/
   │  ├─ document.schema.json
   │  ├─ entity.schema.json
   │  ├─ relation.schema.json
   │  └─ media.schema.json
   ├─ ingestion_provider.py
   ├─ retrieval_provider.py
   └─ policies/
      ├─ retrieval.json
      └─ provenance.json
```

Package khác có thể có schema khác. Chỉ envelope truy vấn/kết quả ở framework
là chung; payload nghiệp vụ do package định nghĩa.

Manifest cần mô tả tối thiểu:

```json
{
  "id": "wikipedia_vi",
  "display_name": "Wikipedia tiếng Việt",
  "capabilities": ["text", "media", "entities", "relations"],
  "ingestion_provider": "ingestion_provider.py",
  "retrieval_provider": "retrieval_provider.py",
  "schemas": {
    "document": "schemas/document.schema.json",
    "entity": "schemas/entity.schema.json",
    "relation": "schemas/relation.schema.json",
    "media": "schemas/media.schema.json"
  },
  "storage": {
    "text_collection": "wiki_text",
    "media_collection": "wiki_media"
  }
}
```

Đây là schema đề xuất để triển khai, chưa phải API tương thích ngược của code
hiện tại. Tên field/model embedding/chunk size phải được chốt trước checkpoint
implement manifest.

## 3. Mô hình dữ liệu tổng quát

Framework không giả định mọi kho đều có “mốc thời gian”. Nó dùng các định danh
liên kết chung; phần ý nghĩa cụ thể đến từ schema của package.

### 3.1. Envelope tài liệu

```text
Document
├─ document_id / page_id
├─ package_id
├─ title
├─ language
├─ source_uri
├─ source_revision hoặc fetched_at
└─ sections/passages do package schema định nghĩa
```

### 3.2. Entity và relation

```text
Entity
├─ entity_id
├─ package_id
├─ canonical_name
├─ entity_type
└─ aliases/metadata

Relation
├─ subject_entity_id
├─ predicate
├─ object_entity_id hoặc literal
├─ source_document_id
├─ confidence
└─ provenance
```

`entity_id` là khóa nối quan trọng giữa text và media. `page_id`/`document_id`
là khóa nối thứ hai để biết hai kết quả cùng nguồn. `event_id` chỉ xuất hiện nếu
package có schema event hoặc provider tạo được định danh ổn định; không bắt
buộc mọi package phải có `event_id`.

### 3.3. Media

```text
MediaRecord
├─ media_id
├─ package_id
├─ object_key / local_path
├─ mime_type
├─ caption / alt_text / title
├─ source_uri
├─ linked_entity_ids[]
├─ linked_document_ids[]
├─ license/provenance
└─ embedding metadata
```

Một media record có thể liên kết nhiều entity hoặc document. Một event/text
result không bắt buộc phải có ảnh cụ thể. Media Worker có thể trả ảnh của entity
liên quan, ảnh của page, hoặc ảnh minh họa tổng quát; Plan Agent quyết định ảnh
có phù hợp bố cục hay không.

## 4. Storage local đã chốt cho giai đoạn đầu

```text
knowledge_data/
├─ media/
│  ├─ sha256_a13f.jpg
│  └─ sha256_b82c.png
├─ chroma/
│  ├─ wiki_text/
│  └─ wiki_media/
└─ postgres/
   └─ entities, relations, metadata, provenance
```

### 4.1. Filesystem — binary media

Ảnh được tải về và lưu local bằng content hash hoặc object key ổn định:

```text
knowledge_data/media/<sha256>.<extension>
```

Database không cần chứa toàn bộ binary ảnh. `MediaRecord.object_key` trỏ tới
file local; sau này object key có thể trỏ tới MinIO/S3 mà contract worker không
đổi.

### 4.2. Chroma — vector retrieval index

Chroma không tự “hiểu” query người dùng. Ingestion phải tạo embedding cho từng
record; khi truy xuất, query cũng được embedding bằng cùng model/phiên bản rồi
Chroma tìm các vector gần nhất. Metadata chứa các khóa để truy về catalog.

Collection text (`wiki_text`) lưu:

```text
id        = chunk_id
document  = nội dung chunk
embedding = vector của chunk
metadata  = {
  package_id, page_id, section_id, entity_ids, language,
  title, source_uri, revision, embedding_model, schema_version
}
```

Collection media (`wiki_media`) lưu:

```text
id        = media_id
document  = caption + alt_text + title đã chuẩn hóa
embedding = vector mô tả media
metadata  = {
  package_id, media_id, page_id, entity_ids, object_key,
  source_uri, mime_type, caption, embedding_model, schema_version
}
```

Ở giai đoạn đầu, media embedding tối thiểu là embedding của caption/alt/title
để tìm ảnh theo ngữ nghĩa. Nếu dùng thêm mô hình embedding hình ảnh, vector hình
ảnh phải ghi rõ `embedding_kind`/`embedding_model`; không trộn hai loại vector
khác chiều vào cùng collection.

Chroma là index phục vụ truy xuất, không phải nơi Plan Agent tự quyết định kết
quả cuối cùng và cũng không thay provenance catalog.

### 4.3. PostgreSQL — catalog, graph và provenance

PostgreSQL lưu thông tin có cấu trúc, quan hệ và nguồn gốc:

```text
documents(
  page_id, package_id, title, language, source_uri,
  source_revision, fetched_at, checksum
)

entities(
  entity_id, package_id, canonical_name, entity_type, aliases, metadata_json
)

relations(
  relation_id, package_id, subject_entity_id, predicate,
  object_entity_id/object_value, source_page_id, confidence
)

media(
  media_id, package_id, object_key, mime_type, caption,
  source_uri, checksum, license, metadata_json
)

entity_media(
  entity_id, media_id, relation_type, confidence, source_page_id
)

provenance(
  record_id, record_type, package_id, source_uri,
  fetched_at, revision, extraction_method, checksum
)
```

Các bảng trên là catalog/ref map. Nội dung chunk dùng để vector search nằm trong
Chroma; binary ảnh nằm trong `media/`. Không sao chép toàn bộ văn bản hoặc binary
vào PostgreSQL ở giai đoạn đầu.

### 4.4. Chưa dùng `.comp.gz`

Hiện tại chưa cần Canonical Wikipedia Store dạng `.comp.gz`. Nếu sau này cần
revision history hoặc khôi phục bản gốc, có thể thêm:

```text
knowledge_data/canonical/wikipedia_vi/<page_key>.comp.gz
```

Khi đó PostgreSQL lưu `object_key` và revision metadata; Chroma vẫn là index.
Việc thêm canonical store không được làm thay đổi envelope truy vấn của worker.

## 5. Ingestion pipeline

### 5.1. Luồng tổng quát

```text
Wikipedia/Wikimedia API
        ↓
Lấy bài viết, section, caption, ảnh và revision metadata
        ↓
Làm sạch, chuẩn hóa, kiểm tra checksum/provenance
        ├─ Tách text thành chunks
        ├─ Tạo text embeddings
        ├─ Ghi chunks + metadata + vectors vào Chroma/wiki_text
        ├─ Tải ảnh về knowledge_data/media/
        ├─ Chuẩn hóa caption/alt/title
        ├─ Tạo media embeddings
        ├─ Ghi media records + vectors vào Chroma/wiki_media
        └─ Ghi documents/entities/relations/media/provenance vào PostgreSQL
```

### 5.2. Chia chunk

Chunk phải giữ metadata liên kết, không chỉ lưu một đoạn text rời:

```text
chunk_id
page_id
section_id
entity_ids[]
title
language
text
source_uri
revision
```

Chunk boundary nên ưu tiên section/đoạn văn có nghĩa. `chunk_size` và
`overlap` là cấu hình của package/policy, không hard-code trong worker. Các giá
trị cụ thể (token hay ký tự) cần benchmark trước khi chốt.

### 5.3. Embedding

Mỗi record phải ghi model và phiên bản embedding để không so sánh vector sinh
bởi các model không tương thích:

```text
embedding_model
embedding_dimension
embedding_kind = text | media_caption | image
normalization
created_at
```

Khi đổi model, tạo collection/index version mới hoặc re-embed toàn bộ package;
không âm thầm trộn vector cũ và mới.

## 6. Hai worker Gemini Flash

### 6.1. Vai trò

Hai worker được phân biệt theo capability truy xuất, không theo nguồn dữ liệu:

```text
Fact/Text Worker
  → tìm passage, entity, relation, fact, event hoặc loại text mà package hỗ trợ

Media Worker
  → tìm media candidate, caption và liên kết entity/page/document
```

Worker không biết đây là Wikipedia, tài liệu công ty hay kho truyện. Worker chỉ
biết mình được cấp một `KnowledgeRegistry` và các capability phù hợp.

Không bắt buộc một package phải có cả text và media. Nếu package chỉ có text,
Media Worker trả trạng thái thiếu dữ liệu thay vì bịa ảnh.

### 6.2. Plan Agent giao nhiệm vụ

Plan Agent vẫn là nơi hiểu intent và thiết kế Surface. Nó không chỉ nói “gọi
Wikipedia”; nó mô tả rõ mục tiêu worker, ràng buộc và điều kiện đạt:

```json
{
  "task_id": "wt_001",
  "plan_run_id": "run_abc",
  "worker": "fact_worker",
  "objective": "Tìm các mốc trong hành trình cứu nước của Hồ Chí Minh",
  "requirements": {
    "language": "vi",
    "max_items": 4,
    "need_entities": true,
    "need_citations": true
  },
  "context": {
    "query": "hành trình cứu nước Hồ Chí Minh",
    "entity_ids": []
  },
  "acceptance": [
    "Mỗi item có thời gian hoặc khoảng thời gian nếu nguồn cung cấp",
    "Mỗi item có source_uri và passage đủ ngữ cảnh",
    "Không dùng item không xác minh được provenance"
  ],
  "allow_external_fallback": true
}
```

Media assignment có thể độc lập:

```json
{
  "task_id": "mt_001",
  "plan_run_id": "run_abc",
  "worker": "media_worker",
  "objective": "Tìm ảnh minh họa phù hợp cho các mốc đã biết",
  "requirements": {
    "language": "vi",
    "max_items": 4,
    "need_caption": true,
    "need_provenance": true
  },
  "context": {
    "entity_ids": ["entity_ho_chi_minh"],
    "page_ids": [],
    "event_hints": ["1911", "..." ]
  },
  "acceptance": [
    "Ảnh phải có caption hoặc mô tả nguồn",
    "Ảnh phải truy được object_key/source_uri",
    "Nêu rõ nếu chỉ là ảnh liên quan entity chứ không phải ảnh đúng mốc"
  ],
  "allow_external_fallback": true
}
```

`worker` ở đây chỉ là capability worker; nó không áp đặt Plan Agent phải tạo
`create_surface`, `patch_surface` hay widget nào.

### 6.3. Song song, tuần tự hoặc gọi lẻ

Plan Agent quyết định dependency logic:

```text
Không phụ thuộc:
  Plan → Text Worker ┐
                     ├→ Plan đánh giá kết quả
  Plan → Media Worker┘

Media phụ thuộc entity do text tìm ra:
  Plan → Text Worker → nhận entity_ids → Plan → Media Worker

Chỉ cần text:
  Plan → Text Worker → Plan

Chỉ cần ảnh:
  Plan → Media Worker → Plan
```

Runtime có thể thực thi hai assignment độc lập song song, nhưng không tự biến
một assignment tuần tự thành song song. Plan Agent nhận từng result và tự đánh
giá có đủ chuẩn kế hoạch hay phải giao lại nhiệm vụ.

### 6.4. Worker tự kiểm tra và fallback Google

Worker Gemini làm ba việc trong một nhiệm vụ:

```text
1. Truy xuất local qua KnowledgeRegistry/provider.
2. Đối chiếu result với acceptance criteria của Plan Agent.
3. Nếu chưa đạt và task cho phép fallback, tìm Google/native grounding;
   gắn provenance rõ là external, rồi kiểm tra lại trước khi trả.
```

Worker không tự quyết định thay Plan Agent rằng Surface phải dùng item nào. Nó
chỉ báo mức đáp ứng, thiếu gì và nguồn nào đã dùng. Fallback Google phải có
policy/credential/adapter riêng; chưa chốt API cụ thể ở checkpoint này.

## 7. Retrieval flow chi tiết

### 7.1. Text Worker

```text
Worker nhận objective + requirements
        ↓
Chuẩn hóa query và entity hints
        ↓
Resolve package/provider qua KnowledgeRegistry
        ↓
Embed query bằng model tương thích collection
        ↓
Chroma wiki_text top-k + metadata filters
        ↓
Hydrate catalog/provenance từ PostgreSQL khi cần
        ↓
Xếp hạng/lọc theo acceptance criteria
        ↓
Trả text candidates + entity/page/chunk IDs + citations
```

Có thể bổ sung lexical filter hoặc reranker trong provider, nhưng đó là chi
tiết của package/policy; Plan Agent không cần biết provider dùng Chroma thuần
vector hay kết hợp thêm bộ lọc.

### 7.2. Media Worker

```text
Worker nhận objective + entity/page/event hints
        ↓
Resolve package/provider qua KnowledgeRegistry
        ↓
Embed query/caption hint hoặc dùng entity/page filter
        ↓
Chroma wiki_media top-k
        ↓
Join entity/page/object_key từ PostgreSQL
        ↓
Kiểm tra file media tồn tại, MIME, provenance và caption
        ↓
Trả media candidates; không tự chọn ảnh cuối cùng
```

Nếu Text Worker trả `entity_id` của một mốc nhưng không có ảnh đúng mốc, Media
Worker được phép trả ảnh entity tổng quát hoặc ảnh page liên quan, đồng thời ghi
rõ loại liên kết. Plan Agent sẽ quyết định có dùng hay bỏ.

### 7.3. Kết quả không được Runtime gộp thay Plan Agent

Runtime chuyển riêng result của từng worker:

```text
TextWorkerResult  → Plan Agent
MediaWorkerResult → Plan Agent
```

Runtime chỉ bảo toàn `entity_id`, `page_id`, `chunk_id`, `media_id` và
provenance để Plan Agent tự ghép/đối chiếu. Không có luật “mỗi event bắt buộc có
một ảnh”.

## 8. Contract kết quả worker

Envelope chung:

```json
{
  "task_id": "wt_001",
  "plan_run_id": "run_abc",
  "worker": "fact_worker",
  "status": "completed",
  "items": [],
  "self_check": {
    "passed": true,
    "met": ["..."],
    "unmet": [],
    "notes": "..."
  },
  "provenance": [],
  "retrieval": {
    "package_id": "wikipedia_vi",
    "provider": "wikipedia_vi.retrieval_provider",
    "local_hits": 4,
    "external_fallback_used": false
  },
  "warnings": []
}
```

`items[].payload` do package schema định nghĩa. Ví dụ text item có thể chứa
`passage`, `date`, `entity_ids`; media item có thể chứa `caption`, `media_id`,
`object_key`. Framework chỉ yêu cầu các khóa liên kết/provenance cần để truy
ngược.

Các trạng thái tối thiểu:

```text
completed        có result hợp lệ
completed_partial có một phần result, còn requirement chưa đạt
not_found        provider không tìm thấy
failed           lỗi thực thi hoặc lỗi provider
cancelled        task bị hủy trước khi hoàn tất
```

Không trả item “đoán” khi không có nguồn. Fallback external phải được đánh dấu
riêng để Plan Agent cân nhắc độ tin cậy.

## 9. Plan Agent đánh giá sau mỗi result

Sau mỗi result, Plan Agent không mặc định dùng ngay. Nó kiểm tra:

```text
result có đúng task_id/plan_run_id không?
đã đủ số lượng và trường bắt buộc chưa?
passage/caption có đủ ngữ cảnh không?
entity_id/page_id có giúp liên kết không?
provenance có hợp lệ không?
result local hay external, độ tin cậy ra sao?
đã đủ để thiết kế Surface chưa?
```

Nếu chưa đủ, Plan Agent có thể:

```text
giao lại worker với query/context hẹp hơn;
giao Media Worker sau khi có entity_ids;
giao Text Worker bổ sung passage/citation;
chỉ dùng một phần result;
hoặc kết thúc với trạng thái thiếu dữ liệu và thiết kế Surface phù hợp.
```

Việc gọi lại là quyết định của Plan Agent; Runtime không tự retry vô hạn và
không tự suy ra rằng result “đủ tốt”.

## 10. Luồng end-to-end mục tiêu

```text
Người dùng yêu cầu
        ↓
Gemini Live gọi route_request(domain_id, intent)
        ↓
Plan Agent nhận run + domain context
        ↓
Plan Agent phân tích mục tiêu và tiêu chí dữ liệu
        ↓
Plan Agent tạo một hoặc hai worker assignment
        ├─ dispatch song song nếu độc lập
        └─ dispatch tuần tự nếu media cần entity/page từ text
        ↓
Gemini Flash worker truy xuất local qua KnowledgeRegistry
        ↓
Worker tự kiểm tra acceptance criteria
        ├─ đạt: trả result + provenance
        └─ chưa đạt: fallback Google nếu được phép, rồi kiểm tra lại
        ↓
Runtime chuyển từng WorkerResult về Plan Agent
        ↓
Plan Agent đánh giá, bổ sung task nếu cần
        ↓
Plan Agent chọn dữ liệu và thiết kế Surface Plan
        ↓
Compiler + Runtime + Browser hoạt động theo luồng hiện có
```

## 11. Các interface cần có khi bắt đầu implement

Đây là các boundary dự kiến, chưa phải thay đổi đã thực hiện trong code:

```text
KnowledgePackage
  manifest
  schemas
  ingestion_provider
  retrieval_provider
  policies

KnowledgeRegistry
  register(package)
  resolve(package_id)
  find_by_capability(capability)
  list_packages()

KnowledgeRetrievalProvider
  search_text(request) -> provider result
  search_media(request) -> provider result

WorkerDispatcher
  submit(worker_task)
  submit_many(worker_tasks, parallel=...)
  cancel(task_id)

WorkerResult
  task_id, plan_run_id, status, items,
  self_check, provenance, retrieval, warnings
```

Tên module/class thực tế phải đối chiếu với code hiện tại trước khi triển khai;
không tự tạo file core chỉ dựa trên các tên minh họa này.

## 12. Checkpoint triển khai

| Checkpoint | Nội dung | Trạng thái | Điều kiện hoàn thành |
|---|---|---|---|
| KF0 | Ghi nhận kiến trúc, storage, embedding, retrieval và worker flow | **Hoàn thành tài liệu** | File tracking này được duyệt làm baseline. |
| KF1 | Audit code hiện tại | Chưa bắt đầu | Xác định registry/domain/Plan loop/provider boundary thật trong repo; không sửa code. |
| KF2 | Chốt public contract `KnowledgePackage`/`KnowledgeRegistry` | Chưa bắt đầu | Manifest/schema/provider contract được duyệt. |
| KF3 | Implement loader/registry cho package | Chưa bắt đầu | Load/validate package độc lập domain; test duplicate/invalid package. |
| KF4 | Chốt schema ingestion Wikipedia | Chưa bắt đầu | Document/chunk/entity/relation/media/provenance schema được duyệt. |
| KF5 | Implement Chroma text/media index | Chưa bắt đầu | Embedding version, metadata filter, collection isolation và rebuild policy có test. |
| KF6 | Implement PostgreSQL catalog/relations/provenance | Chưa bắt đầu | Entity/page/media mapping truy hồi được bằng ID. |
| KF7 | Implement filesystem media store | Chưa bắt đầu | Hash, MIME, object key, duplicate và missing file được xử lý. |
| KF8 | Implement Wikipedia ingestion provider | Chưa bắt đầu | API → normalize → Chroma/Postgres/media có provenance và idempotency. |
| KF9 | Implement Wikipedia retrieval provider | Chưa bắt đầu | Text/media search trả candidates có score, IDs và citations. |
| KF10 | Chốt WorkerTask/WorkerResult và dispatcher | Chưa bắt đầu | Plan có thể giao text/media task; runtime hỗ trợ parallel/sequential/cancel. |
| KF11 | Adapter hai Gemini Flash worker | Chưa bắt đầu | Worker gọi provider local, self-check và trả result đúng envelope. |
| KF12 | Google fallback của worker | Chưa bắt đầu | Policy, API/grounding, provenance và giới hạn fallback được duyệt. |
| KF13 | Tích hợp Plan Agent | Chưa bắt đầu | Route entry giữ nguyên; Plan nhận từng result, đánh giá và có thể giao task tiếp. |
| KF14 | Tests/evaluation end-to-end | Chưa bắt đầu | Các case text-only, media-only, parallel, dependent, not-found, fallback và cancel pass. |
| KF15 | Tài liệu package authoring | Chưa bắt đầu | Có thể thêm company/fairy-tales package mà không sửa worker core. |

## 13. Tiêu chí nghiệm thu kiến trúc

- Thêm một `KnowledgePackage` mới không cần thêm một worker mới chỉ vì nguồn dữ
  liệu khác.
- Domain không sở hữu và không khai báo package; Plan Agent/runtime resolve
  package độc lập qua `KnowledgeRegistry`.
- Text và media có thể được truy xuất song song khi không có dependency.
- Media có thể được truy xuất sau khi Text Worker trả `entity_id`/`page_id`.
- Runtime chuyển result riêng lẻ cho Plan Agent và không tự chọn item cuối cùng.
- Plan Agent có thể đánh giá result, giao bổ sung hoặc kết thúc mà không đạt đủ
  dữ liệu.
- Worker không bịa passage/ảnh khi local và fallback đều không có nguồn.
- Mọi candidate đều truy được provenance; media truy được `object_key` hoặc
  `source_uri`.
- Query embedding và record embedding dùng model/version tương thích.
- Không có binary ảnh trong prompt hoặc trong Chroma document; prompt chỉ nhận
  metadata/candidate cần thiết.
- Luồng Surface hiện có chỉ nhận output cuối của Plan Agent; knowledge layer
  không tự sửa Compiler/Browser.

## 14. Các quyết định còn phải chốt trước khi viết code

1. API/model cụ thể của hai Gemini Flash worker và cách giữ context giữa các
   assignment.
2. Worker dùng native Google Search grounding hay một adapter/tool Google riêng
   khi local knowledge không đủ.
3. Model embedding text/media, dimension, normalization và chiến lược rerank.
4. `chunk_size`, `chunk_overlap`, top-k mặc định và bộ lọc theo language/package.
5. PostgreSQL chạy local bằng service/container hay dùng một adapter khác cho
   môi trường test.
6. Có cần image embedding thật ở phiên bản đầu hay chỉ embedding caption/alt/title.
7. Cơ chế dispatcher song song/tuần tự và timeout/cancel của worker.
8. Cách đưa worker result vào Plan Agent context mà không làm phình prompt quá
   mức; đây là quyết định tích hợp sau audit code, không tự suy đoán ở KF0.

## 15. Nhật ký tiến độ

| Ngày | Checkpoint | Kết quả |
|---|---|---|
| 2026-09-17 | KF0 | Tạo file tracking; ghi nhận `KnowledgeRegistry` cùng tầng registry dùng chung, storage Chroma/PostgreSQL/filesystem, ingestion/retrieval, hai Gemini Flash worker, fallback Google, dependency và trách nhiệm đánh giá của Plan Agent. Chưa sửa code. |
| 2026-09-17 | KF0-clarification | Sửa quan hệ domain/knowledge: `KnowledgePackage` độc lập hoàn toàn, không khai báo trong `DomainPackage`; package được resolve qua `KnowledgeRegistry` theo package id/capability. |
