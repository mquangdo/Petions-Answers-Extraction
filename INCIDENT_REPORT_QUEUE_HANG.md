# BÁO CÁO SỰ CỐ: TẮC NGHẼN HÀNG ĐỢI WORKER (ANSWER MATCHING QUEUE HANG)

- **Các ngày ghi nhận:** 11/09/2026 & 13/09/2026
- **Dịch vụ ảnh hưởng:** Celery Worker `answer_matching` (`ea_api_with_metadata_v2`)
- **Mức độ ảnh hưởng:** Toàn bộ message trong queue `answer_matching` bị giữ và không được xử lý trong thời gian dài (từ 25 đến 50 phút mỗi lần sự cố xảy ra).
- **Hiện tượng đặc trưng:** 
  1. Task bị kẹt trong vòng lặp thử lại vô tận (Poison Pill loop).
  2. Số lượng consumer trên RabbitMQ tụt về 0 (`consumers = 0`) dù tiến trình worker của hệ điều hành vẫn đang chạy.
  3. Người quản trị đã purge/xóa sạch message trên RabbitMQ nhưng Worker vẫn tự động lấy task tiếp theo từ bộ đệm RAM ra xử lý.

---

## 1. TỔNG QUAN SỰ CỐ (EXECUTIVE SUMMARY)

Hệ thống ghi nhận 2 đợt sự cố nghẽn hàng đợi nghiêm trọng tại worker `answer_matching`:

1. **Đợt 1 (11/09/2026 - Sự cố PDF chứa bảng biểu):** 
   Lúc `01:23:58`, tiếp nhận Job `35ee6485...` chứa file PDF có 61 bảng biểu bị OCR từ chối (`TABLE_CONTENT_UNSUPPORTED`, HTTP 400). Do cơ chế thử lại sai lầm với lỗi 4xx kết hợp cùng chế độ đơn luồng `--pool=solo`, tiến trình chính bị chiếm dụng suốt hơn 21 phút/lần, làm đứt heartbeat với RabbitMQ, gây lặp lại task và tắc nghẽn queue trong 43 phút.

2. **Đợt 2 (13/09/2026 - Sự cố OCR Timeout, Consumer = 0 & Prefetch Buffer Persistence):** 
   Lúc `14:47:24`, worker nhận Job `139e55a9...` (gồm 2 file PDF dung lượng lớn ~6MB mỗi file). Phía OCR server xử lý quá lâu khiến request bị `ReadTimeout` (300 giây/lần thử). Trong thời gian chờ 300 giây, worker không gửi được heartbeat khiến RabbitMQ đơn phương hủy kết nối TCP sau 120 giây và xóa bỏ consumer trên queue (`consumers: 0`). 
   Đặc biệt, dù quản trị viên đã purge xóa sạch hàng đợi trên RabbitMQ trong lúc task đầu tiên đang chạy, nhưng do cơ chế `prefetch`, task tiếp theo (`4579fdcd...`) đã được nạp sẵn vào bộ nhớ RAM của tiến trình worker từ lúc `14:47:24`. Khi task đầu tiên kết thúc lúc `15:13:12`, worker tiếp tục lấy task thứ hai từ RAM ra chạy tiếp mà không hề kết nối lại broker, duy trì trạng thái `consumers = 0` và tiếp tục chiếm dụng worker thêm ~25 phút.

---

## 2. DỮ LIỆU CÁC FILE GÂY SỰ CỐ

### 2.1. File sự cố ngày 11/09/2026 (Lỗi bảng biểu - HTTP 400)
- **Đường dẫn:** `/home/jovyan/scratch/quangdm/ea_api_with_metadata_v2/uploaded_files/35ee6485-6e91-4e20-b2cd-22d38ee863d6/4.pdf`
- **Mã nhận diện `file_id` (MD5):** `68099eb7e5d541add5597509e41b579e`
- **Tiêu đề:** `integration_guide` (55 trang, dung lượng 547 KB)
- **Lỗi OCR:** `{"code":"TABLE_CONTENT_UNSUPPORTED","message":"Tài liệu PDF chứa 61 bảng biểu (table)..."}`

