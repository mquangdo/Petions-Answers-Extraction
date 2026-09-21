# -*- coding: utf-8 -*-
"""
OCR hàng loạt file .pdf từ input folder, lưu kết quả .md vào output folder.
Phiên bản ĐỒNG BỘ (sequential): xử lý từng file một, không dùng concurrency.

Sử dụng OCR server (sync, có retry + backoff).

Cách chạy:
    python batch_ocr_sync.py -i input_pdfs -o output_md
    python batch_ocr_sync.py -i input_pdfs -o output_md

Mặc định:
    - Chỉ OCR file chưa có .md tương ứng trong output (bỏ qua nếu đã có,
      trừ khi dùng --overwrite).
"""

import argparse
import json
import random
import sys
import time
from pathlib import Path

import httpx

from postprocess import _fix_ocr_diacritics, clean_footer

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OCR_URL = "https://8078--main--dev--sinhnq3.coder.vts-ai.space/step1/ocr"

JITTER = 1.0
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 3
RETRYABLE_CODES = {429, 500, 502, 503, 504}
OCR_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Core OCR logic
# ---------------------------------------------------------------------------
def _ocr_pdf_sync(
    filename: str,
    pdf_bytes: bytes,
    client: httpx.Client,
) -> str:
    """Gọi OCR server cho 1 file PDF (sync, có retry + backoff). Trả markdown."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.post(
                OCR_URL,
                files={
                    "file": (filename, pdf_bytes, "application/pdf")
                },
                data={
                    "use_celery": "false",
                    "log_dir": "string",
                },
            )

            if response.status_code in RETRYABLE_CODES:
                raise RuntimeError(
                    f"HTTP {response.status_code} (có thể bị chặn/quá tải)"
                )
            if response.status_code >= 400:
                raise RuntimeError(response.text)

            result = response.json()
            text = result["pdf_content"]
            return text.replace("\\n", "\n")

        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, JITTER)
            print(
                f"  [{filename}] Retry {attempt}/{MAX_RETRIES - 1} sau {wait:.1f}s "
                f"(lỗi: {e})"
            )
            time.sleep(wait)

    raise last_err


def _ocr_one_file(
    pdf_path: Path,
    out_path: Path,
    client: httpx.Client,
) -> tuple[Path, bool, str]:
    """OCR 1 file (tuần tự) rồi ghi markdown ra out_path."""
    try:
        pdf_bytes = pdf_path.read_bytes()
        md_text = _ocr_pdf_sync(pdf_path.name, pdf_bytes, client)
    except Exception as e:
        return pdf_path, False, str(e)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Post-OCR làm sạch markdown TRƯỚC khi lưu: chuẩn hóa ký tự tiếng Việt sai
    # + loại footer/chú thích cuối trang do OCR bóc vào giữa nội dung.
    md_text = _fix_ocr_diacritics(md_text)
    md_text = clean_footer(md_text)
    out_path.write_text(md_text, encoding="utf-8")
    return pdf_path, True, ""


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
def run(
    input_dir: Path,
    output_dir: Path,
    overwrite: bool,
) -> int:
    """OCR tất cả PDF trong input_dir (tuần tự). Trả về số file OCR THẤT BẠI."""
    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"Khong co file .pdf nao trong '{input_dir}'.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    tasks_spec = []
    skipped = []
    for pdf in pdf_files:
        out_md = output_dir / (pdf.stem + ".md")
        if out_md.exists() and not overwrite:
            skipped.append(pdf.name)
            continue
        tasks_spec.append((pdf, out_md))

    if skipped:
        print(f"Bo qua {len(skipped)} file da co .md (dung --overwrite de OCR lai):")
        for name in skipped:
            print(f"  - {name}")

    if not tasks_spec:
        print("Khong con file nao can OCR.")
        return 0

    print(
        f"OCR {len(tasks_spec)}/{len(pdf_files)} file (sequential) -> '{output_dir}/'\n"
    )

    failed = 0
    start_time = time.monotonic()

    with httpx.Client(timeout=OCR_TIMEOUT) as client:
        for pdf, out_md in tasks_spec:
            path, ok, err = _ocr_one_file(pdf, out_md, client)
            if ok:
                print(f"  [OK]   {path.name}")
            else:
                failed += 1
                print(f"  [FAIL] {path.name}: {err}")

    done = len(tasks_spec) - failed
    elapsed = time.monotonic() - start_time
    print(f"\nHoan tat: {done} thanh cong, {failed} that bai ({elapsed:.1f}s)")
    return failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OCR hang loat PDF -> Markdown (sequential, tuan tu)."
    )
    parser.add_argument(
        "-i", "--input-dir", type=Path, required=True,
        help="Folder chua file .pdf dau vao",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, required=True,
        help="Folder ghi ket qua .md dau ra",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="OCR lai ca nhung file da co .md trong output",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not args.input_dir.is_dir():
        raise SystemExit(f"Folder dau vao khong ton tai: '{args.input_dir}'")

    failed = run(args.input_dir, args.output_dir, args.overwrite)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()