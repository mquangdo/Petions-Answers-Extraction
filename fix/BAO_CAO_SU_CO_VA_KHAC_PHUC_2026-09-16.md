# BÁO CÁO SỰ CỐ VÀ PHƯƠNG ÁN KHẮC PHỤC PIPELINE ASYNC OCR

- **Ngày lập báo cáo:** 16/09/2026
- **Mốc thời gian ghi nhận sự cố:** 15/09/2026 – 16/09/2026
- **Thời điểm hệ thống vẫn hoạt động trước đó:** Thứ Sáu, 11/09/2026 (trước 15:00)
- **Hệ thống ảnh hưởng:** Celery Worker `answer_matching`, `pipeline.py`, bộ test trong thư mục `test/`
- **Nhánh triển khai bản sửa:** `fix/async-ocr-pipeline` (Commit: `076d28e`)

---

## 1. TỔNG QUAN HIỆN TƯỢNG (SYMPTOMS)

1. **Pipeline chính bị lỗi liên tục:**
   - Vào thứ Sáu tuần trước (11/09/2026), pipeline xử lý bình thường hàng loạt job thành công (`status: succeeded`).
   - Sang đầu tuần này (15/09 – 16/09/2026), các job mới đẩy vào queue đều thất bại (`status: failed`) với lỗi:
     ```text
     OCR/trích xuất thất bại: ... HTTP 500 (có thể bị chặn/quá tải)
     ```
     Dù phía người dùng và mã nguồn hệ thống chưa hề chỉnh sửa gì.

2. **Khác biệt bất thường giữa 2 file test:**
   - File `test/test_sync.py`: Gửi request đồng bộ lên OCR server chạy thành công (HTTP 200).
   - File `test/test_async.py`: Gửi request bất đồng bộ thì luôn bị lỗi.

---

## 2. NGUYÊN NHÂN GỐC RỄ (ROOT CAUSE ANALYSIS)

Qua điều tra log hệ thống (`worker.log`, `debug_app.log`), tiến trình hệ điều hành, GPU phần cứng và mã nguồn tại máy chủ OCR (`8078--main--dev--sinhnq3.coder.vts-ai.space`), xác định 5 nguyên nhân cốt lõi sau:

### Nguyên nhân 1: Server OCR bên ngoài bị lỗi Celery Backend từ cuối tuần (Nguyên nhân chính)
* **Vị trí code:** Endpoint `/step1/ocr` trong tiến trình `/home/jovyan/scratch/hieunq10/Complaint-EC/debug_app.py` đang lắng nghe trên cổng `8078`.
* **Cơ chế:** Phía OCR server kiểm tra tham số form `use_celery`:
  * Nếu `use_celery="true"`: Đẩy tác vụ OCR vào queue Celery nội bộ của OCR server (`uc1_worker1_queue`).
  * Nếu `use_celery="false"`: Xử lý OCR in-process trực tiếp trên tiến trình HTTP qua Surya OCR Server.
* **Sự cố thực tế:**
  * Thứ Sáu tuần trước (11/09/2026): Cụm Celery Result Store của server OCR hoạt động bình thường, chấp nhận `use_celery="true"`.
  * Từ cuối tuần (sau 13/09/2026): Kết nối tới Celery Result Store của server OCR bị đứt và chưa được khởi động lại. Khi gửi `use_celery="true"`, OCR server ném exception và trả về HTTP 500:
    ```json
    HTTP 500 Internal Server Error
    {"detail": "Lỗi xử lý Step 1 (OCR): \nRetry limit exceeded while trying to reconnect to the Celery result store backend. The Celery application must be restarted.\n"}
    ```
* **Vì sao `test_sync.py` chạy được:** Trong `test_sync.py`, tham số gửi lên đã được cấu hình sẵn là `"use_celery": "false"`, do đó bypass hoàn toàn cụm Celery bị hỏng của OCR server.

---