### 2.2. Các file sự cố ngày 13/09/2026 (Lỗi OCR Timeout - Hơn 300s/lần)
- **Đường dẫn:** 
  - `uploaded_files/139e55a9-cd55-4a2d-a630-f9e70fb2c153/0.pdf` (Dung lượng 6.1 MB, 7 trang)
  - `uploaded_files/139e55a9-cd55-4a2d-a630-f9e70fb2c153/1.pdf` (Dung lượng 6.0 MB, 16 trang)
- **Mã nhận diện `file_id`:** `7c8ad219c28ff786bf8381f152da3706` & `c683baca5176387d7a24d057253eb68c`
- **Hiện tượng:** File scan kích thước lớn khiến OCR server không phản hồi kịp trong 300s, liên tục ném lỗi timeout `httpx.ReadTimeout`.

---

## 3. NGUYÊN NHÂN GỐC RỄ (ROOT CAUSE ANALYSIS - RCA)

Sự cố xuất phát từ sự kết hợp của 3 yếu tố kiến trúc và lập trình:

### 3.1. Logic Retry tự động với thời gian chờ quá lớn
Trong file `pipeline.py` (hàm `_ocr_pdf_async`):
- `OCR_TIMEOUT = 300` (giây)
- `MAX_RETRIES = 5`
- Mọi exception từ `httpx.HTTPError` (bao gồm `ReadTimeout`) đến `RuntimeError` (bao gồm HTTP 400) đều bị bắt vào khối `except` và kích hoạt thử lại 5 lần.
- Kết quả: Khi gặp 1 request bị đơ hoặc lỗi lặp lại, thời gian bị giam giữ tối thiểu là:
  $$\text{Tổng thời gian} \approx 5 \times 300\text{s} + \text{backoff} \approx 25\text{–}26\text{ phút}$$

### 3.2. Cấu hình Worker `--pool=solo` làm tê liệt Event Loop
File khởi chạy `worker.sh` cấu hình:
```bash
celery -A tasks worker -Q answer_matching --pool=solo ...
```
- Chế độ `--pool=solo` chạy task trên chính tiến trình chính (MainProcess) của Celery.
- Khi gọi `asyncio.run(run_pipeline_async(...))`, luồng thực thi bị chiếm dụng 100% suốt 20–25 phút.
- Event loop của thư viện mạng Kombu không được trao quyền điều khiển, dẫn tới **không thể gửi heartbeat định kỳ tới RabbitMQ** (`missed heartbeat from...`).
- RabbitMQ nhận định worker đã chết sau timeout (120s) và chủ động ngắt socket (`OSError: Server unexpectedly closed connection`).

### 3.3. Vòng lặp nạp lại task (Re-delivery / Poison Pill Loop) và Prefetch Buffer
- Khi socket bị RabbitMQ đóng, message task đang chạy chưa kịp ACK sẽ được RabbitMQ tự động trả về queue ở trạng thái `Ready`.
- Sau khi worker chạy xong chu kỳ retry và thoát ra, nó reconnect lại RabbitMQ và lập tức nhận lại chính task lỗi đó để tiếp tục chu kỳ hành xác tiếp theo.
- Worker lại áp dụng cơ chế `prefetch_multiplier` mặc định (> 1), kéo trước nhiều task về bộ nhớ RAM nội bộ, tự ý chạy tiếp ngay cả khi quản trị viên đã purge queue trên RabbitMQ.

---

## 4. PHÂN TÍCH CHUYÊN SÂU (DEEP DIVE Q&A)

### 4.1. Tại sao socket giữa RabbitMQ và Worker bị đứt trong lúc task đang chạy?
- **Cơ chế AMQP Heartbeat của RabbitMQ:**
  RabbitMQ sử dụng các khung truyền Heartbeat định kỳ (mặc định 60 giây) để giám sát trạng thái sống của client. Nếu sau khoảng thời gian timeout (thường là 2 chu kỳ heartbeat = 120 giây) mà broker không nhận được bất kỳ tín hiệu nào từ worker, RabbitMQ sẽ coi kết nối đó đã chết (zombie/hung connection) và **chủ động đóng socket TCP** từ phía máy chủ (`OSError: Server unexpectedly closed connection`).
