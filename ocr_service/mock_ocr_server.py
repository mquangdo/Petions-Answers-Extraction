# -*- coding: utf-8 -*-
"""
Mock Standalone OCR Service — CHỈ dùng dev/test, KHÔNG chạy OCR thật.

- 1 endpoint duy nhất: POST /step1/ocr (khớp consumer utils/batch_ocr_sync.py,
  response đúng 1 field `pdf_content`).
- Request body: duy nhất 1 file PDF (multipart field `file`).
- Stateless hoàn toàn (KHÔNG cache): nhận PDF -> SHA-256 bytes (để map
  input <-> output qua document_id + log) -> resolve .md trực tiếp
  (.md thật pair theo stem tên file trong data_markdown/, hoặc canned mẫu)
  -> trả {"pdf_content"} ngay.

Chạy (từ trong folder ocr_service/):
    uvicorn mock_ocr_server:app --host 0.0.0.0 --port 8085
Swagger:
    http://localhost:8085/docs  (1 ô upload file duy nhất)
"""

import hashlib
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

BASE_DIR = Path(__file__).resolve().parent
# Nguồn .md thật để pair theo stem tên file upload (vd "an giang_106.pdf"
# -> data_markdown/<Bộ>/an giang_106.md). Không có -> dùng canned.
MD_SOURCE_DIR = BASE_DIR.parent / "data_markdown"

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

app = FastAPI(title="Mock OCR Service", version="0.2.0")


def _sha256(data: bytes) -> str:
    """SHA-256 hex của bytes file gốc — map input <-> output qua document_id."""
    return hashlib.sha256(data).hexdigest()


def _find_real_md(stem: str) -> Path | None:
    """Tìm <stem>.md (không phân biệt hoa/thường) trong MD_SOURCE_DIR."""
    if not MD_SOURCE_DIR.is_dir():
        return None
    target = stem.lower()
    for sub in sorted(p for p in MD_SOURCE_DIR.iterdir() if p.is_dir()):
        for f in sub.iterdir():
            if f.is_file() and f.suffix.lower() == ".md" and f.stem.lower() == target:
                return f
    return None


@app.post("/step1/ocr")
async def ocr_document(file: UploadFile = File(...)):
    """Nhận 1 file PDF -> tính hash -> resolve .md trực tiếp -> trả ngay.

    Trả về duy nhất {"pdf_content": "<markdown>"} (khớp consumer cũ).
    """
    t0 = time.monotonic()
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=415,
            detail=f"File '{filename}' không đúng định dạng application/pdf.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail=f"File '{filename}' rỗng.")

    sha = _sha256(data)
    doc_id = f"doc_{sha[:12]}"

    src = _find_real_md(Path(filename).stem)
    if src is not None:
        md_text = src.read_text(encoding="utf-8")
        via = f"file:{src.parent.name}"
    else:
        md_text = CANNED_MD
        via = "canned"

    elapsed_ms = (time.monotonic() - t0) * 1000
    print(
        f"[{doc_id}] {filename} ({len(data) / 1048576:.1f}MB) "
        f"-> {via}, {len(md_text)} chars, {elapsed_ms:.0f}ms"
    )
    return {"pdf_content": md_text}
