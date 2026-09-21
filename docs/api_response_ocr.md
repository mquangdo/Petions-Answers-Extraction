# Canonical OCR API Response Specification (v1.0.0)

Tài liệu này mô tả chi tiết cấu trúc phản hồi JSON (Response Schema), quy tắc định dạng và ý nghĩa của từng trường dữ liệu được trả về từ Standalone OCR Service, tuân thủ chuẩn đặc tả **[Canonical Tree-Structured OCR Output v1.0.0](./ocr_canonical_tree_output_spec.md)**.

---

## 1. Mô hình dữ liệu tổng quan (Conceptual Model)

Dữ liệu OCR được thiết kế theo nguyên tắc **Lossless Canonical Representation** (biểu diễn toàn vẹn không mất mát thông tin), đóng vai trò là **Single Source of Truth** (nguồn chân lý duy nhất). Toàn bộ các định dạng phái sinh như Markdown, HTML hay Plain Text đều được chuyển đổi từ cấu trúc cây này.

Cây dữ liệu tổng quan:

```text
CanonicalOCRResponse (v1.0.0)
├── schema_version: "1.0.0"
├── document                     # Metadata tệp nguồn (ID, tên tệp, số trang, mã băm...)
├── content                      # Chuỗi văn bản thuần chuẩn hóa gộp theo thứ tự đọc logic
├── pages[]                      # Thông tin vật lý từng trang (kích thước, xoay, chất lượng ảnh...)
├── tree                         # Cây phân cấp cấu trúc logic (DocumentRootNode)
│   ├── paragraph                # Đoạn văn trước heading đầu tiên (nếu có)
│   ├── list                     # Danh sách (ordered / unordered)
│   ├── table_ref                # Node tham chiếu đến bảng biểu ở root.tables
│   ├── figure_ref               # Node tham chiếu đến visual element
│   ├── footnote                 # Ghi chú chân trang
│   └── section                  # Nhánh phân cấp (Heading 1 -> Heading 2 -> ...)
│       ├── heading              # Tiêu đề mục (text, level, span, locations)
│       └── children[]           # Các paragraph, list, table_ref, section con...
├── tables{}                     # Từ điển các bảng biểu hoàn chỉnh (keyed by table_id)
├── page_artifacts               # Header, footer, số trang lặp lại (loại khỏi cây logic)
├── visual_elements[]            # Chữ ký, con dấu đỏ, biểu đồ, hình vẽ...
├── semantic_annotations{}       # Nhãn ngữ nghĩa mở rộng (trích xuất thông tin hành chính)
├── processing                   # Thông tin pipeline, model version, normalization, thời gian chạy
└── warnings[]                   # Cảnh báo không làm gián đoạn pipeline (unresolved footnote, low OCR...)
```

---

## 2. Chi tiết các trường tại Top-Level Schema

Mỗi phản hồi thành công từ endpoint `POST /v1/ocr/documents` (hoặc kết quả task tại `GET /v1/ocr/tasks/{task_id}`) có cấu trúc gốc:

```json
{
  "schema_version": "1.0.0",
  "document": { ... },
  "content": "...",
  "pages": [ ... ],
  "tree": { ... },
  "tables": { ... },
  "page_artifacts": { ... },
  "visual_elements": [ ... ],
  "semantic_annotations": { ... },
  "processing": { ... },
  "warnings": [ ... ]
}
```

### Bảng mô tả chi tiết:

