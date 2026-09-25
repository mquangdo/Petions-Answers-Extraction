# SƠ ĐỒ VÀ ĐẶC TẢ KỸ THUẬT TOÀN DIỆN HỆ THỐNG HIỆN TẠI (ANSWER MATCHING API V2)

- **Phiên bản hệ thống:** `2.3.0`
- **Thời gian cập nhật:** 25/09/2026
- **Nhánh Git triển khai:** `backup` (Kế thừa từ `fix/async-ocr-pipeline`)
- **Các file mã nguồn cốt lõi:**
  - API Layer: `extract_api_server.py`, `schemas.py`, `config.py`
  - Task & Queue: `tasks.py`, `worker.sh`, `env.sh`
  - Database & Cache: `db.py`
  - AI Pipeline: `pipeline.py`, `router.py`, `modules/`, `functions.py`, `regexes.py`, `llm_chunking.py`, `postprocess.py`

---

## 1. TỔNG QUAN KIẾN TRÚC TOÀN CẢNH (ARCHITECTURE OVERVIEW)

Hệ thống Answer Matching v2 vận hành theo mô hình **Bất đồng bộ hướng sự kiện (Event-Driven Asynchronous Architecture)** kết hợp cơ chế **Cache hai lớp (Double-check Cache)**, **Định tuyến trích xuất theo Bộ (Ministry Router)**, và **Hàng đợi tác vụ phân tán (Distributed Task Queue)**:

```mermaid
flowchart TB
    subgraph CLIENT_LAYER["1. PHÍA KHÁCH HÀNG (HTTT)"]
        Client["Hệ Thống Thông Tin Client<br/>• Gửi request multipart (data_list, files)<br/>• Polling định kỳ 2-3s"]
    end

    subgraph API_LAYER["2. TẦNG TIẾP NHẬN & ĐIỀU PHỐI (FastAPI 8005)"]
        API["FastAPI Web Server<br/>extract_api_server.py<br/>• Lifespan & Schema Validation<br/>• SHA-256 Cache Key Computing<br/>• Dual-mode Markdown Conversion"]
        Vol[("Shared File Volume<br/>FILE_STORE_DIR: uploaded_files/request_id/*.pdf")]
    end

    subgraph BROKER_DB["3. TẦNG DỮ LIỆU & TRUYỀN THÔNG ĐIỆP"]
        DB[("PostgreSQL Database (5432)<br/>• Bảng jobs (Quản lý vòng đời request)<br/>• Bảng result_cache (Cache kết quả theo Hash)")]
        RabbitMQ[("RabbitMQ Broker (10.0.11.184:5673)<br/>• Vhost: /shared<br/>• Queue: answer_matching (durable)")]
    end

    subgraph WORKER_LAYER["4. TẦNG THỰC THI TÁC VỤ (Celery Multi-threading Worker)"]
        WorkerMain["Celery Consumer Main Thread<br/>• Duy trì kết nối TCP & AMQP Heartbeat 30s<br/>• worker_prefetch_multiplier = 1<br/>• task_acks_late = True"]
        InternalQ["Internal Work Queue (In-memory FIFO)"]
        ThreadPool["Worker ThreadPool (concurrency = 4)<br/>• Thread 1 • Thread 2 • Thread 3 • Thread 4<br/>• Work-stealing: Thread rảnh bốc job ngay"]
        AsyncPipe["run_pipeline_async (pipeline.py)<br/>• File Reader tuần tự an toàn<br/>• Semaphore OCR = 4<br/>• Semaphore LLM = 1<br/>• Jaccard Semantic Matcher"]
    end

    subgraph AI_SERVICES["5. DỊCH VỤ TRÍ TUỆ NHÂN TẠO & OCR"]
        subgraph OCR_SYS["Standalone OCR Service (Port 8085)"]
            OCR_DOC["1. POST /v1/ocr/documents<br/>• use_celery = true, use_cache = true<br/>• Trả về Canonical Tree JSON v1.0.0"]
            OCR_RENDER["2. POST /v1/ocr/render<br/>• Dựng cấu trúc Markdown (#, **)<br/>• Fallback text thuần nếu render lỗi"]
        end
        subgraph LLM_SYS["LLM Gateway / vLLM (Port 4000)"]
            vLLM["Model Serving (127.0.0.1:4000/v1)<br/>• Model: google/gemma-4-26B-A4B-it<br/>• Max Tokens: 32768 | Temp: 0.0<br/>• Fallback 2-Pass Semantic Chunking"]
        end
    end

    subgraph ROUTER_LAYER["6. BỘ ĐỊNH TUYẾN THEO BỘ (Ministry Router)"]
        Router["router.py (Ministry Router)<br/>• Nhận diện tên Bộ/Ngành tự động<br/>• Tra bảng và nạp module chuyên biệt<br/>• Trích xuất ID Đoàn ĐBQH (donvi_id)"]
        Modules["modules/<mã_bộ>.py<br/>• Regex & mẫu đặc thù từng Bộ<br/>• Fallback root extractor nếu chưa có module"]
    end

    Client -->|"1. POST /api/v1/answer-matching<br/>data_list, file_ids, files, force_reprocess"| API
    API -->|"2a. Kiểm tra Cache ban đầu"| DB
    API -->|"2b. Cache Miss: Lưu file binary"| Vol
    API -->|"2c. INSERT Job (status=processing)"| DB
    API -->|"2d. apply_async(task_id=request_id)"| RabbitMQ
    API -->|"3. Trả về ngay HTTP 200 (request_id, status)"| Client

    Client -.->|"4. Polling GET /answer-matching/:id"| API
    API -.->|"Truy vấn trạng thái & kết quả"| DB

    RabbitMQ -->|"Kéo task về qua AMQP"| WorkerMain
    WorkerMain -->|"Đẩy task vào bộ đệm RAM"| InternalQ
    InternalQ -->|"Bốc task theo slot rảnh"| ThreadPool
    ThreadPool --> AsyncPipe
    AsyncPipe -->|"Đọc file PDF bytes"| Vol
    AsyncPipe -->|"Giai đoạn 1: Lấy Canonical JSON (Timeout 600s)"| OCR_DOC
    OCR_DOC -->|"Giai đoạn 2: Render Markdown (Timeout 60s)"| OCR_RENDER
    OCR_RENDER --> Router
    Router --> Modules
    Modules -.->|"Fallback trích xuất khi văn bản dị biệt"| vLLM
    ThreadPool -->|"Ghi cache kết quả (put_cached_result)"| DB
    ThreadPool -->|"Cập nhật Job hoàn tất (set_succeeded/set_failed)"| DB
```