- **Nguyên nhân Worker không gửi Heartbeat:**
  Worker chạy ở chế độ `--pool=solo` (chỉ có 1 process và 1 thread duy nhất). Khi gọi `asyncio.run(...)`, luồng duy nhất này bị chiếm dụng hoàn toàn để chạy tính toán logic và các request HTTP gửi tới OCR server. Vì task bị kẹt retry 5 lần kéo dài tới 20–25 phút, event loop của Celery/Kombu bị phong tỏa 100%, không thể gửi đi bất kỳ heartbeat frame nào.
- **So sánh với `--pool=threads` hoặc `--pool=prefork`:**
  Nếu sử dụng pool đa luồng (`threads`) hoặc đa tiến trình (`prefork`), luồng quản lý kết nối RabbitMQ và luồng thực thi task tách biệt hoàn toàn. Dù task có chạy 30 phút hay vài tiếng, heartbeat vẫn được gửi đều đặn và socket **không bao giờ bị đứt**.

### 4.2. Việc đứt socket này có được tự động xử lý không?
**Câu trả lời: CÓ ở tầng hạ tầng mạng và bảo toàn dữ liệu, nhưng KHÔNG giải quyết được tắc nghẽn logic, thậm chí gây ra vòng lặp vô tận (Poison Pill):**

1. **Các phần ĐƯỢC tự động xử lý:**
   - **Tự động kết nối lại (Auto-Reconnect):** Ngay khi task kết thúc và main thread được giải phóng, Celery phát hiện mất socket và tự động kết nối lại RabbitMQ trong vòng chưa đầy 1 giây (`consumer: Connection to broker lost. Trying to re-establish...` -> `Connected to amqp...`).
   - **Tự động bảo toàn dữ liệu (At-least-once Delivery):** Theo cơ chế xác nhận (ACK) của AMQP, khi kết nối bị đứt trong lúc task chưa gửi ACK hoàn tất, RabbitMQ tự động nạp lại (re-deliver / requeue) message đó từ trạng thái `Unacknowledged` về lại `Ready` để đảm bảo không bị mất dữ liệu yêu cầu của người dùng.

2. **Mặt trái: Tự động biến thành thảm họa "Poison Pill" (Vòng lặp vô tận):**
   - Do lỗi xuất phát từ file PDF lỗi vĩnh viễn (chứa bảng biểu hoặc timeout không hồi phục), mỗi khi worker vừa reconnect xong, RabbitMQ lại tự động đẩy lại **chính task lỗi đó**.
   - Worker lại nhận task, lại bị block 25 phút, lại mất heartbeat, socket lại đứt, lại nạp lại...
   - Kết hợp với cơ chế `prefetch` (nhận trước các task tiếp theo vào buffer), một task lỗi duy nhất đã khóa chết toàn bộ worker và khiến các job đến sau bị nghẽn hoàn toàn.

### 4.3. "Missed Heartbeat" trong file log là gì, gây ra điều gì và tại sao thi thoảng vẫn thấy nhưng worker vẫn chạy được?

Trong Celery có **2 cơ chế Heartbeat hoàn toàn khác nhau** cần phân biệt rõ:

| Tiêu chí | 1. Gossip Heartbeat (Giữa các Worker) | 2. AMQP Broker Heartbeat (Worker với RabbitMQ) |
|---|---|---|
| **Mục đích** | Các worker trong cluster thông báo trạng thái với nhau qua giao thức Celery Gossip. | Duy trì và giám sát kết nối TCP giữa Worker và RabbitMQ server. |
| **Dạng log xuất hiện** | `[INFO/MainProcess] missed heartbeat from worker1@...` | Không có dòng log riêng mà biểu hiện bằng exception ngắt socket: `OSError: Server unexpectedly closed connection`. |
| **Mức độ nghiêm trọng** | Mức `INFO` — Chỉ mang tính chất thông tin trạng thái giữa các worker lân cận. | Mức `CRITICAL/ERROR` — Rớt kết nối mạng, socket bị đóng, task bị requeue. |
| **Hậu quả khi bị miss** | Worker hiện tại tạm thời đánh dấu worker kia bận hoặc vắng mặt; **không ảnh hưởng** tới việc thực thi task của worker hiện tại. | RabbitMQ hủy kết nối TCP, coi worker đã chết; toàn bộ task chưa ACK bị trả ngược lại queue. |