| Trường | Kiểu dữ liệu | Bắt buộc | Mô tả |
|---|:---:|:---:|---|
| `schema_version` | `string` | **Có** | Phiên bản của chuẩn schema, hiện tại là `"1.0.0"`. |
| `document` | `object` | **Có** | Chứa thông tin nhận dạng tệp gốc, mã băm, số trang và ngôn ngữ phát hiện. |
| `content` | `string` | **Có** | Chuỗi văn bản thuần nối kết toàn bộ tài liệu theo đúng thứ tự đọc logic (`reading order`). Được dùng làm hệ quy chiếu offset cho toàn bộ các `span`. |
| `pages` | `array[object]` | **Có** | Mảng chứa thông tin vật lý từng trang (chiều rộng, chiều cao, góc xoay, chất lượng ảnh, ngôn ngữ). |
| `tree` | `object` | **Có** | Gốc của cây phân cấp logic (`DocumentRootNode`), chứa các section, heading, paragraph, list, table_ref,... |
| `tables` | `object` (dict) | **Có** | Từ điển lưu cấu trúc các bảng biểu, key là `table_id`. Bảng chỉ lưu một lần duy nhất tại đây ("Tables stored once"). |
| `page_artifacts` | `object` | **Có** | Chứa các phần tử lặp lại không thuộc nội dung chính: `headers`, `footers`, `page_numbers`. |
| `visual_elements` | `array[object]` | **Có** | Danh sách các phần tử phi văn bản: con dấu (`stamp`), chữ ký (`signature`), hình ảnh (`figure`), mã vạch (`qr_code`, `barcode`). |
| `semantic_annotations` | `object` | **Có** | Nơi chứa các trường dữ liệu hành chính trích xuất (ví dụ: `document_number`, `signing_date`, `signer`, `issuing_body`). Mặc định `{}` nếu chưa gán nhãn. |
| `processing` | `object` | **Có** | Thông số vận hành pipeline: tên model, thời gian thực thi (`duration_ms`), cấu hình chuẩn hóa (`normalization`), thông tin các component model. |
| `warnings` | `array[object]` | **Có** | Danh sách các cảnh báo mềm trong quá trình OCR (như độ tin cậy thấp, footnote không tìm thấy tham chiếu). |

---

## 3. Các kiểu dữ liệu dùng chung (Common Primitives)

Toàn bộ các node trong cây và bảng biểu đều dùng chung các cấu trúc tọa độ, độ tin cậy và định vị văn bản sau:

### 3.1. `TextSpan` (Định vị text trong `content`)
Đại diện cho khoảng nửa mở `[start, end)` tính theo **Unicode code point** trong chuỗi `content` gốc:

```json
{
  "start": 120,
  "end": 245
}
```
* **Quy tắc:** `content[start:end]` tương ứng chính xác với `text` của phần tử sau khi đã chuẩn hóa.

### 3.2. `Location` (Tọa độ hình học vật lý)
Mỗi phần tử có thể xuất hiện trên 1 hoặc nhiều trang (ví dụ đoạn văn bị ngắt trang):

```json
{
  "page_number": 1,
  "bbox": [72.0, 108.5, 520.0, 145.0],
  "normalized_bbox": [0.120, 0.136, 0.867, 0.181],
  "polygon": [
    [72.0, 108.5],
    [520.0, 108.5],
    [520.0, 145.0],
    [72.0, 145.0]
  ],
  "reading_order": 0,
  "orientation": 0.0
}
```
* `page_number`: Số thứ tự trang (bắt đầu từ `1`).
* `bbox`: `[x_min, y_min, x_max, y_max]` tính theo đơn vị gốc (`pixel` hoặc `point`). Gốc tọa độ `(0,0)` ở góc trên bên trái.
* `normalized_bbox`: Tọa độ chuẩn hóa trong dải `[0.0, 1.0]` tương ứng với tỉ lệ `[x_min/w, y_min/h, x_max/w, y_max/h]`.
* `polygon`: Tọa độ đa giác lồi hoặc 4 điểm bao quanh.
* `reading_order`: Thứ tự đọc tương đối trên trang (bắt đầu từ `0`).
* `orientation`: Góc xoay chữ theo chiều kim đồng hồ (đơn vị: độ).

### 3.3. `Confidence` (Độ tin cậy)
Điểm xác suất trong dải `[0.0, 1.0]`:

```json
{
  "ocr": 0.985,
  "layout": 0.970,
  "heading_level": 0.950,
  "semantic_role": 0.900,
  "structure": 0.960,
  "detection": 0.990
}
```

---

## 4. Chi tiết từng khối thành phần

### 4.1. Khối `document`

```json
{
  "document_id": "doc_a443cd5b4f4f",
  "filename": "Cau 17 18 - Ha Noi.pdf",
  "mime_type": "application/pdf",
  "source_sha256": "a443cd5b4f4fc4542d76006ffc6b4697f649abebe392bd81b76996389a14e4d1",
  "page_count": 4,
  "languages": ["vi"],
  "title": null
}
```

* `document_id`: Định danh duy nhất của tài liệu (tiền tố `doc_` kết hợp với 12 ký tự hex của SHA256).
* `filename`: Tên file gốc được upload.
* `source_sha256`: Mã băm SHA-256 của toàn bộ byte đầu vào, phục vụ kiểm tra trùng lặp và truy vết cache.
* `page_count`: Tổng số trang tài liệu.
* `languages`: Danh sách mã ngôn ngữ BCP-47 nhận diện được (`vi`, `en`, ...).
* `title`: Tiêu đề tài liệu ở cấp document (nếu trích xuất được từ metadata hoặc tiêu đề chính).

