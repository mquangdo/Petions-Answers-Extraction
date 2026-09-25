# Hướng dẫn Trích xuất Markdown từ File PDF sử dụng StandaloneOCR

Tài liệu này hướng dẫn chi tiết các phương pháp để chuyển đổi một file `.pdf` sang nội dung `.md` (Markdown) thông qua dịch vụ **Standalone OCR Service** (`/home/jovyan/scratch/hieunq10/StandaloneOCR`).

---

## 1. Tổng quan cơ chế tạo Markdown của StandaloneOCR

Dịch vụ StandaloneOCR **không** trả về chuỗi Markdown thô ngay ở bước OCR đầu tiên, mà tuân thủ kiến trúc phân tầng:
1. **Bước 1 (OCR Lõi):** Nhận file PDF, phân tích layout (Surya Qwen :8043), nhận dạng chữ, sửa chính tả (Gemma-4 :8076) và trả về định dạng cây phân cấp chuẩn **Canonical Tree JSON (`CanonicalOCRResponse`)**.
2. **Bước 2 (Derivation Rendering):** Dùng module `app/renderers/markdown.py` duyệt cây phân cấp (`tree`) và bảng biểu (`tables`) trong JSON để dựng thành chuỗi Markdown hoàn chỉnh với các thẻ Heading (`#`, `##`, `###`), danh sách, bảng Markdown, và chú thích chân trang (`[^1]`).

Có **3 cách chính** để lấy ra Markdown từ file PDF:
- **Cách 1 (Khuyên dùng qua API):** Gọi 2 bước qua HTTP API của StandaloneOCR (`/v1/ocr/documents` $\rightarrow$ `/v1/ocr/render`).
- **Cách 2 (Tiện lợi nhất qua Gateway Complaint-EC):** Gọi endpoint `/step1/ocr` ở port 8078 (hệ thống đã đóng gói sẵn 2 bước thành 1).
- **Cách 3 (Xử lý Python nội bộ không cần bật API Render):** Nhận JSON từ OCR và render trực tiếp bằng hàm Python của service.

---

## 2. Cách 1: Gọi trực tiếp StandaloneOCR API (Port 8085)

Quy trình chuẩn gồm 2 API requests:

### Bước 1: Gửi file PDF để lấy Canonical JSON
Gửi file PDF đến endpoint `/v1/ocr/documents`:

```bash
curl -X POST "http://localhost:8085/v1/ocr/documents" \
  -F "file=@/path/to/document.pdf" \
  -F "rasterize=true" \
  -F "enable_correction=true" \
  -F "process_table=true" \
  -F "use_celery=false" \
  -F "use_cache=false" \
  -o canonical_result.json
```

**Các tham số quan trọng:**
- `file`: File PDF cần trích xuất.
- `process_table=true`: Giữ cấu trúc bảng biểu để sinh bảng Markdown.
- `enable_correction=true`: Sửa lỗi dấu tiếng Việt qua LLM trước khi tạo Markdown.
- `use_celery=false`: Xử lý đồng bộ nhận kết quả ngay (nếu file nặng, đặt `true` hoặc dùng `/v1/ocr/tasks`).

---

### Bước 2: Render Canonical JSON sang Markdown
Gửi file `canonical_result.json` vừa nhận được đến endpoint `/v1/ocr/render`:

```bash
curl -X POST "http://localhost:8085/v1/ocr/render" \
  -H "Content-Type: application/json" \
  -d '{
    "canonical": '"$(cat canonical_result.json)"',
    "options": {
      "format": "markdown",
      "include_tables": true,
      "include_footnotes": true,
      "exclude_roles": ["appendix"],
      "include_headings": true
    }
  }' > render_result.json
```

Trích xuất chuỗi Markdown từ trường `content` trong JSON trả về:
```bash
# Lấy nội dung markdown ra file document.md
cat render_result.json | jq -r .content > document.md
```

**Các tùy chọn trong `options`:**
- `format`: `"markdown"` (bắt buộc).
- `include_tables`: `true` để render bảng biểu dưới dạng Markdown Table (`| Cột 1 | Cột 2 |`).
- `include_footnotes`: `true` để xuất chú thích dạng `[^1]: nội dung`.
- `exclude_roles`: Danh sách semantic role muốn loại bỏ, ví dụ: `["appendix"]` (cắt phụ lục).

---

### Script Python tự động 2 bước (Full Example):

