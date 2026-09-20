# Insight: Luồng OCR bất đồng bộ — `use_celery=False` vs `use_celery=True`

## Thắc mắc của User

> Hiện tại tôi thấy hàm OCR trong `pipeline.py` đang là hàm bất đồng bộ.
> Điều gì xảy ra khi set `use_celery=False` khi hàm này được gọi một cách bất đồng bộ?
> Bên OCR service sẽ xử lý như nào?
> Câu hỏi tương tự với `use_celery=True`.

---

## I. Tổng quan kiến trúc 3 tầng

Hệ thống OCR thực tế là một chuỗi **3 tầng lồng nhau**:

```
┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 1: Pipeline Caller (ea_api_with_metadata_v2/pipeline.py)  │
│   _ocr_pdf_async()                                             │
│   HTTP POST multipart/form-data → use_celery: "false"          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 2: OCR Gateway (Complaint-EC/debug_app.py — Port 8078)    │
│   POST /step1/ocr                                              │
│   ├── use_celery=False → PDFExtractor.run_ocr()                │
│   │                      → asyncio.to_thread(_run_ocr_sync)    │
│   │                      → Kiểm tra MinIO Cache Tầng 1         │
│   │                      → Gọi HTTP đến StandaloneOCR :8085    │
│   │                                                            │
│   └── use_celery=True  → Celery task step_1_ocr_task           │
│                          Queue: uc1_worker1_queue               │
│                          → Worker chạy PDFExtractor.run_ocr()   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ TẦNG 3: Core OCR Engine (StandaloneOCR — Port 8085, Container) │
│   POST /v1/ocr/documents                                       │
│   ├── use_celery=False → In-process OCREngine.process_document  │
│   └── use_celery=True  → Celery task, RabbitMQ 8 queues        │
│                                                                 │
│   Các mô hình suy luận:                                        │
│   • Rasterizer (PyMuPDF đa luồng → PNG)                        │
│   • Text Line Detection (Surya DetectionPredictor, GPU/CPU)     │
│   • Layout Analysis (Surya Qwen vLLM tại Port 8043)            │
│   • Text Recognition (Surya RecognitionPredictor, Port 8043)    │
│   • Canonical Tree & Span Builder (in-memory)                   │
│   • Diacritics & Spelling (Gemma-4-26B qua LiteLLM Port 4000)  │
└─────────────────────────────────────────────────────────────────┘
```

---

## II. Khi `use_celery=False` — Chuyện gì xảy ra?

### A. Tại Pipeline Caller (`pipeline.py`)

Hàm `_ocr_pdf_async()` là một **coroutine** (`async def`) sử dụng `httpx.AsyncClient`
để gửi HTTP POST đến OCR Gateway port 8078:

```python
# pipeline.py dòng 66-78
response = await client.post(
    OCR_URL,  # http://127.0.0.1:8078/step1/ocr
    files={"file": (filename, pdf_bytes, "application/pdf")},
    data={"use_celery": "false", "log_dir": "string"},
)
```

- Đây là **I/O-bound async thuần túy** — `httpx` dùng `asyncio` socket.
- **Event loop của Pipeline KHÔNG bị block** — nó chỉ chờ response HTTP.
- Khi pipeline gọi song song nhiều file (qua `asyncio.gather`), tất cả request
  được gửi đi **đồng thời** nhờ `asyncio.Semaphore(MAX_CONCURRENCY)`.

### B. Tại OCR Gateway (`debug_app.py` — Port 8078)

```python
# debug_app.py dòng 436-437
extractor = get_pdf_extractor()  # PDFExtractor singleton
pdf_content, is_text_based = await extractor.run_ocr(pdf_bytes, ...)
```

Bên trong `PDFExtractor.run_ocr()` (`modules/pdf_extractor.py`):

```python
async def run_ocr(self, pdf_input, ...):
    return await asyncio.to_thread(self._run_ocr_sync, pdf_input, ...)
```