### Bảng chi tiết cấu hình và cổng giao tiếp kỹ thuật:

| Thành phần | File mã nguồn | Port / Giao thức | Cấu hình & Trách nhiệm kỹ thuật cốt lõi |
| :--- | :--- | :--- | :--- |
| **FastAPI Server** | `extract_api_server.py` | `8005` (HTTP) | • Tiền xử lý: BOM removal, Smart quotes sanitization, Markdown strip.<br/>• Validate kích thước file (`MAX_FILE_SIZE_MB = 50MB`), đuôi `.pdf`.<br/>• Sinh `cache_key = SHA256(file_ids + file_bytes_hashes + data_list)`.<br/>• Quản lý lưu trữ file trên volume dùng chung `./uploaded_files/{request_id}/`. |
| **PostgreSQL** | `db.py` | `5432` (TCP) | • `jobs`: Quản lý trạng thái xử lý (`processing`, `succeeded`, `failed`).<br/>• `result_cache`: Lưu trữ kết quả JSON theo `cache_key`.<br/>• Connection pool: SQLAlchemy `create_engine` với `SessionLocal`. |
| **RabbitMQ** | Cấu hình trong `env.sh` | `10.0.11.184:5673` (AMQP)<br/>Vhost: `/shared` | • Queue bền vững (`durable`): `answer_matching`.<br/>• Điều phối tác vụ từ API sang Worker, chống nghẽn bộ nhớ. |
| **Celery Worker** | `tasks.py`, `worker.sh` | Chạy tiến trình nền | • Lệnh: `celery -A tasks worker -Q answer_matching --pool=threads -c 4 --loglevel=info --logfile=worker.log --pidfile=worker.pid --detach`.<br/>• `worker_prefetch_multiplier = 1`: Tránh kéo dồn task nặng.<br/>• `task_acks_late = True`: Task chỉ được ACK khi hoàn tất.<br/>• Tách biệt luồng Consumer (giữ Heartbeat) và 4 luồng thực thi. |
| **AI Pipeline** | `pipeline.py`, `functions.py` | In-process Python | • Điều phối bất đồng bộ `run_pipeline_async`.<br/>• `asyncio.Semaphore(4)`: Cho phép OCR đồng thời tối đa 4 file PDF.<br/>• `asyncio.Semaphore(1)`: Giới hạn tối đa 1 lượt suy luận LLM để bảo vệ GPU. |
| **Ministry Router** | `router.py`, `modules/` | In-process Python | • Tự động nhận diện tên Bộ trong văn bản.<br/>• Nạp preload 13+ module chuyên biệt theo Bộ.<br/>• Trích xuất ID Đoàn ĐBQH (`donvi_id`) phục vụ đối soát địa phương. |
| **Standalone OCR** | Container độc lập | `8085` (HTTP) | • URL Documents: `http://localhost:8085/v1/ocr/documents` (`timeout=600s`).<br/>• URL Render: `http://localhost:8085/v1/ocr/render` (`timeout=60s`).<br/>• Canonical Tree JSON v1.0.0, tự động inpaint xóa dấu đỏ và bóc tách chữ ký. |
| **LLM Gateway** | LiteLLM / vLLM Gateway | `127.0.0.1:4000` (HTTP) | • URL: `http://127.0.0.1:4000/v1` (OpenAI compatible).<br/>• Model: `google/gemma-4-26B-A4B-it`, `max_tokens=32768`.<br/>• 2-Pass Semantic Chunking phân đoạn kiến nghị và câu trả lời. |