#### Vì sao trong log thi thoảng vẫn thấy `missed heartbeat from...` nhưng worker vẫn chạy bình thường?
1. **Bản chất dòng log:** Log `missed heartbeat from worker1@coder-sinhnq3-dev` nghĩa là worker hiện tại không nhận được heartbeat từ node `worker1` (do node `worker1` đang bận CPU hoặc bị restart). Đây là việc của node khác, không ảnh hưởng đến node hiện tại.
2. **Khoảng trễ ngắn (Dưới ngưỡng 120s):** Khi chính worker hiện tại bận xử lý một tác vụ ngắn (vài giây đến 30–60 giây), event loop bị chậm một chút nên nó cũng trễ việc nhận/gửi Gossip heartbeat. Tuy nhiên khoảng trễ này **nhỏ hơn ngưỡng timeout 120 giây của RabbitMQ**, socket TCP vẫn sống nguyên vẹn nên worker vẫn tiếp tục chạy bình thường.

### 4.4. Hiện tượng Consumer = 0 trong khi tiến trình Worker vẫn đang chạy (Sự cố ngày 13/09/2026)

#### Bối cảnh ghi nhận:
- Message mới đẩy vào queue `answer_matching` (Job ID `df6e0d1c-c75a-4390-8b85-582e58abbf4f` lúc `14:57:53`) bị kẹt ở trạng thái `Ready`.
- Kiểm tra RabbitMQ Management API thấy: **`consumers: 0`**.
- Nhưng kiểm tra hệ điều hành qua `ps aux` thì tiến trình Worker Celery (`PID 2330255`) **vẫn đang tồn tại và hoạt động**.

#### Cơ chế kỹ thuật dẫn đến Consumer = 0:
1. **Worker bị treo trong I/O timeout của OCR:**
   - Lúc `14:47:24`, worker nhận Job `139e55a9...` (gồm 2 file PDF dung lượng ~6MB mỗi file).
   - Khi gửi OCR tới endpoint `https://8078.../step1/ocr`, dịch vụ OCR không phản hồi kịp khiến `httpx` rơi vào `ReadTimeout` với thời gian chờ tối đa **300 giây (`OCR_TIMEOUT = 300`)** cho mỗi lần gọi.
2. **RabbitMQ đơn phương đóng kết nối (Drop Consumer):**
   - Do worker chạy chế độ đơn luồng `--pool=solo`, toàn bộ CPU thread bị block cứng bên trong hàm `httpx`.
   - Sau 120 giây (ngưỡng Heartbeat Timeout của RabbitMQ), không nhận được heartbeat nào từ client, RabbitMQ kết luận kết nối đã chết và **chủ động đóng socket TCP từ phía server** vào lúc khoảng `14:49:24`.
   - Phía RabbitMQ hủy channel, **xóa bỏ consumer của queue `answer_matching` -> `consumers = 0`**.
   - Phía máy chủ của Worker, socket rơi vào trạng thái **`CLOSE_WAIT`** (`FD 6 -> TCP localhost:37706->localhost:amqp (CLOSE_WAIT)`).
3. **Worker hoàn toàn "mù thông tin" về việc mất kết nối:**
   - Vì là `--pool=solo`, không có background thread nào để xử lý mạng. Luồng mạng của Celery chỉ chạy khi code Python trả về.
   - Code Python tiếp tục retry 5 lần (mỗi lần timeout 300s + backoff -> tổng thời gian kéo dài tới **hơn 25 phút**).
   - Trong suốt hơn 25 phút này, Worker vẫn miệt mài retry mà không hề biết socket tới broker đã bị cắt từ 14:49.
4. **Hệ quả nghẽn:**
   - Bất kỳ message nào đến sau đều không có consumer nào nhận và bắt buộc phải nằm đợi.