---

### 4.2. Khối `pages`

Mỗi phần tử trong mảng đại diện cho một trang vật lý:

```json
{
  "page_number": 1,
  "width": 1654.0,
  "height": 2339.0,
  "unit": "pixel",
  "rotation": 0.0,
  "detected_languages": [
    { "code": "vi", "confidence": 0.99 }
  ],
  "quality": {
    "score": 0.95,
    "defects": []
  }
}
```

* `width`, `height`: Kích thước ảnh trang.
* `unit`: Đơn vị đo (`"pixel"`, `"point"`, `"inch"`, `"millimeter"`).
* `rotation`: Góc xoay trang cần thiết để đọc bình thường (`0.0`, `90.0`, `180.0`, `270.0`).
* `quality.score`: Điểm chất lượng ảnh từ `0.0` (rất mờ/rách) đến `1.0` (rất sắc nét).
* `quality.defects`: Danh sách khiếm khuyết ảnh phát hiện được (ví dụ: `slight_blur`, `shadow`, `low_contrast`).

---

### 4.3. Khối `tree` (Cây cấu trúc tài liệu)

`tree` bắt đầu từ `DocumentRootNode`:
- `node_id`: `"document_root"`.
- `node_type`: `"document"`.
- `children`: Danh sách các node cấp 1.

#### Các loại Node trong cây:

#### A. `SectionNode` & `HeadingNode`
Đại diện cho một chương/mục phân cấp:
```json
{
  "node_id": "sec_0001",
  "node_type": "section",
  "level": 1,
  "semantic_role": "body",
  "level_gap": false,
  "heading": {
    "node_id": "head_0001",
    "node_type": "heading",
    "level": 1,
    "text": "I. KIẾN NGHỊ CỦA CỬ TRI",
    "span": { "start": 320, "end": 344 },
    "locations": [ ... ],
    "confidence": { "ocr": 0.99, "heading_level": 0.98 }
  },
  "children": [
    { ... }
  ]
}
```
* `level`: Cấp độ tiêu đề (`1` cho `H1` / `I.`, `2` cho `H2` / `1.`, `3` cho `H3` / `a)`,...).
* `semantic_role`: Vai trò ngữ nghĩa (`"front_matter"`, `"body"`, `"appendix"`, `"references"`, `"table_of_contents"`, `"legal_notice"`).
* `level_gap`: `true` nếu có sự nhảy cóc về cấp độ (ví dụ từ `H1` nhảy thẳng xuống `H3` mà không có `H2`).

#### B. `ParagraphNode`
Đại diện cho một đoạn văn bản:
```json
{
  "node_id": "p_0002",
  "node_type": "paragraph",
  "semantic_role": "body",
  "text": "Cử tri thành phố Hà Nội kiến nghị Bộ Giáo dục và Đào tạo quan tâm...",
  "span": { "start": 345, "end": 415 },
  "locations": [ ... ],
  "footnote_refs": ["fn_0001"],
  "confidence": { "ocr": 0.98 }
}
```
* `footnote_refs`: Danh sách các `node_id` của footnote được trích dẫn bên trong đoạn văn này.

#### C. `ListNode` & `ListItemNode`
Đại diện cho danh sách liệt kê gạch đầu dòng hoặc đánh số:
```json
{
  "node_id": "list_0001",
  "node_type": "list",
  "list_style": "ordered",
  "locations": [ ... ],
  "children": [
    {
      "node_id": "item_0001",
      "node_type": "list_item",
      "marker": "1.",
      "text": "Đầu tư xây dựng thêm phòng học đạt chuẩn quốc gia.",
      "span": { "start": 416, "end": 468 },
      "locations": [ ... ],
      "footnote_refs": [],
      "children": []
    }
  ]
}
```
* `list_style`: `"ordered"` (đánh số), `"unordered"` (bullet tròn, gạch ngang), `"definition"`.
* `marker`: Ký tự đánh dấu gốc (`"1."`, `"-"`, `"(a)"`, `"•"`).
* `children`: Hỗ trợ lồng danh sách con hoặc đoạn văn phụ thuộc.

#### D. `TableRefNode`
Node trỏ đến bảng biểu (tránh lặp dữ liệu trong cây):
```json
{
  "node_id": "tbl_ref_0001",
  "node_type": "table_ref",
  "table_id": "table_0001",
  "caption": "Bảng 1: Thống kê số lượng trường lớp năm học 2025-2026",
  "locations": [ ... ]
}
```
* `table_id`: ID của bảng trong đối tượng `tables` tại root JSON.