---

## 2. SƠ ĐỒ TUẦN TỰ HOẠT ĐỘNG (SEQUENCE DIAGRAMS)

### 2.1. Luồng chuẩn thành công (Bao gồm Cache Hit, Asynchronous OCR & Ministry Route)

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client (HTTT)
    participant API as FastAPI (8005)
    participant Disk as Shared Disk
    participant DB as PostgreSQL (5432)
    participant MQ as RabbitMQ (5673)
    participant Worker as Celery Worker (Pool Threads -c 4)
    participant Pipe as Pipeline Engine
    participant OCR as Standalone OCR (8085)
    participant Router as Ministry Router
    participant LLM as LLM Gateway (4000)

    Client->>API: POST /api/v1/answer-matching (data_list, file_ids, files, force_reprocess)
    API->>API: 1. Clean JSON, Validate Schema
    API->>API: 2. Đọc binary PDF, tính SHA-256 từng file & cache_key tổng hợp

    alt Cache Hit (force_reprocess=false & cache_key tồn tại trong DB)
        API->>DB: get_cached_result(cache_key)
        DB-->>API: Trả về kết quả JSON có sẵn
        API->>API: _convert_result_format(cached, plain_text=True)
        API-->>Client: Trả ngay HTTP 200 (status: FINISHED, kèm result)
    else Cache Miss (Dữ liệu mới hoặc force_reprocess=true)
        API->>Disk: _save_files -> uploaded_files/request_id/i.pdf
        API->>DB: db_create_job(request_id, input_data, status=processing)
        API->>MQ: process_answer_matching.apply_async
        API-->>Client: Trả về HTTP 200 (request_id, status: PROCESSING)
        
        par Client Polling định kỳ
            loop Mỗi 2s - 3s
                Client->>API: GET /api/v1/answer-matching/:id?plain_text=true
                API->>DB: get_job(request_id)
                DB-->>API: status: processing
                API-->>Client: HTTP 200 (status: PROCESSING)
            end
        and Celery Worker xử lý ngầm (Đa luồng)
            MQ->>Worker: Consume message qua luồng chính
            Worker->>Worker: Giao task cho 1 Worker Thread đang rảnh
            Worker->>DB: get_cached_result(cache_key) (Double-check race condition)
            
            Worker->>Pipe: run_pipeline -> run_pipeline_async
            Pipe->>Disk: Đọc tuần tự bytes toàn bộ file PDF
            
            par OCR & Extract đồng thời (OCR Semaphore = 4)
                Pipe->>OCR: 1. POST /v1/ocr/documents (bytes, use_celery=true, use_cache=true)
                OCR-->>Pipe: Trả về Canonical Tree JSON v1.0.0
                Pipe->>OCR: 2. POST /v1/ocr/render (canonical JSON, format=markdown)
                OCR-->>Pipe: Trả về Markdown có cấu trúc (#, **)
                Note over Pipe: Nếu render lỗi -> fallback sang canonical['content']
                
                Pipe->>Pipe: _fix_ocr_diacritics & clean_footer
                Pipe->>Router: route_extract(md_text, llm_semaphore)
                Router->>Router: Nhận diện tên Bộ & extract_donvi_id (Đoàn ĐBQH)
                
                alt Bộ có module chuyên biệt trong modules/
                    Router->>Router: Gọi hàm extract của module Bộ tương ứng
                else Bộ chưa có module hoặc format bất quy tắc
                    Router->>LLM: Lần 1: Chat Completion trích xuất mảng ID Kiến nghị
                    LLM-->>Router: JSON array of unit IDs
                    Router->>LLM: Lần 2: Chat Completion trích xuất mảng ID Câu trả lời
                    LLM-->>Router: JSON array of unit IDs
                    Router->>Router: validate_and_resolve & cân bằng KN == TL
                end
                Router-->>Pipe: Trả về petitions, metadata, donvi_id
            end
            
            Pipe->>Pipe: Gom toàn bộ pairs (noi_dung, tra_loi, file_id, metadata)
            Pipe->>Pipe: Duyệt từng kiến nghị cử tri -> Tính Jaccard token similarity
            Pipe->>Pipe: Lọc kết quả tốt nhất (Score >= 0.5) & Đóng gói data_list, metadata_all
            Pipe-->>Worker: Trả về kết quả hoàn chỉnh
            
            Worker->>DB: put_cached_result(cache_key, data_list, result)
            Worker->>DB: set_succeeded(request_id, result)
            Worker->>MQ: Gửi basic_ack xác nhận hoàn tất task
        end

        Client->>API: GET /api/v1/answer-matching/:id?plain_text=true
        API->>DB: get_job(request_id)
        DB-->>API: status: succeeded, result JSON
        API->>API: _convert_result_format(plain_text=True)
        API-->>Client: Trả về HTTP 200 (status: FINISHED, kèm result JSON)
    end
```

---

### 2.2. Sơ đồ xử lý ngoại lệ và cơ chế bảo vệ (Failure & Resilience Recovery)

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client (HTTT)
    participant API as FastAPI (8005)
    participant DB as PostgreSQL (5432)
    participant MQ as RabbitMQ (5673)
    participant Worker as Celery Worker
    participant Pipe as Pipeline Engine
    participant OCR as Standalone OCR (8085)

    Note over Client,API: TÌNH HUỐNG 1: Lỗi dữ liệu đầu vào (Validation Error)
    Client->>API: POST /api/v1/answer-matching (File > 50MB hoặc không phải PDF)
    API-->>Client: Trả ngay HTTP 413 (Payload Too Large) hoặc HTTP 415 (Unsupported Media)
    Client->>API: POST /api/v1/answer-matching (len file_ids != len files)
    API-->>Client: Trả ngay HTTP 400 Bad Request (Dừng ngay trước khi lưu disk)

    Note over Client,MQ: TÌNH HUỐNG 2: Broker gián đoạn kết nối (OperationalError)
    Client->>API: POST /api/v1/answer-matching (Payload hợp lệ)
    API->>DB: Ghi Job (status=processing)
    API->>MQ: apply_async thất bại do broker mất kết nối
    API->>DB: set_failed(request_id, error=Không kết nối được RabbitMQ)
    API-->>Client: Trả ngay HTTP 503 Service Unavailable (Kèm thông báo lỗi chi tiết)

    Note over Worker,OCR: TÌNH HUỐNG 3: Lỗi nghiệp vụ OCR (Fail-Fast 4xx vs Retryable 5xx)
    Worker->>Pipe: run_pipeline_async
    Pipe->>OCR: POST /v1/ocr/documents (File hỏng mã hóa)
    OCR-->>Pipe: HTTP 400 Bad Request
    Note over Pipe: Nhận mã 4xx -> Ném NonRetryableOCRError ngay lập tức<br/>KHÔNG retry, giải phóng worker ngay!
    Pipe-->>Worker: Raise NonRetryableOCRError
    Worker->>DB: set_failed(request_id, error=HTTP 400: Non-retryable error)

    Note over Worker,OCR: TÌNH HUỐNG 4: OCR Quá tải / Timeout tạm thời (504, 502, 500)
    Pipe->>OCR: POST /v1/ocr/documents (Server OCR bận tính toán)
    OCR-->>Pipe: HTTP 504 Gateway Timeout
    Note over Pipe: Nhận mã trong RETRYABLE_CODES -> Thử lại tối đa 5 lần<br/>Delay: base * 2^(attempt-1) + jitter
    Pipe->>OCR: Thử lại lần 2 sau delay...

    Note over Client,API: TÌNH HUỐNG 5: Phản hồi lỗi qua Polling
    Client->>API: GET /api/v1/answer-matching/:id
    API->>DB: get_job(request_id)
    DB-->>API: status: failed, error: Chi tiết nguyên nhân
    API-->>Client: HTTP 200 (status: FAILED, kèm error chi tiết)
```

---

## 3. SƠ ĐỒ & ĐẶC TẢ CHI TIẾT AI PIPELINE (`pipeline.py`, `router.py`, `modules/`)

### 3.1. Sơ đồ luồng xử lý nội bộ Pipeline

```mermaid
flowchart TD
    Start(["Bắt đầu run_pipeline_async"]) --> ReadDisk["Đọc tuần tự bytes PDF từ Volume dùng chung"]
    ReadDisk --> CheckDisk{"Đọc file thành công?"}
    CheckDisk -->|Có file lỗi| RaiseDiskErr["Ném RuntimeError: 1 file lỗi -> Hủy toàn bộ Job"]
    
    CheckDisk -->|Tất cả thành công| InitSem["Khởi tạo Semaphores:<br/>• OCR Semaphore = 4<br/>• LLM Semaphore = 1"]
    InitSem --> PrepareTasks["Tạo mảng Task _ocr_extract_one cho từng file"]

    subgraph SUB_FILE["Quy trình xử lý từng file (_ocr_extract_one)"]
        AcquireSem["Lấy OCR Semaphore Slot (Max 4)"] --> CallOCRDoc["Gọi HTTP POST /v1/ocr/documents<br/>timeout = 600s"]
        
        CallOCRDoc --> CheckHTTP{"HTTP Status Code?"}
        CheckHTTP -->|200 OK| CallRender["Gọi HTTP POST /v1/ocr/render<br/>timeout = 60s"]
        CheckHTTP -->|4xx Lỗi nghiệp vụ| FailFast["Ném NonRetryableOCRError<br/>Dừng ngay, không retry"]
        CheckHTTP -->|429 / 5xx / ReadTimeout| CheckRetry{"Số lần thử <= 5?"}
        CheckRetry -->|Còn lượt| CalcBackoff["Chờ: base * 2^attempt + jitter"]
        CalcBackoff --> CallOCRDoc
        CheckRetry -->|Hết lượt| RaiseOCRErr["Ném RuntimeError: Quá số lần thử lại"]

        CallRender --> RenderCheck{"Render thành công?"}
        RenderCheck -->|Thành công| UseRender["Sử dụng Markdown có cấu trúc (#, **)"]
        RenderCheck -->|Thất bại| FallbackText["Fallback dùng canonical['content'] thuần"]
        
        UseRender --> Clean["Làm sạch văn bản:<br/>• _fix_ocr_diacritics: Sửa lỗi dấu tiếng Việt<br/>• clean_footer: Loại bỏ footer, số trang lặp lại"]
        FallbackText --> Clean

        Clean --> DonVi["extract_donvi_id_from_text:<br/>Trích xuất ID Đoàn ĐBQH từ văn bản"]
        Clean --> MinistryRouter{"ENABLE_MINISTRY_ROUTER?"}

        MinistryRouter -->|True| RouteMinistry["router.route_extract:<br/>1. Quét tên Bộ từ danh sách 25+ cơ quan<br/>2. Tra bảng nạp module chuyên biệt"]
        MinistryRouter -->|False| RootExtract["Chạy bộ trích xuất Root mặc định"]

        RouteMinistry --> CheckMod{"Tìm thấy module Bộ?"}
        CheckMod -->|Có module| RunMod["Thực thi regex theo quy chuẩn riêng của Bộ"]
        CheckMod -->|Không có / Format dị biệt| FallbackLLM["Fallback: llm_chunking.extract_from_md<br/>(Kiểm soát bởi LLM Semaphore = 1)"]

        subgraph SUB_LLM["Chi tiết luồng LLM Fallback (vLLM / LiteLLM Port 4000)"]
            Segment["Tách Atomic Units: segment_units_simple"] --> CallLLM1["Prompt 1: Phân đoạn Kiến nghị -> Array ID"]
            CallLLM1 --> CallLLM2["Prompt 2: Phân đoạn Trả lời -> Array ID"]
            CallLLM2 --> ValidateLLM["validate_and_resolve: Kiểm tra ID, tính toàn vẹn"]
            ValidateLLM --> BalanceCheck{"Số KN bằng Số TL?"}
            BalanceCheck -->|Bằng nhau| BuildPairs["Tạo cặp nội dung và câu trả lời"]
            BalanceCheck -->|Lệch| RetryHint{"Còn lượt thử <= 3?"}
            RetryHint -->|Còn| AddHint["Thêm gợi ý sửa lỗi vào prompt"]
            AddHint --> CallLLM1
            RetryHint -->|Hết| MergeExtra["Gộp nhóm thừa liên tục: _merge_extra_groups"]
            MergeExtra --> BuildPairs
        end
        
        RunMod --> ReturnFileResult["Trả về petitions, metadata, donvi_id, ministry"]
        BuildPairs --> ReturnFileResult
        RootExtract --> ReturnFileResult
    end

    PrepareTasks --> SUB_FILE
    ReturnFileResult --> Gather["asyncio.gather(*tasks, return_exceptions=True)"]
    
    Gather --> CheckAllFiles{"Có file nào bị Exception?"}
    CheckAllFiles -->|Có lỗi| RaiseTotalJob["Ném RuntimeError: Hủy toàn bộ Job, đánh dấu failed"]
    CheckAllFiles -->|Tất cả thành công| BuildPairsList["Gom toàn bộ các cặp trích xuất: pairs list"]

    subgraph SUB_MATCHING["Jaccard Semantic Matching (Token-based)"]
        BuildPairsList --> LoopKN["Duyệt từng kiến nghị cử tri trong data_list"]
        LoopKN --> Normalize["Chuẩn hóa text: Lowercase, NFKD tách dấu, regex token"]
        Normalize --> CalcJaccard["Tính Score: giao tập token / hợp tập token"]
        CalcJaccard --> FindBest["Lấy cặp có Score cao nhất với kiến nghị"]
        FindBest --> CheckScore{"Score >= 0.5?"}
        CheckScore -->|Thỏa mãn| MatchSuccess["answers = item câu trả lời<br/>matched_count += 1"]
        CheckScore -->|Dưới ngưỡng| MatchFail["answers = rỗng []<br/>unmatched_count += 1"]
    end

    MatchSuccess --> Aggregate["Tổng hợp kết quả cuối cùng: data_list + metadata_all"]
    MatchFail --> Aggregate
    Aggregate --> Finish(["Hoàn tất Pipeline trả về kết quả"])
```

---

### 3.2. Cơ chế định tuyến theo Bộ (`router.py`) & Mô-đun hóa

Hệ thống bổ sung kiến trúc **Ministry Router** độc lập (`ENABLE_MINISTRY_ROUTER = True`), giúp tăng độ chính xác trích xuất regex theo đặc thù văn bản của từng cơ quan Nhà nước:

1. **Preload Module an toàn:**
   - Quét và nạp trước toàn bộ các module trong thư mục `modules/` lúc khởi động server (tránh hiện tượng race condition khi các worker thread import đồng thời).
2. **Nhận diện cơ quan ban hành (`detect_ministry`):**
   - Quét phần đầu văn bản Markdown tìm tên cơ quan thuộc danh sách chuẩn hơn 25 Bộ, Ban, Ngành (Bộ Tài nguyên & Môi trường, Bộ Nội vụ, Bộ Công an, Bộ Quốc phòng, Bộ GTVT, Bộ GD&ĐT...).
3. **Tra cứu và điều phối (`MINISTRY_TO_MODULE`):**
   - Ánh xạ tên Bộ sang module tương ứng (`btnmt.py`, `bnv.py`, `bca.py`, `bgtvt.py`, v.v.).
   - Nếu phát hiện cấu trúc đặc thù theo từng Bộ, module sẽ áp dụng bộ regex tối ưu riêng.
   - Nếu văn bản có cấu trúc không theo quy chuẩn, tự động chuyển sang cơ chế **LLM Fallback**.
4. **Trích xuất định danh địa phương (`extract_donvi_id_from_text`):**
   - Tự động nhận dạng tên tỉnh/thành phố của Đoàn Đại biểu Quốc hội trong văn bản và ánh xạ ra mã định danh `donvi_id`, hỗ trợ công tác quản lý và thống kê theo địa bàn.

---

### 3.3. Giải thuật Jaccard Semantic Matching

Sau khi trích xuất toàn bộ các cặp câu trả lời từ các file PDF, hệ thống tiến hành đối soát ngữ nghĩa giữa từng kiến nghị trong `data_list` với các câu trả lời:

1. **Chuẩn hóa chuỗi (`_normalize`):**
   ```python
   def _normalize(text: str) -> set[str]:
       text = (text or "").lower()
       # Khử dấu tiếng Việt qua NFKD
       text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
       # Lấy danh sách token chữ và số
       return set(re.findall(r"[a-z0-9]+", text))
   ```
2. **Hệ số tương đồng Jaccard (`_jaccard`):**
   $$\text{Jaccard}(A, B) = \frac{|Tokens(A) \cap Tokens(B)|}{|Tokens(A) \cup Tokens(B)|}$$
3. **Ngưỡng quyết định (`JACCARD_THRESHOLD = 0.5`):**
   - Với mỗi kiến nghị cử tri $K$, tìm câu trả lời $P^*$ sao cho:
     $$P^* = \arg\max_{P \in \text{Pairs}} \text{Jaccard}(K_{\text{nội dung}}, P_{\text{nội dung}})$$
   - Nếu $\text{Score}(P^*) \ge 0.5$: Gán câu trả lời vào kết quả và tăng `matched_count`.
   - Nếu $\text{Score}(P^*) < 0.5$: Gán mảng `answers: []` và tăng `unmatched_count`.

---

## 4. CHI TIẾT IMPLEMENT KỸ THUẬT & CẤU TRÚC DỮ LIỆU

### 4.1. Cấu hình Tham số Môi trường Hiện tại (`config.py`, `env.sh`)

| Biến môi trường | Giá trị mặc định | Giải thích chức năng |
| :--- | :--- | :--- |
| `RABBITMQ_URL` | `amqp://platform_developer:Project2026%21@10.0.11.184:5673/%2Fshared` | Chuỗi kết nối tới RabbitMQ Broker tập trung. |
| `OCR_URL` | `http://localhost:8085/v1/ocr/documents` | Endpoint nhận diện toàn trang, trả về Canonical Tree JSON. |
| `OCR_RENDER_URL` | `http://localhost:8085/v1/ocr/render` | Endpoint tái cấu trúc Canonical JSON thành Markdown chuẩn. |
| `OCR_TIMEOUT` | `600.0` (giây) | Thời gian chờ tối đa cho bước nhận diện tài liệu OCR (10 phút). |
| `OCR_RENDER_TIMEOUT`| `60.0` (giây) | Thời gian chờ tối đa cho bước render Markdown (1 phút). |
| `MAX_CONCURRENCY` | `4` | Số lượng file PDF được gọi OCR song song tối đa trong 1 job. |
| `LLM_BASE_URL` | `http://127.0.0.1:4000/v1` | Cổng Gateway LiteLLM phục vụ mô hình ngôn ngữ lớn. |
| `LLM_MODEL_NAME` | `google/gemma-4-26B-A4B-it` | Mô hình ngôn ngữ lớn dùng cho Fallback Semantic Chunking. |
| `LLM_TIMEOUT` | `300.0` (giây) | Thời gian chờ tối đa cho một request suy luận LLM (5 phút). |
| `LLM_CONCURRENCY` | `1` | Semaphore kiểm soát luồng gọi LLM, bảo vệ bộ nhớ VRAM GPU. |
| `ENABLE_MINISTRY_ROUTER`| `True` | Bật tính năng định tuyến trích xuất chuyên biệt theo Bộ. |

---

### 4.2. Cơ chế Đa luồng Celery Worker (`--pool=threads -c 4`)

Cơ chế phân phối và duy trì nhịp tim kết nối của Worker:

```
[ RabbitMQ Broker (Port 5673) ]
        │
        │ (1) Duy trì kết nối TCP & AMQP Heartbeat 30s/lần
        ▼
┌────────────────────────────────────────────────────────┐
│  Celery Worker Process                                 │
│                                                        │
│  [ Luồng chính (Consumer Event Loop) ]                 │
│         │                                              │
│         │ (2) Đẩy task nhận được vào RAM                │
│         ▼                                              │
│  ┌────────────────────────────────────────┐            │
│  │   Hàng đợi bộ nhớ đệm (Internal Queue) │            │
│  │   [ Task D ]  [ Task C ]  [ Task B ]   │            │
│  └───────────────────┬────────────────────┘            │
│                      │                                 │
│                      │ (3) Work-stealing: Thread nào rảnh bốc việc ngay
│       ┌──────────────┼──────────────┬─────────────┐    │
│       ▼              ▼              ▼             ▼    │
│  [ Thread 1 ]   [ Thread 2 ]   [ Thread 3 ]  [ Thread 4]│
│  (Đang chạy)    (Đang chạy)       (RẢNH)     (Đang chạy)│
│                                     │                  │
│                                     ▼                  │
│                                Nhận Task B!            │
└────────────────────────────────────────────────────────┘
```

- **Chống đứt kết nối (Missed Heartbeat):** Luồng chính (Main Thread) chạy vòng lặp sự kiện mạng độc lập, liên tục gửi bản tin Heartbeat định kỳ 30 giây lên RabbitMQ, giải quyết dứt điểm lỗi `Socket was disconnected` thường gặp ở chế độ `-P solo`.
- **Cạnh tranh công bằng (Work-stealing):** 4 worker thread tự do lấy task từ hàng đợi RAM ngay khi vừa hoàn thành tác vụ trước đó, tối ưu hóa công suất xử lý I/O.
- **Kiểm soát tải (`prefetch_multiplier = 1`):** Không cho phép kéo ồ ạt task nặng về chiếm dụng bộ nhớ RAM.

---

## 5. TỔNG KẾT CÁC NÂNG CẤP VÀ ĐIỂM BẢO VỆ CỐT LÕI

1. **Chuẩn hóa Standalone OCR 2 giai đoạn:** Tách biệt rõ ràng giai đoạn trích xuất cây cấu trúc (`/v1/ocr/documents` - Canonical Tree JSON v1.0.0) và giai đoạn hiển thị (`/v1/ocr/render` - Markdown có cấu trúc). Tự động fallback về text thuần nếu render gặp sự cố, đảm bảo pipeline không bao giờ bị gián đoạn.
2. **Hệ thống định tuyến theo Bộ (Ministry Router):** Nâng cao tỷ lệ bóc tách chính xác bằng cách nhận diện tự động và áp dụng bộ regex riêng biệt cho từng cơ quan ban hành, đồng thời bóc tách thành công mã định danh Đoàn ĐBQH (`donvi_id`).
3. **Cơ chế Điều tiết Tải Song song (Dual Semaphore Control):**
   - `MAX_CONCURRENCY = 4`: Cho phép mở rộng song song 4 luồng OCR để tăng tốc độ xử lý tài liệu.
   - `LLM_CONCURRENCY = 1`: Hãm tải nghiêm ngặt các lượt gọi LLM Fallback, bảo vệ GPU tránh tình trạng tranh chấp và tràn bộ nhớ VRAM.
4. **Kiến trúc Celery Worker Đa luồng Bền vững:** Sử dụng `--pool=threads -c 4` tách biệt hoàn toàn việc truyền thông mạng AMQP và xử lý tính toán, đảm bảo kết nối với RabbitMQ luôn thông suốt, loại bỏ hoàn toàn hiện tượng task bị redeliver lặp đi lặp lại.
5. **Cơ chế Fail-Fast & Double-check Cache:** Cô lập ngay lập tức các file lỗi cấu trúc 4xx (`NonRetryableOCRError`), ngăn chặn retry lãng phí, đồng thời bảo vệ hệ thống khỏi các request trùng lặp nhờ cơ chế kiểm tra cache hai lớp.