- `_run_ocr_sync` là hàm **đồng bộ** (blocking) — nó kiểm tra MinIO cache,
  nếu cache miss thì gọi HTTP đến StandaloneOCR port 8085.
- **Nhờ `asyncio.to_thread()`**, hàm blocking này được đẩy sang worker thread.
- ✅ **Event loop của Gateway KHÔNG bị block** — có thể nhận request tiếp.

### C. Tại StandaloneOCR (`Port 8085`) — ĐÂY LÀ ĐIỂM NGHẼN

```python
# StandaloneOCR/app/api/v1/ocr.py dòng 152-160
result = await engine.process_document(
    file_bytes=content, filename=filename, ...
)
```

`OCREngine.process_document()` được khai báo `async def` nhưng **~80% khối lượng
xử lý bên trong là mã đồng bộ CPU/GPU-bound nặng**:

| Bước | Tính chất | Chi tiết |
|------|-----------|----------|
| `rasterize_pdf()` | **Sync, CPU-bound** | `multiprocessing.Pool` + PyMuPDF, 4 worker processes |
| `safe_load_images()` | **Sync, CPU-bound** | Đọc ảnh PIL vào RAM |
| `detection_predictor()` | **Sync, GPU-bound** | PyTorch inference cục bộ (DetectionPredictor) |
| `layout_predictor()` | **Sync, I/O** | HTTP đồng bộ đến Surya vLLM :8043 |
| `recognition_predictor()` | **Sync, I/O** | HTTP đồng bộ đến Surya vLLM :8043 |
| `tree_builder.build_tree_and_content()` | **Sync, CPU-bound** | Thuật toán xây cây |
| `spelling_corrector.correct_chunks()` | **Async thực sự** | `await` gọi Gemma API |

**Vấn đề cốt lõi**: `process_document()` được gọi bằng `await` trực tiếp
mà **KHÔNG được bọc trong `asyncio.to_thread()`**. StandaloneOCR chạy Uvicorn
với **1 worker duy nhất**.

### ⚠️ Hệ quả khi `use_celery=False`

```
                    ┌─────────────────────────────────┐
                    │  Uvicorn Event Loop (1 thread)   │
                    │                                  │
  Request A ──────► │  await engine.process_document() │ ◄── BLOCKED 10-30s
                    │   ├── rasterize_pdf()  (SYNC)    │
                    │   ├── detection (SYNC GPU)       │
  Request B ──────► │   ├── layout (SYNC HTTP)         │ ◄── ĐANG CHỜ
                    │   ├── recognition (SYNC HTTP)    │
  Request C ──────► │   └── tree_builder (SYNC CPU)    │ ◄── ĐANG CHỜ
                    │                                  │
  GET /health ────► │  ❌ KHÔNG THỂ PHẢN HỒI           │ ◄── TIMEOUT
                    └─────────────────────────────────┘
```

1. **Event loop bị BLOCK hoàn toàn** trong suốt thời gian OCR (10-30 giây/tài liệu).
2. Uvicorn **không thể xử lý bất kỳ request nào khác**, kể cả health check.
3. Các request đến sau bị xếp hàng trong TCP socket queue.
4. Docker daemon đánh dấu container `unhealthy` do health check timeout.
5. **Rủi ro deadlock**: `multiprocessing.Pool` bên trong `rasterize_pdf()` khi
   chạy trong asyncio event loop có thể gây deadlock (đã xảy ra thực tế ngày 19/09).

> **Kết luận**: Mặc dù `pipeline.py` gọi OCR một cách async qua HTTP, và Gateway
> 8078 cũng async nhờ `asyncio.to_thread`, nhưng **tầng cuối cùng (StandaloneOCR 8085)
> lại block event loop**, biến toàn bộ chuỗi thành xử lý **tuần tự de facto**.

---

## III. Khi `use_celery=True` — Chuyện gì xảy ra?

Hệ thống có cơ chế Celery ở **cả 2 tầng server** (Gateway và StandaloneOCR).

