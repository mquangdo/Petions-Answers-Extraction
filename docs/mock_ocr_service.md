# Mock OCR Service (`ocr_service/`)

Tài liệu mô tả service giả lập OCR dùng cho dev/test: nhận PDF, trả markdown
ngay lập tức mà **không chạy OCR thật**. Stateless hoàn toàn (không cache).

- **Mock là gì:** server giả bắt chước y hình thức API thật (cùng URL, cùng
  request/response) nhưng bên trong chỉ trả dữ liệu mẫu có sẵn. Dev/test
  pipeline không cần server OCR GPU thật.
- **Spec gốc mà mock bắt chước:** `docs/standalone_ocr_api_specs.md`
  (endpoint `POST /step1/ocr`, response `{"pdf_content"}` — đúng contract mà
  `utils/batch_ocr_sync.py` đang đọc tại `result["pdf_content"]`).

---

## 1. Cấu trúc

```
ocr_service/
  mock_ocr_server.py   # FastAPI app (1 endpoint duy nhất, ~110 dòng)
```

Không cache, không DB, không file phụ: mọi request được resolve trực tiếp
từ đầu (đọc `.md` local hoặc canned trong RAM — vài ms).

---

## 2. Endpoint duy nhất

### `POST /step1/ocr`

**Request — Input Schema (`multipart/form-data`, đúng 1 field):**

| Field | Type | Required | Mô tả |
|---|---|---|---|
| `file` | `binary` (PDF) | **Có** | File `.pdf` cần OCR |

Validate: đuôi `.pdf` + non-empty, sai → `415` / `400`.

**Response — Output Schema (`application/json`, 200 OK, đúng 1 field):**

```json
{
  "pdf_content": "### BỘ NÔNG NGHIỆP VÀ MÔI TRƯỜNG\n\n## 1. Nội dung kiến nghị ..."
}
```

### Resolve `.md` (2 tầng, không cache)

```
PDF upload -> sha256(bytes) --+-- tầng 1: stem(filename) khớp file .md
                               |   thật trong data_markdown/
                               |   vd "an giang_106.pdf" -> ".../an giang_106.md"
                               |   -> đọc .md thật, trả về (log: file:<Tên Bộ>)
                               |
                               +-- tầng 2: không khớp gì -> trả canned mẫu
                                   S1/S2 trong code (ghi rõ MOCK, không giá trị
                                   pháp lý) (log: canned)
```

- Cùng bytes luôn ra cùng kết quả (logic là hàm thuần của input) — không cần
  cache mà vẫn deterministic.
- Hash (`document_id = "doc_" + 12 hex đầu SHA-256`, đúng quy tắc spec) dùng
  để **map input <-> output**: client đối chiếu file gửi với kết quả bằng cặp
  `(filename, sha)`; server log mỗi request:
  `[doc_<12hex>] tên.pdf (MB) -> file/canned, N chars, Nms`.

---

## 3. Cách chạy

```bash
# Từ trong folder ocr_service/
uvicorn mock_ocr_server:app --host 0.0.0.0 --port 8085
```

- Swagger UI: `http://localhost:8085/docs` (1 ô upload file duy nhất).
- Port 8085 trùng base URL đang hardcode trong `utils/batch_ocr.py`.

## 4. Trỏ consumer vào mock (1 dòng)

`utils/batch_ocr_sync.py` đang hardcode URL remote. Muốn test local thì đổi:

```python
OCR_URL = "http://localhost:8085/step1/ocr"
```

Lưu ý: `batch_ocr.py` bản mới (canonical JSON + `/v1/ocr/render`) **không**
dùng được mock này — mock chỉ phục vụ consumer đọc `pdf_content`
(`batch_ocr_sync.py`, `test/test_sync.py`, `pipeline.py` cũ).

## 5. Verify sau khi chạy

1. Swagger `/docs` → Try it out → upload 1 PDF trong `data/` → 200,
   `document_id` khớp SHA-256 file.
2. Upload file tên lạ (không có `.md` pair) → log `canned`, vẫn 200.
3. Chạy `utils/batch_ocr_sync.py` vào mock → ra `.md` trong output.

## 6. Giới hạn (không dùng production)

- Không OCR thật: nội dung trả về là `.md` có sẵn hoặc canned mẫu.
- Stateless: restart/xóa gì cũng như nhau, không có gì để invalidate.