```python
import requests

OCR_BASE_URL = "http://localhost:8085"

def pdf_to_markdown_via_standalone_ocr(pdf_path: str, output_md_path: str) -> str:
    # 1. Gọi OCR lấy Canonical JSON
    with open(pdf_path, "rb") as f:
        ocr_res = requests.post(
            f"{OCR_BASE_URL}/v1/ocr/documents",
            files={"file": (pdf_path, f, "application/pdf")},
            data={
                "rasterize": "true",
                "enable_correction": "true",
                "process_table": "true",
                "use_celery": "false",
                "use_cache": "false",
            },
            timeout=300,
        )
    ocr_res.raise_for_status()
    canonical_data = ocr_res.json()

    # 2. Gọi API Render chuyển sang Markdown
    render_res = requests.post(
        f"{OCR_BASE_URL}/v1/ocr/render",
        json={
            "canonical": canonical_data,
            "options": {
                "format": "markdown",
                "include_tables": True,
                "include_footnotes": True,
                "exclude_roles": ["appendix"],  # Bỏ phụ lục nếu cần
                "include_headings": True,
            },
        },
        timeout=60,
    )
    render_res.raise_for_status()
    
    markdown_content = render_res.json().get("content", "")
    
    # Lưu ra file .md
    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    print(f"Đã lưu markdown vào: {output_md_path}")
    return markdown_content

if __name__ == "__main__":
    pdf_to_markdown_via_standalone_ocr("sample.pdf", "sample.md")
```

---

## 3. Cách 2: Gọi qua Gateway Complaint-EC (Port 8078 - Gộp 1 bước)

Trong hệ thống (`/home/jovyan/scratch/hieunq10/Complaint-EC`), service gateway đã bọc sẵn toàn bộ quá trình gọi StandaloneOCR và render Markdown thành **1 endpoint duy nhất**:

* **Endpoint:** `POST http://localhost:8078/step1/ocr`
* **cURL lấy Markdown trực tiếp:**

```bash
curl -X POST "http://localhost:8078/step1/ocr" \
  -F "file=@/path/to/document.pdf" \
  -F "use_celery=false" \
  -F "process_table=true" \
  -F "keep_phu_luc=false" \
  | jq -r .pdf_content > output.md
```

* **Kết quả trả về:** JSON có trường `pdf_content` chính là chuỗi Markdown hoàn chỉnh.

---

## 4. Cách 3: Tự Render trong Python bằng Renderer nội bộ của StandaloneOCR

Nếu bạn đang viết code Python cùng môi trường với StandaloneOCR và đã có `CanonicalOCRResponse` từ model, bạn có thể render Markdown mà không cần gọi thêm HTTP request:

```python
import sys
from pathlib import Path

# Thêm đường dẫn tới StandaloneOCR
sys.path.append("/home/jovyan/scratch/hieunq10/StandaloneOCR")

from app.schemas.canonical import CanonicalOCRResponse
from app.schemas.api import RenderOptions
from app.renderers.markdown import render_to_markdown

# 1. canonical_obj là instance của CanonicalOCRResponse hoặc load từ json:
# canonical_obj = CanonicalOCRResponse.model_validate(json_data)

# 2. Thiết lập tùy chọn render
options = RenderOptions(
    format="markdown",
    include_tables=True,
    include_footnotes=True,
    exclude_roles=["appendix"]
)

# 3. Render trực tiếp ra chuỗi Markdown
markdown_text = render_to_markdown(canonical_obj, options=options)

with open("output.md", "w", encoding="utf-8") as f:
    f.write(markdown_text)
```

---

## 5. Bảng tổng kết so sánh các cách

| Phương pháp | Endpoint / Thư viện | Ưu điểm | Nhược điểm |
|---|---|---|---|
| **API StandaloneOCR (Cách 1)** | `POST /v1/ocr/documents`<br>+ `POST /v1/ocr/render` | Tùy biến sâu bộ lọc (bỏ bảng, bỏ footnote, bỏ role), chuẩn kiến trúc microservice | Cần gọi 2 HTTP requests |
| **API Gateway (Cách 2)** | `POST /step1/ocr` (port 8078) | Gọn nhẹ nhất (1 request duy nhất ra ngay Markdown), tự động xử lý cắt phụ lục | Phụ thuộc service trung gian Complaint-EC |
| **Python Renderer (Cách 3)** | `app.renderers.markdown.render_to_markdown` | Nhanh nhất khi xử lý offline/batch, không qua mạng HTTP | Đòi hỏi import mã nguồn StandaloneOCR trong Python |