### A. Celery tại Gateway (`debug_app.py`)

```python
# debug_app.py dòng 432-434
if use_celery:
    task = step_1_ocr_task.apply_async(
        args=[pdf_bytes, log_dir, process_table, keep_phu_luc],
        queue="uc1_worker1_queue",
        priority=1
    )
    pdf_content, is_text_based = await asyncio.to_thread(wait_for_celery_task, task)
```

**Luồng xử lý**:
1. Task được gửi vào RabbitMQ queue `uc1_worker1_queue`.
2. `wait_for_celery_task()` thăm dò kết quả trong vòng lặp:
   ```python
   def wait_for_celery_task(task_result, timeout=600.0, poll_interval=0.2):
       while not task_result.ready():
           if time.perf_counter() - start_time > timeout:
               raise TimeoutError(...)
           time.sleep(0.2)  # Blocking sleep, nhưng chạy trong to_thread
       return task_result.get(propagate=True)
   ```
3. Nhờ `asyncio.to_thread()`, event loop của Gateway **không bị block**.

**Cấu hình Worker**:
- 8 Celery workers: `uc1_worker1_queue` → `uc1_worker8_queue`
- Pool: `threads`, concurrency = 10
- Task được **hardcode** gửi vào `uc1_worker1_queue` → chỉ **1 worker** thực sự nhận task OCR

### B. Celery tại StandaloneOCR

Nếu StandaloneOCR cũng nhận `use_celery=True`:

```
Request → RoundRobinQueueRouter → RabbitMQ Exchange (standalone_ocr.docker.direct)
          ├── standalone_ocr.docker.queue_1
          ├── standalone_ocr.docker.queue_2
          ├── ...
          └── standalone_ocr.docker.queue_8
                    │
                    ▼
          Celery Worker (-P solo, concurrency=1)
          → OCREngine.process_document() (chạy trong worker process riêng)
```

**Đặc điểm quan trọng**:
- Worker pool: **`-P solo`** → chỉ 1 execution thread, **concurrency = 1**
- `worker_prefetch_multiplier = 1` → lấy 1 task/lần, không prefetch
- `task_acks_late = True` → acknowledge sau khi hoàn thành

### C. So sánh 2 chế độ

| Đặc tính | `use_celery=False` | `use_celery=True` |
|----------|--------------------|--------------------|
| **Nơi thực thi OCR** | Trong tiến trình Uvicorn | Trong Celery Worker riêng |
| **Event loop Uvicorn** | ❌ **BỊ BLOCK** (tại StandaloneOCR) | ✅ Không bị block |
| **Health check** | ❌ Bị treo theo | ✅ Phản hồi bình thường |
| **Xử lý song song** | Tuần tự (event loop đơ) | Xếp hàng trong RabbitMQ, tuần tự tại worker (`-P solo`) |
| **Rủi ro deadlock** | ⚠️ Cao (`mp.Pool` trong event loop) | ✅ Thấp (worker process riêng) |
| **Rủi ro treo server** | ⚠️ Rất cao | ✅ Worker treo không ảnh hưởng API |
| **Overhead** | Thấp (không qua message queue) | Cao hơn (RabbitMQ + Redis polling) |
| **Latency** | Thấp hơn ~200-500ms | Cao hơn (serialize + queue + poll) |

---

## IV. Tác động GPU / Memory

### GPU VRAM

| Thành phần | VRAM ước tính | Ghi chú |
|------------|---------------|---------|
| Surya Layout & Recognition (vLLM :8043) | ~32.8 GB (GPU 0) | Không phụ thuộc chế độ Celery |
| Gemma-4-26B Correction (LiteLLM :4000) | ~35-40 GB | Không phụ thuộc chế độ Celery |
| DetectionPredictor (PyTorch cục bộ) | ~1-2 GB | Nạp trong Uvicorn hoặc Worker tùy chế độ |