### 4.5. Cơ chế Prefetch Buffer (Bộ đệm RAM nội bộ của Worker) và lý do vì sao "Xóa message trên RabbitMQ nhưng task vẫn tự động chạy"

#### Bản chất cơ chế Prefetch & Reserved Tasks:
- Khi một Celery Worker kết nối tới RabbitMQ, theo mặc định nó **không chỉ lấy đúng 1 message đang chạy**, mà nó kéo sẵn nhiều message về lưu trữ cục bộ trong bộ nhớ RAM của tiến trình worker (thông qua cấu hình `worker_prefetch_multiplier`, mặc định thường là 4).
- Những message này một khi đã được RabbitMQ gửi qua socket TCP tới Worker thì **đã rời khỏi hàng đợi sẵn sàng của RabbitMQ** và trở thành **Reserved Tasks** nằm riêng trong RAM của tiến trình Python (`PID 2330255`).

#### Tại sao xóa hết message trên RabbitMQ nhưng task tiếp theo vẫn tự động chạy?
Sự việc diễn ra trên thực tế theo 4 bước:
1. **Kéo sẵn 2 task về RAM (`14:47:24`):** Lúc vừa kết nối lại, RabbitMQ có sẵn cả 2 task: `139e55a9...` và `4579fdcd...`. Do cơ chế Prefetch, Worker đã nạp cả hai task này vào RAM của tiến trình `PID 2330255`. Nó bắt đầu chạy `139e55a9...`, còn task `4579fdcd...` được xếp hàng chờ sẵn trong RAM.
2. **RabbitMQ ngắt kết nối (`14:49:24`):** Sau 120s không có heartbeat, RabbitMQ ngắt socket. Hai bên hoàn toàn mất liên lạc.
3. **Quản trị viên xóa message trên RabbitMQ:** Lệnh xóa (Purge Queue) trên RabbitMQ **chỉ có tác dụng xóa các message đang nằm trên đĩa/RAM của máy chủ RabbitMQ** (các message chưa deliver). Lệnh này **hoàn toàn KHÔNG THỂ xóa được dữ liệu đã nằm trong bộ nhớ RAM của tiến trình Python Worker OS**. Vì socket giữa 2 bên đã đứt từ 14:49, RabbitMQ cũng không thể gửi bất kỳ tín hiệu nào để yêu cầu Worker hủy task đã tải.
4. **Tự động chạy từ RAM (`15:13:12`):** Khi Job `139e55a9...` kết thúc 5 lần retry, tiến trình Python kiểm tra hàng đợi nội bộ trong RAM của nó và thấy task `4579fdcd...` vẫn đang nằm chờ sẵn từ lúc 14:47. Thế là nó lập tức lấy ra chạy tiếp, **hoàn toàn chạy bằng dữ liệu trong RAM mà không cần kết nối tới RabbitMQ**.

---

## 5. DÒNG THỜI GIAN SỰ CỐ (TIMELINE)

### Sự cố đợt 1 (11/09/2026): Lỗi Bảng biểu 61 Tables
| Mốc thời gian | Sự kiện | Chi tiết |
|---|---|---|
| **01:23:58** | Nhận Job lỗi lần 1 | Worker nhận task `35ee6485...` (5 file, 116 KN). |
| **01:24:01 – 01:45:30** | Retry 5 lần | File 5 bị lỗi bảng biểu (400), mỗi lần OCR mất > 4m. Worker bị block suốt 21 phút. |
| **01:44:02** | Job mới bị kẹt | Job `0a855470...` được đẩy vào queue, bị worker prefetch nhưng không được chạy. |
| **01:45:30** | Socket đứt & Reconnect | Hết 5 lần retry, socket rớt vì timeout heartbeat. Celery reconnect lại. |
| **01:45:32 – 02:06:49** | Lặp lại chu kỳ 2 | RabbitMQ nạp lại task lỗi, worker lại block thêm 21 phút lần hai. |
| **02:06:55** | Giải phóng queue | Job `0a855470...` chạy xong trong 5.97s, queue trở về trạng thái sạch. |

