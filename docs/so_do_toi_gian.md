# SƠ ĐỒ HỆ THỐNG TỐI GIẢN (ANSWER MATCHING API V2)

> Tài liệu mô tả luồng hoạt động của hệ thống dưới dạng **Sequence Diagram tối giản**, trực quan và dễ theo dõi nhất.

---

## 1. SƠ ĐỒ TUẦN TỰ TỔNG QUAN (CORE SEQUENCE DIAGRAM)

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client (HTTT)
    participant API as FastAPI (:8005)
    participant DB as PostgreSQL / Cache
    participant Worker as Celery Worker
    participant AI as OCR & vLLM (:8078 / :8076)

    %% 1. Gửi request & kiểm tra Cache
    Client->>API: 1. POST /answer-matching (Files PDF & Kiến nghị)
    API->>DB: 2. Kiểm tra Cache theo mã Hash

    alt Trường hợp Cache Hit (Trùng dữ liệu cũ)
        DB-->>API: Có kết quả sẵn
        API-->>Client: 3. Trả về ngay kết quả { status: "FINISHED", result }
    else Trường hợp Cache Miss (Dữ liệu mới)
        API->>DB: 4. Tạo Job mới & đẩy vào RabbitMQ
        API-->>Client: 5. Trả ngay { request_id, status: "PROCESSING" }

        %% 2. Worker xử lý ngầm
        activate Worker
        Worker->>AI: 6. Gửi PDF OCR & Trích xuất nội dung
        AI-->>Worker: Trả về văn bản Markdown
        Worker->>Worker: 7. Ghép nối câu trả lời (Jaccard Matching)
        Worker->>DB: 8. Cập nhật kết quả & Lưu Cache
        deactivate Worker

        %% 3. Client Polling nhận kết quả
        loop Polling định kỳ mỗi 2s - 3s
            Client->>API: 9. GET /answer-matching/{request_id}
            API->>DB: Kiểm tra trạng thái Job
            DB-->>API: Trả về trạng thái
            API-->>Client: { status: "PROCESSING" } (nếu chưa xong)
        end

        Client->>API: 10. GET /answer-matching/{request_id} (Lần cuối)
        API->>DB: Lấy kết quả hoàn tất
        DB-->>API: Dữ liệu kết quả
        API-->>Client: 11. Trả về kết quả JSON { status: "FINISHED", result }
    end
```

---

## 2. SƠ ĐỒ TUẦN TỰ NỘI BỘ BƯỚC AI PIPELINE (PIPELINE SEQUENCE)

Biểu diễn tuần tự cách Worker bóc tách và ghép nối dữ liệu của từng Job:

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker (pipeline.py)
    participant OCR as OCR Server (:8078)
    participant LLM as vLLM Server (:8076)

    W->>W: 1. Đọc đồng thời các file PDF từ ổ đĩa dùng chung
    
    par Gọi OCR song song (Semaphore = 3)
        W->>OCR: 2. POST /step1/ocr (use_celery: false)
        OCR-->>W: Trả về nội dung Markdown
    end

    W->>W: 3. Làm sạch text & Trích xuất Metadata (Số CV, Ngày, Người ký)

    alt Định dạng chuẩn (f1 / f2 / f3)
        W->>W: 4a. Bóc tách cặp {Nội dung, Trả lời} bằng Regex
    else Định dạng phức tạp
        W->>LLM: 4b. Phân đoạn ngữ nghĩa (Semantic Chunking)
        LLM-->>W: Trả về các khối nội dung
    end

    W->>W: 5. Tính điểm tương đồng Jaccard giữa Kiến nghị và Câu trả lời
    W->>W: 6. Lọc kết quả (ngưỡng >= 0.5) & Đóng gói danh sách answers
```

---

## 3. TÓM TẮT 3 TRẠNG THÁI JOB

| Trạng thái | Ý nghĩa | Hành động của Client |
| :--- | :--- | :--- |
| **`PROCESSING`** | Job đang trong hàng đợi hoặc đang chạy OCR / Matching | Tiếp tục gọi polling sau 2-3s |
| **`FINISHED`** | Xử lý thành công, có object `result` kèm theo | Dừng polling, nhận câu trả lời |
| **`FAILED`** | Có lỗi xảy ra (file hỏng, lỗi mạng không thể phục hồi) | Dừng polling, hiển thị thông báo lỗi |
