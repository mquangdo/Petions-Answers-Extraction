# Hướng dẫn Khởi chạy Hệ thống Answer Matching Pipeline v2 (`run_pipeline.md`)

Tài liệu này hướng dẫn chi tiết cách khởi chạy toàn bộ dịch vụ, worker, và cách gửi request kiểm thử hệ thống Answer Matching v2 sau khi đã refactor cấu hình tập trung và logging.

---

## 1. Yêu cầu Môi trường & Dịch vụ Nền

Đảm bảo môi trường Python và các dịch vụ nền tảng đang hoạt động:

1. **Python Environment:** Kích hoạt môi trường Conda có đủ thư viện (`celery`, `sqlalchemy`, `psycopg2`, `fastapi`, `uvicorn`, `httpx`):
   ```bash
   conda activate surya
   ```
2. **PostgreSQL:** Chạy trên cổng `5432` (hoặc cấu hình qua biến `DATABASE_URL`).
3. **RabbitMQ:** Chạy trên cổng `5672` (hoặc cấu hình qua biến `RABBITMQ_URL`).
4. **OCR Service (Step 1):** Đang lắng nghe tại `http://127.0.0.1:8078/step1/ocr`.
5. **LLM Service (nếu dùng fallback):** vLLM/Ollama tại `http://127.0.0.1:8076/v1`.

---

## 2. Cấu hình Biến Môi trường (Tùy chọn)

Toàn bộ cấu hình được quản lý tập trung tại [`config.py`](file:///home/jovyan/scratch/quangdm/ea_api_with_metadata_v2/config.py). Bạn có thể override bằng biến môi trường trước khi chạy hoặc tạo file `.env`:

```bash
export DATABASE_URL="postgresql+psycopg2://postgres:mysecretpassword@localhost:5432/postgres"
export RABBITMQ_URL="amqp://admin:admin123@localhost:5672//"
export OCR_URL="http://127.0.0.1:8078/step1/ocr"
export LLM_BASE_URL="http://127.0.0.1:8076/v1"
export LLM_MODEL_NAME="google/gemma-4-26B-A4B-it"
export MAX_CONCURRENCY=3
```

---

## 3. Các bước Khởi chạy

Hệ thống hoạt động theo mô hình Producer - Consumer (FastAPI Server nhận việc $\leftrightarrow$ Celery Worker xử lý). Cần mở 2 terminal riêng biệt:

### Bước 1: Khởi động Celery Worker (Consumer)
Worker nhận nhiệm vụ OCR, trích xuất văn bản và đối soát câu trả lời.

```bash
# Cách 1: Chạy trực tiếp qua script worker.sh (chạy nền background):
bash worker.sh

# Cách 2: Chạy trực tiếp ở foreground để theo dõi:
celery -A tasks worker -Q answer_matching --pool=threads -c 4 --loglevel=info
```
> **Lưu ý:** Flag `--pool=threads -c 4` giúp worker giữ kết nối Heartbeat với RabbitMQ liên tục khi thực hiện các tác vụ I/O dài (như chờ OCR file lớn).

### Bước 2: Khởi động FastAPI Server (Producer)
API Server tiếp nhận request upload file, kiểm tra SHA-256 Cache, đẩy job vào queue và cung cấp API polling.

```bash
# Cách 1: Chạy qua run.sh
bash run.sh

# Cách 2: Chạy bằng lệnh uvicorn trực tiếp
uvicorn extract_api_server:app --host 127.0.0.1 --port 8005 --reload
```

---

## 4. Cách Gửi Request Kiểm thử (Usage & Testing)

### 4.1. Kiểm tra tình trạng server (Healthcheck)
```bash
curl -s http://127.0.0.1:8005/health
# Kết quả mong đợi: {"status":"ok"}
```

### 4.2. Gửi yêu cầu trích xuất & ghép câu trả lời (POST /api/v1/answer-matching)

```bash
curl -X POST "http://127.0.0.1:8005/api/v1/answer-matching" \
  -F 'data_list=[{"KN_KIENNGHI.ID":"KN_01","KN_KIENNGHI.NOI_DUNG":"Cử tri kiến nghị bình ổn giá vật tư nông nghiệp"}]' \
  -F 'file_ids=["FILE_001"]' \
  -F 'files=@downloaded/ha noi_25.pdf;type=application/pdf' \
  -F 'force_reprocess=false'
```

- **Nếu Cache Miss:** Trả về ngay `status: "PROCESSING"` kèm `request_id`:
  ```json
  {
    "request_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "status": "PROCESSING"
  }
  ```
- **Nếu Cache Hit:** Trả về ngay `status: "FINISHED"` kèm `result` mà không cần gọi lại OCR.

### 4.3. Polling kết quả (GET /api/v1/answer-matching/{request_id})

Dùng `request_id` nhận được ở trên để kiểm tra tiến độ (poll mỗi 2-3 giây):

```bash
curl -s "http://127.0.0.1:8005/api/v1/answer-matching/3fa85f64-5717-4562-b3fc-2c963f66afa6"
```

Khi xử lý hoàn tất, kết quả trả về có dạng:
```json
{
  "request_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "FINISHED",
  "result": {
    "data_list": [
      {
        "KN_KIENNGHI.ID": "KN_01",
        "answers": [
          {
            "content": "Về vấn đề này, Bộ đã ban hành thông tư...",
            "file_id": "FILE_001",
            "metadata": {
              "so_cong_van": "1234/BTC-QLG",
              "ngay_ban_hanh": "15/08/2024",
              "nguoi_ky": "Thứ trưởng..."
            }
          }
        ]
      }
    ],
    "metadata_all": {
      "matched_count": 1,
      "unmatched_count": 0
    }
  }
}
```

---

## 5. Theo dõi Log Hệ thống (`logs/`)

Hệ thống tự động phân tách và xoay vòng log trong thư mục `logs/`:

- **`tail -f logs/api.log`**: Xem log request đến API, sinh SHA-256 cache key, Cache HIT/MISS, lưu file volume, và enqueue Celery.
- **`tail -f logs/worker.log`**: Xem log Celery Worker nhận job, kiểm tra cache lớp 2, và kết quả xử lý.
- **`tail -f logs/pipeline.log`**: Xem chi tiết phân loại định dạng văn bản (`f1`/`f2`/`f3`/`llm`), tiến trình gọi OCR từng file, gọi song song LLM fallback, và điểm Jaccard.
- **`tail -f logs/db.log`**: Xem trạng thái kết nối PostgreSQL và lưu trữ Cache.

---

## 6. Lệnh Dừng & Dọn dẹp Worker khi cần

Nếu chạy worker qua `worker.sh` (chế độ daemon background), bạn có thể dừng worker bằng:

```bash
# Dừng worker qua PID file
kill $(cat worker.pid) && rm -f worker.pid

# Hoặc dừng bằng pkill nếu cần
pkill -f "celery -A tasks worker"
```