#### E. `FigureRefNode`
Node trỏ đến visual element (hình ảnh, đồ thị):
```json
{
  "node_id": "fig_ref_0001",
  "node_type": "figure_ref",
  "element_id": "elem_stamp_01",
  "caption": "Con dấu của Bộ Giáo dục và Đào tạo",
  "locations": [ ... ]
}
```

#### F. `FootnoteNode`
Ghi chú chân trang:
```json
{
  "node_id": "fn_0001",
  "node_type": "footnote",
  "label": "1",
  "text": "Theo báo cáo số 12/BC-BGDĐT ngày 15/01/2026.",
  "span": { "start": 1250, "end": 1295 },
  "referenced_by": ["p_0002"],
  "locations": [ ... ],
  "confidence": { "ocr": 0.97 }
}
```
* `referenced_by`: Danh sách các `node_id` đoạn văn gọi đến footnote này (tạo liên kết 2 chiều Paragraph `footnote_refs` $\leftrightarrow$ Footnote `referenced_by`).

---

### 4.4. Khối `tables`

Chứa toàn bộ các bảng trong tài liệu, tổ chức dưới dạng ma trận ô lưới chuẩn hóa:

```json
{
  "tables": {
    "table_0001": {
      "table_id": "table_0001",
      "caption": {
        "text": "Bảng 1: Danh sách tổng hợp",
        "span": null,
        "locations": [ ... ]
      },
      "row_count": 3,
      "column_count": 4,
      "locations": [ ... ],
      "cells": [
        {
          "cell_id": "c_0_0",
          "row_index": 0,
          "column_index": 0,
          "row_span": 1,
          "column_span": 1,
          "role": "column_header",
          "text": "STT",
          "locations": [ ... ],
          "confidence": { "ocr": 0.99 }
        }
      ],
      "representations": {
        "markdown": "| STT | Nội dung |\n|---|---|\n| 1 | Mẫu |",
        "html": "<table>...</table>",
        "plain_text": "STT\tNội dung\n1\tMẫu"
      },
      "confidence": { "structure": 0.95 },
      "continuation_of": null
    }
  }
}
```

* `row_count`, `column_count`: Kích thước lưới bảng.
* `row_span`, `column_span`: Hỗ trợ ô gộp (merged cells).
* `role`: Vai trò của ô:
  * `"column_header"`: Tiêu đề cột.
  * `"row_header"`: Tiêu đề hàng.
  * `"stub_header"`: Ô góc trên cùng bên trái.
  * `"section_header"`: Hàng gom nhóm ngang toàn bảng.
  * `"body"`: Ô dữ liệu bình thường.
  * `"footer"`: Ô tổng kết, ghi chú cuối bảng.
* `representations`: Các biểu diễn dựng sẵn (Markdown, HTML, Plain Text có định dạng tab).
* `continuation_of`: Nếu bảng kéo dài qua nhiều trang, trường này chứa `table_id` của trang trước.

---

### 4.5. Khối `page_artifacts`

Lưu các phần tử lặp lại ở mép trang, được tách ra khỏi luồng đọc chính để không làm nhiễu dữ liệu RAG / tóm tắt:

```json
{
  "page_artifacts": {
    "headers": [
      {
        "node_id": "hdr_0001",
        "node_type": "page_header",
        "text": "VĂN PHÒNG BỘ GIÁO DỤC VÀ ĐÀO TẠO",
        "span": null,
        "locations": [ ... ],
        "confidence": { "ocr": 0.98 }
      }
    ],
    "footers": [],
    "page_numbers": [
      {
        "node_id": "pgnum_0001",
        "node_type": "page_number",
        "text": "Trang 1/4",
        "span": null,
        "locations": [ ... ],
        "confidence": { "ocr": 0.99 }
      }
    ]
  }
}
```

---

### 4.6. Khối `visual_elements`

Lưu các đối tượng hình ảnh, con dấu, chữ ký có tọa độ chính xác:

```json
{
  "visual_elements": [
    {
      "element_id": "elem_stamp_01",
      "element_type": "stamp",
      "subtype": "round_red_stamp",
      "caption": null,
      "locations": [
        {
          "page_number": 4,
          "bbox": [350.0, 1850.0, 520.0, 2020.0],
          "normalized_bbox": [0.211, 0.791, 0.314, 0.863],
          "polygon": [],
          "reading_order": 99,
          "orientation": 0.0
        }
      ],
      "associated_node_ids": ["p_signature_01"],
      "confidence": { "detection": 0.96 }
    },
    {
      "element_id": "elem_sig_01",
      "element_type": "signature",
      "subtype": null,
      "caption": null,
      "locations": [ ... ],
      "associated_node_ids": ["p_signature_01"],
      "confidence": { "detection": 0.94 }
    }
  ]
}
```

* `element_type`: `"stamp"`, `"signature"`, `"figure"`, `"picture"`, `"diagram"`, `"barcode"`, `"qr_code"`, `"checkbox"`, `"other"`.
* `associated_node_ids`: Liên kết đến node chữ đi kèm (ví dụ chức danh của người ký).

---

### 4.7. Khối `processing` & `warnings`

Cung cấp thông tin truy vết toàn diện về quy trình thực thi:

```json
{
  "processing": {
    "ocr_engine": "surya",
    "ocr_engine_version": "2.x",
    "pipeline_name": "canonical-administrative-ocr",
    "pipeline_version": "1.0.0",
    "processed_at": "2026-09-17T09:34:19.848123Z",
    "duration_ms": 17456,
    "normalization": {
      "unicode_form": "NFC",
      "line_ending": "LF",
      "join_wrapped_lines": true,
      "page_artifacts_in_content": false
    },
    "components": [
      {
        "name": "ocr_engine",
        "model": "datalab-to/surya-ocr-2",
        "version": "2.x"
      },
      {
        "name": "layout_recognition",
        "model": "qwen-layout-8043",
        "version": "1.0"
      },
      {
        "name": "spelling_corrector",
        "model": "google/gemma-4-26B-A4B-it",
        "version": "1.0"
      }
    ]
  },
  "warnings": [
    {
      "code": "LOW_OCR_CONFIDENCE",
      "message": "Page 3 contains blurry text region with OCR confidence below threshold 0.70",
      "page_number": 3,
      "node_ids": ["p_0012"],
      "severity": "warning"
    }
  ]
}
```

---

## 5. Cấu trúc Response của các Endpoint khác

### 5.1. `POST /v1/ocr/tasks` (Gửi tác vụ bất đồng bộ)
HTTP Status: `200 OK`
```json
{
  "task_id": "860ed5b0-7c18-42e9-8822-98a2d1e577f3",
  "status": "PENDING",
  "queue": "ocr_queue"
}
```

### 5.2. `GET /v1/ocr/tasks/{task_id}` (Truy vấn trạng thái tác vụ)
- **Khi đang xử lý:**
```json
{
  "task_id": "860ed5b0-7c18-42e9-8822-98a2d1e577f3",
  "status": "PENDING"
}
```
- **Khi thành công:**
```json
{
  "task_id": "860ed5b0-7c18-42e9-8822-98a2d1e577f3",
  "status": "SUCCESS",
  "result": {
    "schema_version": "1.0.0",
    "document": { ... },
    "content": "...",
    "pages": [ ... ],
    "tree": { ... },
    "tables": { ... },
    "page_artifacts": { ... },
    "visual_elements": [ ... ],
    "semantic_annotations": { ... },
    "processing": { ... },
    "warnings": []
  },
  "error": null
}
```
- **Khi thất bại:**
```json
{
  "task_id": "860ed5b0-7c18-42e9-8822-98a2d1e577f3",
  "status": "FAILURE",
  "result": null,
  "error": "Error details here"
}
```

### 5.3. `POST /v1/ocr/render` (Dựng Markdown / HTML / Plain Text)
HTTP Status: `200 OK`
```json
{
  "format": "markdown",
  "content": "# I. KIẾN NGHỊ CỦA CỬ TRI\n\nCử tri thành phố Hà Nội kiến nghị...",
  "document_id": "doc_a443cd5b4f4f"
}
```

### 5.4. `GET /health` (Kiểm tra sức khỏe dịch vụ)
HTTP Status: `200 OK`
```json
{
  "status": "ok",
  "surya_inference_url": "http://127.0.0.1:8043/v1",
  "surya_status": "healthy",
  "correction_model_url": "http://127.0.0.1:8076/v1",
  "correction_status": "healthy",
  "gpu_available": true,
  "gpu_count": 2,
  "celery_enabled": true,
  "celery_status": "connected",
  "celery_broker": "127.0.0.1:5672/",
  "cache_enabled": true,
  "cache_status": "connected",
  "cache_bucket": "ocr-results"
}
```
