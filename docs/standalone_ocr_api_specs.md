# Tài liệu Đặc tả API StandaloneOCR (Endpoints, Input & Output Schema)

Tài liệu này tổng hợp toàn bộ các **Endpoints**, **Input Schema**, và **Output Schema** được định nghĩa trong dịch vụ **StandaloneOCR** (`/home/jovyan/scratch/hieunq10/StandaloneOCR`).

---

## Mục lục
1. [Tổng quan](#1-tổng-quan)
2. [Danh sách Endpoints](#2-danh-sách-endpoints)
3. [Chi tiết từng Endpoint](#3-chi-tiết-từng-endpoint)
   - [3.1. `GET /health` (Health Check)](#31-get-health-kiểm-tra-sức-khỏe-hệ-thống)
   - [3.2. `POST /v1/ocr/documents` (Xử lý OCR trực tiếp)](#32-post-v1ocrdocuments-xử-lý-ocr-tài-liệu-chuẩn-hóa)
   - [3.3. `POST /v1/ocr/tasks` (Gửi job OCR bất đồng bộ qua Celery)](#33-post-v1ocrtasks-gửi-tác-vụ-ocr-bất-đồng-bộ)
   - [3.4. `GET /v1/ocr/tasks/{task_id}` (Truy vấn kết quả job Celery)](#34-get-v1ocrtaskstask_id-tra-cứu-trạng-thái-và-kết-quả-task)
   - [3.5. `POST /v1/ocr/render` (Render Canonical JSON sang Markdown / HTML / Text)](#35-post-v1ocrrender-chuyển-đổi-canonical-json-thành-văn-bản)
4. [Đặc tả chi tiết Canonical OCR Response Schema (v1.0.0)](#4-đặc-tả-chi-tiết-canonical-ocr-response-schema-v100)

---

## 1. Tổng quan

StandaloneOCR là dịch vụ OCR độc lập trích xuất tài liệu (PDF, Ảnh) thành định dạng chuẩn phân cấp **Canonical Tree-Structured OCR JSON (v1.0.0)**. 
- **Công nghệ lõi:** Surya OCR (Detection, Layout Qwen vLLM, Recognition), Tree Builder, Bảng biểu (Table Extraction), và LLM sửa lỗi chính tả/dấu (Gemma).
- **Hỗ trợ thực thi:** In-process hoặc phân tán qua Celery (RabbitMQ + Redis).
- **Bộ nhớ đệm:** MinIO Cache (bucket `standalone_ocr-docker` hoặc `ocr-results`).

---

## 2. Danh sách Endpoints

| STT | Phương thức | Endpoint Path | Tag | Mục đích |
|---|---|---|---|---|
| 1 | `GET` | `/health` | `Health` | Kiểm tra trạng thái hoạt động của Surya, LLM Correction, GPU, Celery và MinIO. |
| 2 | `POST` | `/v1/ocr/documents` | `OCR` | Trích xuất OCR toàn diện (đồng bộ hoặc qua Celery) và trả về Canonical Tree JSON v1.0.0. |
| 3 | `POST` | `/v1/ocr/tasks` | `OCR` | Đẩy file vào hàng đợi Celery (asynchronous), trả về ngay `task_id`. |
| 4 | `GET` | `/v1/ocr/tasks/{task_id}` | `OCR` | Tra cứu trạng thái (PENDING, SUCCESS, FAILURE) và kết quả của task Celery. |
| 5 | `POST` | `/v1/ocr/render` | `Rendering` | Nhận Canonical Tree JSON và render thành chuỗi **Markdown**, **HTML**, hoặc **Plain Text**. |

---

## 3. Chi tiết từng Endpoint

### 3.1. `GET /health` (Kiểm tra sức khỏe hệ thống)

Kiểm tra kết nối và tính sẵn sàng của các thành phần phụ thuộc (Surya Model Server, LLM sửa dấu, GPU CUDA, RabbitMQ Broker, MinIO Storage).

#### Input Schema
*Không yêu cầu tham số request (No body, no query params).*

#### Output Schema: `HealthResponse` (`application/json`)
```json
{
  "status": "ok | degraded",
  "surya_inference_url": "http://127.0.0.1:8043/v1",
  "surya_status": "healthy | unhealthy (...) | unavailable | error (...)",
  "correction_model_url": "http://127.0.0.1:8076/v1",
  "correction_status": "healthy | unhealthy (...) | unavailable | error (...)",
  "gpu_available": true,
  "gpu_count": 1,
  "celery_enabled": false,
  "celery_status": "connected | disconnected | unconfigured (null) | error (...)",
  "celery_broker": "localhost:5672//",
  "celery_queues": ["standalone_ocr.docker.queue_1"],
  "cache_enabled": false,
  "cache_status": "connected | disconnected | unconfigured (null) | error (...)",
  "cache_bucket": "standalone_ocr-docker"
}
```

---

### 3.2. `POST /v1/ocr/documents` (Xử lý OCR tài liệu chuẩn hóa)

Endpoint OCR chính của dịch vụ. Thực hiện trích xuất toàn diện và trả về đối tượng `CanonicalOCRResponse`.

#### Input Schema (`multipart/form-data`)
| Tên trường | Kiểu dữ liệu | Bắt buộc | Mặc định | Mô tả |
|---|---|---|---|---|
| `file` | `UploadFile (binary)` | **Có** | - | File PDF hoặc file ảnh cần OCR (`.pdf`, `.png`, `.jpg`...). |
| `rasterize` | `bool (Form)` | Không | `true` | Có kết xuất PDF thành ảnh trước khi chạy OCR hay không. |
| `enable_correction` | `bool (Form)` | Không | `true` | Bật/tắt bước LLM sửa lỗi chính tả và dấu tiếng Việt. |
| `process_table` | `bool (Form)` | Không | `true` | Bật/tắt nhận diện cấu trúc và trích xuất bảng biểu. |
| `use_celery` | `bool (Form)` | Không | `null` (theo config) | `true`: Đẩy qua Celery Worker; `false`: Chạy in-process; `null`: Theo cài đặt hệ thống. |
| `use_cache` | `bool (Form)` | Không | `null` (theo config) | `true`: Tra cứu MinIO cache trước; `false`: Bỏ qua tra cứu cache (nhưng vẫn lưu kết quả sau khi xong vào cache). |

#### Output Schema: `CanonicalOCRResponse` (`application/json`)
*Xem mục [4. Đặc tả chi tiết Canonical OCR Response Schema](#4-đặc-tả-chi-tiết-canonical-ocr-response-schema-v100).*

---

### 3.3. `POST /v1/ocr/tasks` (Gửi tác vụ OCR bất đồng bộ)

Đẩy tác vụ OCR vào Message Queue (RabbitMQ). Không chờ đợi OCR hoàn tất mà trả về ngay `task_id` cho client để thăm dò trạng thái.

#### Input Schema (`multipart/form-data`)
| Tên trường | Kiểu dữ liệu | Bắt buộc | Mặc định | Mô tả |
|---|---|---|---|---|
| `file` | `UploadFile (binary)` | **Có** | - | File PDF hoặc ảnh. |
| `rasterize` | `bool (Form)` | Không | `true` | Có rasterize PDF trước khi OCR không. |
| `enable_correction` | `bool (Form)` | Không | `true` | Có chạy sửa lỗi chính tả/dấu không. |
| `process_table` | `bool (Form)` | Không | `true` | Có trích xuất bảng biểu không. |

#### Output Schema: `TaskSubmitResponse` (`application/json`)
```json
{
  "task_id": "b3f07a72-7c3d-4c31-897d-6511a3df3ec6",
  "status": "PENDING",
  "queue": "standalone_ocr.docker.queue_1"
}
```

---

### 3.4. `GET /v1/ocr/tasks/{task_id}` (Tra cứu trạng thái và kết quả task)

Truy vấn kết quả của một tác vụ Celery theo `task_id`.

#### Input Schema
* **Path Parameter:**
  * `task_id` (string, bắt buộc): ID tác vụ nhận được từ endpoint `/v1/ocr/tasks`.

#### Output Schema: `TaskStatusResponse` (`application/json`)
1. **Khi tác vụ đang chạy hoặc đang chờ (PENDING, STARTED...):**
```json
{
  "task_id": "b3f07a72-7c3d-4c31-897d-6511a3df3ec6",
  "status": "PENDING",
  "result": null,
  "error": null
}
```
2. **Khi tác vụ thất bại (FAILURE):**
```json
{
  "task_id": "b3f07a72-7c3d-4c31-897d-6511a3df3ec6",
  "status": "FAILURE",
  "result": null,
  "error": "Chi tiết lỗi ngoại lệ gặp phải..."
}
```
3. **Khi tác vụ thành công (SUCCESS):**
```json
{
  "task_id": "b3f07a72-7c3d-4c31-897d-6511a3df3ec6",
  "status": "SUCCESS",
  "result": { /* Đối tượng CanonicalOCRResponse chi tiết */ },
  "error": null
}
```

---

### 3.5. `POST /v1/ocr/render` (Chuyển đổi Canonical JSON thành văn bản)

Nhận vào đối tượng `CanonicalOCRResponse` đã có từ trước và render sang định dạng hiển thị mong muốn mà **không cần chạy lại quy trình OCR**.

#### Input Schema: `RenderRequest` (`application/json`)
```json
{
  "canonical": {
    /* Toàn bộ cấu trúc CanonicalOCRResponse nhận được từ /v1/ocr/documents */
  },
  "options": {
    "format": "markdown",       // "markdown" | "html" | "plain_text" (mặc định: "markdown")
    "include_tables": true,     // Có đưa bảng vào nội dung kết quả không
    "include_footnotes": true,  // Có đưa footnote vào nội dung không
    "exclude_roles": ["appendix"], // Danh sách vai trò loại bỏ (ví dụ: ["appendix", "metadata"])
    "include_headings": true    // Có bao gồm tiêu đề mục khi xuất plain_text không
  }
}
```

#### Output Schema: `RenderResponse` (`application/json`)
```json
{
  "format": "markdown",
  "content": "# CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n\nNội dung văn bản được render...",
  "document_id": "doc_e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

---

## 4. Đặc tả chi tiết Canonical OCR Response Schema (v1.0.0)

Mô hình dữ liệu trả về của `/v1/ocr/documents` và trường `result` của `/v1/ocr/tasks/{task_id}` tuân thủ schema `CanonicalOCRResponse`:

```json
{
  "schema_version": "1.0.0",
  "document": {
    "document_id": "doc_xxxx",
    "filename": "cong_van.pdf",
    "mime_type": "application/pdf",
    "source_sha256": "489770829c04fc2757ec4eba045fce24...",
    "page_count": 2,
    "languages": ["vi"],
    "title": null
  },
  "content": "Toàn bộ nội dung văn bản được ghép theo thứ tự đọc tự nhiên...",
  "pages": [
    {
      "page_number": 1,
      "width": 1654.0,
      "height": 2338.0,
      "unit": "pixel",
      "rotation": 0.0,
      "detected_languages": [
        { "code": "vi", "confidence": 0.98 }
      ],
      "quality": {
        "score": 0.95,
        "defects": []
      }
    }
  ],
  "tree": {
    "node_id": "document_root",
    "node_type": "document",
    "children": [
      {
        "node_id": "sec_1",
        "node_type": "section",
        "level": 1,
        "semantic_role": "body",
        "level_gap": false,
        "heading": {
          "node_id": "h_1",
          "node_type": "heading",
          "level": 1,
          "text": "I. KẾT QUẢ GIẢI QUYẾT KIẾN NGHỊ",
          "span": { "start": 0, "end": 31 },
          "locations": [
            {
              "page_number": 1,
              "bbox": [100.0, 150.0, 800.0, 180.0],
              "normalized_bbox": [0.06, 0.064, 0.483, 0.077],
              "polygon": [],
              "reading_order": 0,
              "orientation": 0.0
            }
          ],
          "confidence": { "ocr": 0.99, "layout": 0.98 }
        },
        "children": [
          {
            "node_id": "p_1",
            "node_type": "paragraph",
            "semantic_role": "body",
            "text": "Nội dung trả lời chi tiết...",
            "span": { "start": 32, "end": 150 },
            "locations": [...],
            "footnote_refs": [],
            "confidence": { "ocr": 0.97 }
          },
          {
            "node_id": "tbl_ref_1",
            "node_type": "table_ref",
            "table_id": "tbl_001",
            "caption": "Bảng thống kê số liệu",
            "locations": [...]
          }
        ]
      }
    ]
  },
  "tables": {
    "tbl_001": {
      "table_id": "tbl_001",
      "row_count": 3,
      "column_count": 2,
      "cells": [
        {
          "cell_id": "c_0_0",
          "row_index": 0,
          "column_index": 0,
          "row_span": 1,
          "column_span": 1,
          "role": "column_header",
          "text": "STT",
          "locations": [...]
        }
      ],
      "representations": {
        "html": "<table>...</table>",
        "markdown": "| STT | Nội dung |\n|---|---|\n| 1 | Dữ liệu |",
        "plain_text": "STT\tNội dung"
      }
    }
  },
  "page_artifacts": {
    "headers": [],
    "footers": [],
    "page_numbers": []
  },
  "visual_elements": [
    {
      "element_id": "vis_1",
      "element_type": "signature",
      "subtype": "blue_pen_signature",
      "caption": null,
      "locations": [...]
    }
  ],
  "semantic_annotations": {},
  "processing": {
    "ocr_engine": "surya",
    "ocr_engine_version": "2.x",
    "pipeline_name": "canonical-administrative-ocr",
    "pipeline_version": "1.0.0",
    "processed_at": "2026-09-21T02:44:20.123456Z",
    "duration_ms": 3250,
    "normalization": {
      "unicode_form": "NFC",
      "line_ending": "LF",
      "join_wrapped_lines": true,
      "page_artifacts_in_content": false
    },
    "components": [
      { "name": "detection", "model": "surya-detection", "version": "1.0" },
      { "name": "layout", "model": "surya-layout-qwen", "version": "2.0" },
      { "name": "correction", "model": "google/gemma-4-26B-A4B-it", "version": "vllm" }
    ]
  },
  "warnings": []
}
```

---
*Tài liệu được khởi tạo tự động dựa trên mã nguồn thực tế tại `/home/jovyan/scratch/hieunq10/StandaloneOCR`.*