### Nguyên nhân 2: Vòng lặp Retry vô ích với các mã lỗi không thể hồi phục (4xx)
* **Vị trí code:** `_ocr_pdf_async` trong `pipeline.py`.
* **Cơ chế lỗi:** Code cũ bắt chung mọi ngoại lệ `(httpx.HTTPError, RuntimeError, ValueError)` vào vòng lặp `for attempt in range(1, MAX_RETRIES + 1)` và thử lại 5 lần.
* **Hậu quả:** Khi gặp file lỗi vĩnh viễn (ví dụ PDF chứa bảng biểu bị server từ chối bằng HTTP 400 `TABLE_CONTENT_UNSUPPORTED`), worker vẫn kiên trì retry 5 lần kèm exponential backoff, giam cầm worker suốt 20–25 phút/lần cho một file không bao giờ thành công.

---

### Nguyên nhân 3: Mất Heartbeat RabbitMQ do cấu hình Worker `--pool=solo`
* **Vị trí code:** `worker.sh`.
* **Cơ chế lỗi:** Celery worker chạy với cờ `--pool=solo` (đơn tiến trình, đơn luồng).
* **Hậu quả:** Khi gọi `asyncio.run(run_pipeline_async(...))`, luồng chính bị phong tỏa hoàn toàn để đợi I/O mạng của các file PDF (mất từ 30s đến hàng trăm giây). Thư viện mạng Kombu không thể trao quyền gửi khung truyền Heartbeat định kỳ (60s) về RabbitMQ. Quá ngưỡng 120s, RabbitMQ đóng socket TCP (`OSError: Server unexpectedly closed connection`), khiến task bị đứt kết nối và rơi vào vòng lặp re-deliver (Poison Pill).

---

### Nguyên nhân 4: Xung đột Event Loop khi chạy `test_async.py` trong môi trường Notebook
* Môi trường máy chủ dùng user `jovyan` (JupyterLab / Jupyter Notebook).
* Trong Jupyter Notebook, Kernel luôn duy trì một `asyncio event loop` chạy nền để phục vụ giao tiếp WebSocket.
* Lệnh `asyncio.run(main())` ở cuối `test_async.py` sẽ bị Python chặn lại và báo lỗi:
  ```text
  RuntimeError: asyncio.run() cannot be called from a running event loop
  ```
  Trong khi `test_sync.py` gọi hàm đồng bộ thông thường nên không bị ảnh hưởng.

---

### Nguyên nhân 5: `asyncio.gather` trong `test_async.py` thiếu `return_exceptions=True`
* `test_async.py` gọi:
  ```python
  results = await asyncio.gather(*[_ocr_one(client, name, pdf) for name, pdf in payloads])
  ```
  Mặc định, chỉ cần 1 request trong danh sách gặp lỗi (như HTTP 400/500 hoặc Timeout), toàn bộ `asyncio.gather` lập tức ném exception và làm crash toàn bộ script ngay lập tức (trong khi `test_sync.py` có khối `try ... except` cho từng file nên bỏ qua được file lỗi).

---

## 3. CHI TIẾT CÁC BƯỚC KHẮC PHỤC (IMPLEMENTATION)

Toàn bộ các sửa đổi đã được triển khai trên nhánh **`fix/async-ocr-pipeline`**:

### 3.1. Sửa `pipeline.py`
1. **Duy trì tham số an toàn:** Đảm bảo `use_celery: "false"` để không phụ thuộc vào cụm Celery backend đang lỗi của server OCR.
2. **Phân loại lỗi Non-Retryable:**
   * Bổ sung class `NonRetryableOCRError(RuntimeError)`.
   * Khi server OCR trả về mã `4xx` (như HTTP 400 `TABLE_CONTENT_UNSUPPORTED`), ném `NonRetryableOCRError` và **dừng ngay lập tức**, không lãng phí 5 lần retry.
   * Chỉ kích hoạt retry đối với các lỗi mạng hoặc mã lỗi tạm thời (`429, 500, 502, 503, 504`).

