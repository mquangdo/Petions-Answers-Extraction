# -*- coding: utf-8 -*-
"""
Mock Standalone OCR Service — CHỈ dùng dev/test, KHÔNG chạy OCR thật.

- 1 endpoint duy nhất: POST /step1/ocr (khớp consumer utils/batch_ocr_sync.py,
  response đúng 1 field `pdf_content`).
- Request body: 1 file PDF (multipart `file`) + 2 form fields dummy
  `use_celery`/`log_dir` (giữ cho đủ contract pipeline.py::_ocr_pdf_async —
  mock nhận rồi bỏ qua).
- Map THUẦN NỘI DUNG: nhận PDF -> SHA-256 bytes -> tra bảng dựng sẵn
  hash_table.json ({sha256: markdown}, sinh bởi build_hash_table.py từ cặp
  PDF trong data/ <-> .md trong data_markdown/) -> HIT trả .md ngay,
  MISS (file lạ) trả canned mẫu. Tên file hoàn toàn vô nghĩa: đổi tên PDF
  vẫn ra đúng .md, PDF trùng nội dung chung 1 entry.
- Server read-only (không ghi disk runtime), stateless.

Chạy (từ trong folder ocr_service/):
    1. Dựng bảng 1 lần (lúc deploy / khi data đổi):
           python build_hash_table.py   # chạy từ repo root: python ocr_service/build_hash_table.py
    2. Chạy service:
           uvicorn mock_ocr_server:app --host 0.0.0.0 --port 8085
Swagger:
    http://localhost:8085/docs  (1 ô upload file duy nhất)
"""

import hashlib
import json
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

BASE_DIR = Path(__file__).resolve().parent
# Bảng {sha256(PDF bytes): nội dung .md} dựng sẵn bởi build_hash_table.py.
# Tái tạo được, vài MB -> gitignored, không commit. Thiếu file -> server vẫn
# chạy, mọi request rớt canned (log cảnh báo lúc startup).
HASH_TABLE_PATH = BASE_DIR / "hash_table.json"

# Markdown mẫu khi không pair được .md thật. Có sẵn mốc S1/S2 để tuyến
# regex/modules ăn được ngay. Ghi rõ là MOCK để không lẫn văn bản thật.
CANNED_MD = """### BỘ NÔNG NGHIỆP VÀ MÔI TRƯỜNG

# CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
Độc lập - Tự do - Hạnh phúc

Số: 0000/BNNMT-PC
V/v trả lời kiến nghị của cử tri (BẢN MOCK - KHÔNG CÓ GIÁ TRỊ PHÁP LÝ)

*Hà Nội, ngày 01 tháng 01 năm 2026*

## 1. Nội dung kiến nghị của cử tri (Kiến nghị số 0)

"Cử tri kiến nghị nội dung mẫu phục vụ kiểm thử (mock, không phải văn bản thật)."

## 2. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị

Nội dung mock: Bộ đã ghi nhận kiến nghị mẫu và sẽ trả lời chính thức bằng văn bản khác. Đoạn này do Mock OCR Service sinh ra để pipeline regex/modules có mốc S1/S2 để bóc tách.

Bộ Nông nghiệp và Môi trường trân trọng gửi Đoàn đại biểu Quốc hội để thông tin tới cử tri.

## BỘ TRƯỞNG

Nguyễn Văn Mock
"""

app = FastAPI(title="Mock OCR Service", version="0.3.0")


def _sha256(data: bytes) -> str:
    """SHA-256 hex của bytes file gốc — key duy nhất map input <-> output."""
    return hashlib.sha256(data).hexdigest()


def _load_table() -> dict:
    """Load bảng hash dựng sẵn (1 lần lúc import). Thiếu file -> dict rỗng."""
    try:
        with open(HASH_TABLE_PATH, encoding="utf-8") as fh:
            table = json.load(fh)
        print(f"[mock] load {len(table)} entry từ {HASH_TABLE_PATH}")
        return table
    except FileNotFoundError:
        print(
            f"[mock] CẢNH BÁO: chưa có {HASH_TABLE_PATH} "
            "(chạy python build_hash_table.py để dựng) — mọi request rớt canned."
        )
        return {}
    except (OSError, ValueError) as e:
        print(f"[mock] CẢNH BÁO: không đọc được bảng hash ({e}) — dùng canned.")
        return {}


HASH_TABLE = _load_table()


@app.post("/step1/ocr")
async def ocr_document(
    file: UploadFile = File(...),
    use_celery: str = Form("false"),
    log_dir: str = Form("string"),
):
    """Nhận 1 file PDF -> SHA-256 bytes -> tra bảng dựng sẵn -> trả ngay.

    Trả về duy nhất {"pdf_content": "<markdown>"} (khớp consumer cũ).
    Tên file vô nghĩa: đổi tên PDF vẫn ra đúng .md (map thuần nội dung).

    `use_celery` / `log_dir`: giữ cho đủ contract với pipeline.py::_ocr_pdf_async
    (gửi "false"/"string") — mock NHẬN RỒI BỎ QUA, chỉ log lại.
    """
    t0 = time.monotonic()
    filename = file.filename or "upload.pdf"
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"File '{filename}' rỗng.")

    # Validate BẢN CHẤT PDF (không đòi đuôi tên file): pipeline gửi file_id
    # trần (hash không đuôi, vd 'c3a69c91...') làm filename nên check đuôi sẽ
    # chặn oan 100% request. Thứ tự: magic bytes %PDF- trước, rồi tới
    # content-type do client khai báo, cuối cùng mới tới đuôi file.
    content_type = (file.content_type or "").lower()
    is_pdf = (
        data.startswith(b"%PDF-")
        or content_type == "application/pdf"
        or filename.lower().endswith(".pdf")
    )
    if not is_pdf:
        raise HTTPException(
            status_code=415,
            detail=f"File '{filename}' không đúng định dạng application/pdf.",
        )

    sha = _sha256(data)
    doc_id = f"doc_{sha[:12]}"

    md_text = HASH_TABLE.get(sha)
    via = "table" if md_text is not None else "canned"
    if md_text is None:
        md_text = CANNED_MD

    elapsed_ms = (time.monotonic() - t0) * 1000
    print(
        f"[{doc_id}] {filename} ({len(data) / 1048576:.1f}MB) "
        f"-> {via}, {len(md_text)} chars, {elapsed_ms:.0f}ms "
        f"(use_celery={use_celery} log_dir={log_dir})"
    )
    return {"pdf_content": md_text}


if __name__ == "__main__":
    # Cho phép: python ocr_service/mock_ocr_server.py  (chạy từ repo root,
    # tương đương lệnh uvicorn bên dưới; BASE_DIR dùng .resolve() nên đúng
    # mọi CWD).
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8085)


print(BASE_DIR)