**Cảnh báo**: Nếu chạy đồng thời cả 2 chế độ hoặc nhiều worker, mỗi tiến trình
Python sẽ khởi tạo **bản sao riêng** của DetectionPredictor → nhân bội VRAM.

### RAM (Host Memory)

- `rasterize_pdf()` ở DPI=200: mỗi trang A4 ≈ **11.6 MB RAM** (ảnh RGB thô)
- Tài liệu 50 trang ≈ **600 MB - 1 GB RAM** chỉ riêng ảnh

| Chế độ | Hành vi RAM |
|--------|-------------|
| `use_celery=False` | Nhiều request đồng thời → RAM tăng vọt, nguy cơ OOM |
| `use_celery=True` | Worker `-P solo` xử lý 1 tài liệu/lần → RAM ổn định |

---

## V. Cơ chế MinIO Cache 2 tầng

```
[PDF bytes] → SHA-256 hash
     │
     ├──► Cache Tầng 1 (Complaint-EC Gateway)
     │       Bucket: 'ocr'
     │       Key: <sha256>.md
     │       Nội dung: Markdown đã render + hậu xử lý
     │       HIT → Trả ngay, KHÔNG gọi StandaloneOCR
     │
     └──► Cache Tầng 2 (StandaloneOCR)
             Bucket: 'standalone_ocr-docker'
             Key: <sha256>.json
             Nội dung: Canonical JSON gốc (lossless AST)
             HIT → Trả ngay Canonical JSON, KHÔNG chạy OCR inference
             MISS → Chạy OCR → TỰ ĐỘNG lưu kết quả vào cache
```

**Lưu ý quan trọng**: Complaint-EC gửi request đến StandaloneOCR với
`use_cache="false"` (vì đã tự kiểm tra cache tầng 1 rồi), nhưng StandaloneOCR
**vẫn tự động ghi kết quả mới vào cache tầng 2** (write-through).

---

## VI. Kết luận

### Luồng hiện tại (`use_celery=False` ở tất cả các tầng)

```
Pipeline → [async HTTP] → Gateway 8078 → [asyncio.to_thread] → StandaloneOCR 8085
                                                                       │
                                                              ❌ BLOCK EVENT LOOP
                                                              ❌ Tuần tự de facto
                                                              ❌ Rủi ro deadlock
```

- Pipeline gọi async → OK
- Gateway xử lý async nhờ `to_thread` → OK
- **StandaloneOCR block event loop** → bottleneck, chỉ xử lý 1 file/lần,
  health check timeout, nguy cơ container unhealthy

### Nếu đổi sang `use_celery=True`

```
Pipeline → [async HTTP] → Gateway 8078 → [Celery queue] → Worker → StandaloneOCR 8085
                                                                          │
                                                                 ✅ Worker riêng biệt
                                                                 ✅ Uvicorn không block
                                                                 ⚠️ Vẫn tuần tự (-P solo)
                                                                 ⚠️ Overhead queue + polling
```

- Ổn định hơn, ít rủi ro treo server
- Nhưng vẫn xử lý tuần tự do Celery worker chạy `-P solo`
- Thêm overhead: RabbitMQ routing + Redis polling mỗi 0.2-0.5s

### Khuyến nghị

Nếu muốn `use_celery=False` ổn định, cần sửa tại StandaloneOCR:
bọc `engine.process_document()` trong `asyncio.to_thread()` hoặc tăng `--workers`
cho Uvicorn. Hiện tại, chế độ `use_celery=True` **an toàn hơn** cho production
dù chậm hơn một chút do overhead message queue.

---

*Tài liệu này được tạo ngày 2026-09-20 dựa trên phân tích mã nguồn tại:*
- *`ea_api_with_metadata_v2/pipeline.py`*
- *`Complaint-EC/debug_app.py`, `modules/pdf_extractor.py`, `modules/pipeline.py`*
- *`Complaint-EC/tasks/pipeline_tasks.py`*
- *`StandaloneOCR/app/api/v1/ocr.py`, `app/services/`, `app/tasks/`*