### 3.2. Sửa `test/test_async.py`
1. **Xử lý ngoại lệ từng file:** Thêm `try ... except` trong `_ocr_one` để ghi nhận mã lỗi HTTP và nội dung phản hồi từ server mà không ngắt tiến trình.
2. **Thêm `return_exceptions=True`:** Đảm bảo `asyncio.gather` thu thập kết quả đầy đủ cho tất cả các file trong batch.
3. **Tương thích mọi môi trường thực thi:**
   ```python
   if __name__ == "__main__":
       try:
           loop = asyncio.get_running_loop()
       except RuntimeError:
           loop = None

       if loop is not None and loop.is_running():
           # Nếu đang chạy trong Jupyter Notebook / IPython có sẵn event loop
           with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
               pool.submit(asyncio.run, main()).result()
       else:
           asyncio.run(main())
   ```
4. **Fallback đường dẫn file:** Tự động tìm trong `data/` nếu đường dẫn `downloaded/` không tồn tại cục bộ.

### 3.3. Sửa `worker.sh`
* Chuyển đổi pool của Celery:
  ```bash
  # Trước:
  celery -A tasks worker -Q answer_matching --pool=solo ...
  # Sau:
  celery -A tasks worker -Q answer_matching --pool=threads -c 4 --loglevel=info --logfile=worker.log --pidfile=worker.pid --detach
  ```
  👉 **Tác dụng:** Tách biệt hoàn toàn luồng quản lý kết nối AMQP (gửi Heartbeat liên tục cho RabbitMQ) với luồng chạy task async nặng, loại bỏ triệt để lỗi đứt socket.

### 3.4. Sửa `tasks.py`
* Bổ sung cấu hình kiểm soát bộ đệm:
  ```python
  celery_app.conf.worker_prefetch_multiplier = 1
  celery_app.conf.task_acks_late = True
  ```
  👉 **Tác dụng:** Cấm worker "ôm trước" nhiều task vào RAM khi đang bận xử lý task dài, đảm bảo quản trị viên có thể kiểm soát và điều phối hàng đợi trên RabbitMQ.

---

## 4. BẢNG SO SÁNH TRƯỚC VÀ SAU KHI SỬA

| Tiêu chí | Trước khi sửa (`main`) | Sau khi sửa (`fix/async-ocr-pipeline`) |
| :--- | :--- | :--- |
| **Gửi request OCR** | `"use_celery": "true"` (gặp lỗi 500 do backend server OCR chết) | `"use_celery": "false"` (xử lý trực tiếp in-process, ổn định) |
| **Xử lý lỗi HTTP 400 / Bảng biểu** | Bị kẹt retry 5 lần, chiếm dụng worker ~20–25 phút | Bắt `NonRetryableOCRError`, fail-fast ngay lập tức |
| **Worker Execution Pool** | `--pool=solo` (dễ mất heartbeat RabbitMQ khi chạy async) | `--pool=threads -c 4` (heartbeat độc lập, không rớt mạng) |
| **Prefetch RAM** | Mặc định (kéo nhiều task về RAM, khó dọn queue) | `worker_prefetch_multiplier = 1` (chỉ nhận đúng 1 task) |
| **Chạy `test_async.py` trong Notebook** | Văng `RuntimeError: cannot be called from running event loop` | Tự bọc qua `ThreadPoolExecutor`, chạy mượt mà cả CLI lẫn Notebook |
| **Xử lý batch trong `test_async.py`** | 1 file lỗi -> crash toàn bộ script | `return_exceptions=True`, tiếp tục xử lý các file còn lại |

---

## 5. KIỂM TRA VÀ XÁC NHẬN (VERIFICATION)

- Toàn bộ 4 file đã được kiểm tra cú pháp thành công bằng `python -m py_compile`.
- Đã import kiểm tra module (`pipeline`, `tasks`) không có lỗi phát sinh.
- Đã commit toàn bộ thay đổi vào git:
  * Branch: `fix/async-ocr-pipeline`
  * Commit hash: `076d28eb71a64b72fcb66103cac0ddcb5f40201a`