### Sự cố đợt 2 (13/09/2026): Lỗi OCR Timeout, Consumer = 0 & Prefetch Persistence
| Mốc thời gian | Sự kiện | Chi tiết |
|---|---|---|
| **14:47:24** | Nhận Job & Prefetch | Worker nhận task `139e55a9...`, đồng thời nạp sẵn task `4579fdcd...` vào RAM bộ đệm nội bộ. Bắt đầu gọi OCR. |
| **14:49:24** | RabbitMQ đóng socket | Quá 120s không có heartbeat, RabbitMQ ngắt kết nối. Consumer tụt về 0 (`consumers: 0`). Socket worker rơi vào `CLOSE_WAIT`. |
| **14:52:24 – 15:07:46** | 4 lần Timeout (300s/lần) | `httpx.ReadTimeout` liên tiếp 4 lần do file 6MB xử lý quá lâu. |
| **~15:00** | Quản trị viên Purge Queue | Quản trị viên xóa message trên RabbitMQ, nhưng task `4579fdcd...` trong RAM của worker không bị ảnh hưởng. |
| **15:13:12** | Task 1 kết thúc | Task `139e55a9...` thất bại sau 5 lần retry (~26 phút). |
| **15:13:12** | Tự động chạy Task 2 từ RAM | Worker chưa kịp reconnect mạng thì đã lập tức lấy Task `4579fdcd...` từ RAM ra chạy tiếp. Socket vẫn kẹt ở `CLOSE_WAIT`, `consumers` vẫn là 0. |
| **15:18:12 – 15:23:16** | Task 2 bị Timeout tiếp | Task `4579fdcd...` lại gặp 2 file 6MB, tiếp tục timeout 300s/lần, giam cầm worker thêm ~25 phút nữa (đến ~15:38–15:40). |

---

## 6. HÀNH ĐỘNG KHẮC PHỤC ĐỀ XUẤT (ACTION ITEMS)

### Mức độ P0 (Cần xử lý ngay trong code & config)
1. **Cấu hình bắt buộc `worker_prefetch_multiplier = 1`:**
   Trong `tasks.py`:
   ```python
   celery_app.conf.worker_prefetch_multiplier = 1
   ```
   **Ý nghĩa sống còn:** Cấm worker "ôm trước" task vào RAM. Khi worker đang bận xử lý 1 task, nó không được phép tải thêm task nào khác. Nhờ đó, khi quản trị viên xóa message trên RabbitMQ hoặc cần điều phối lại, các task chưa chạy vẫn nằm trên RabbitMQ và được kiểm soát 100%.

2. **Chỉ retry đối với mã lỗi tạm thời (Transient Errors):**
   Trong `pipeline.py`, chỉ retry đối với danh sách `RETRYABLE_CODES = {429, 500, 502, 503, 504}` hoặc lỗi mạng (`httpx.TransportError`).
   Đối với lỗi `400 Bad Request` hoặc các lỗi nghiệp vụ như `TABLE_CONTENT_UNSUPPORTED`, dừng ngay lập tức (`raise unretryable error`).

3. **Giảm `OCR_TIMEOUT` và `MAX_RETRIES` hợp lý:**
   - Giảm `OCR_TIMEOUT` từ 300s xuống khoảng 60–90s.
   - Giảm `MAX_RETRIES` từ 5 xuống 2 lần để tránh giam cầm worker quá 3 phút khi xảy ra sự cố.

### Mức độ P1 (Tối ưu hóa kiến trúc Worker)
1. **Chuyển đổi pool của Celery Worker:**
   Không sử dụng `--pool=solo` cho worker chạy tác vụ nặng/I/O dài. Cập nhật `worker.sh` sang `--pool=threads` (hoặc `--pool=prefork`):
   ```bash
   celery -A tasks worker -Q answer_matching --pool=threads -c 4 --loglevel=info --logfile=worker.log --pidfile=worker.pid --detach
   ```
   Tách biệt hoàn toàn luồng quản lý kết nối AMQP (duy trì heartbeat) với luồng thực thi task nặng.

2. **Cấu hình Acknowledge an toàn:**
   ```python
   celery_app.conf.task_acks_late = True
   celery_app.conf.task_reject_on_worker_lost = True
   ```
