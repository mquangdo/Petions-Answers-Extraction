# -*- coding: utf-8 -*-
"""
OCR hàng loạt file .pdf từ input folder, lưu kết quả .md vào output folder.

Sử dụng Standalone OCR mới (Canonical Tree JSON v1.0.0 — xem
docs/standalone_ocr_api_specs.md), async, có retry + backoff + concurrency
control. Hướng A: dựng lại markdown CÓ CẤU TRÚC (#, **) qua
POST /v1/ocr/render để tuyến regex/modules phía sau ăn được; render lỗi
-> fallback về text thuần "content" (không bao giờ tệ hơn trước).

Cách chạy:
    python batch_ocr.py -i input_pdfs -o output_md
    python batch_ocr.py -i input_pdfs -o output_md -c 5
    

Mặc định:
    - Chỉ OCR file chưa có .md tương ứng trong output (bỏ qua nếu đã có,
      trừ khi dùng --overwrite).
"""

import argparse
import asyncio
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
# Standalone OCR mới (Canonical Tree JSON v1.0.0):
#   1. POST /v1/ocr/documents -> canonical JSON (field "content" là text thuần,
#      KHÔNG có marker markdown).
#   2. POST /v1/ocr/render (format=markdown) -> markdown có cấu trúc (#, **).
OCR_URL = "http://localhost:8085/v1/ocr/documents"
RENDER_URL = "http://localhost:8085/v1/ocr/render"

JITTER = 1.0
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 3
RETRYABLE_CODES = {429, 500, 502, 503, 504}
DEFAULT_CONCURRENCY = 4
OCR_TIMEOUT = 300
RENDER_TIMEOUT = 60      # giây; render không chạy lại OCR nên nhanh
RENDER_MAX_RETRIES = 3   # số lần thử lại cho bước render


# ---------------------------------------------------------------------------
# Core OCR logic
# ---------------------------------------------------------------------------
async def _ocr_pdf_async(
    filename: str,
    pdf_bytes: bytes,
    client: httpx.AsyncClient,
) -> dict:
    """Gọi /v1/ocr/documents cho 1 file PDF (async, có retry + backoff).

    Trả về nguyên dict canonical JSON (schema v1.0.0). Field "content" bên
    trong là text thuần (không marker) — bước render sau sẽ dựng markdown
    có cấu trúc từ dict này.
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.post(
                OCR_URL,
                files={
                    "file": (filename, pdf_bytes, "application/pdf")
                },
                data={
                    "rasterize": "true",
                    "enable_correction": "false",
                    "process_table": "false",
                    "use_celery": "true",
                    "use_cache": "false",
                },
            )

            if response.status_code in RETRYABLE_CODES:
                raise RuntimeError(
                    f"HTTP {response.status_code} (có thể bị chặn/quá tải)"
                )
            if response.status_code >= 400:
                raise RuntimeError(response.text)

            result = response.json()
            if not isinstance(result, dict) or "content" not in result:
                raise ValueError(
                    "Response /v1/ocr/documents thiếu field 'content'"
                )
            return result

        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, JITTER)
            print(
                f"  [{filename}] Retry {attempt}/{MAX_RETRIES - 1} sau {wait:.1f}s "
                f"(lỗi: {e})"
            )
            await asyncio.sleep(wait)

    raise last_err


def _has_structure(text: str) -> bool:
    """Markdown có cấu trúc không (dòng heading '#' hoặc marker '**')."""
    if "**" in text:
        return True
    return any(line.lstrip().startswith("#") for line in text.splitlines())


async def _render_markdown_async(
    filename: str,
    canonical: dict,
    client: httpx.AsyncClient,
) -> str:
    """Gọi /v1/ocr/render dựng markdown có cấu trúc từ canonical JSON.

    Render không chạy lại OCR nên nhanh (timeout riêng ngắn). Trả về
    markdown (literal \\n đã đổi thành newline thật). Hết retry mà vẫn
    lỗi -> ném exception để caller fallback về canonical["content"].
    """
    last_err = None
    for attempt in range(1, RENDER_MAX_RETRIES + 1):
        try:
            response = await client.post(
                RENDER_URL,
                json={
                    "canonical": canonical,
                    "options": {
                        "format": "markdown",
                        "include_tables": True,
                        "include_footnotes": True,
                    },
                },
                timeout=RENDER_TIMEOUT,
            )

            if response.status_code in RETRYABLE_CODES:
                raise RuntimeError(
                    f"HTTP {response.status_code} (có thể bị chặn/quá tải)"
                )
            if response.status_code >= 400:
                raise RuntimeError(response.text)

            result = response.json()
            text = result.get("content") or ""
            if not text.strip():
                raise ValueError("Response /v1/ocr/render trả content rỗng")
            return text.replace("\\n", "\n")

        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            last_err = e
            if attempt == RENDER_MAX_RETRIES:
                break
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, JITTER)
            print(
                f"  [{filename}] Render retry {attempt}/{RENDER_MAX_RETRIES - 1} "
                f"sau {wait:.1f}s (lỗi: {e})"
            )
            await asyncio.sleep(wait)

    raise last_err


async def _ocr_one_file(
    pdf_path: Path,
    out_path: Path,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> tuple[Path, bool, str]:
    """OCR 1 file (giới hạn concurrency), render markdown rồi ghi ra out_path."""
    async with semaphore:
        try:
            pdf_bytes = pdf_path.read_bytes()
            canonical = await _ocr_pdf_async(pdf_path.name, pdf_bytes, client)
            try:
                md_text = await _render_markdown_async(
                    pdf_path.name, canonical, client
                )
                if not _has_structure(md_text):
                    raise ValueError(
                        "Markdown render thiếu marker cấu trúc (#/**)"
                    )
                via = "render"
            except Exception as e:
                # Fallback an toàn: text thuần (không marker) — không tệ hơn
                # hành vi lấy thẳng canonical["content"].
                print(
                    f"  [{pdf_path.name}] Render thất bại ({e}) "
                    "-> fallback content thuần."
                )
                md_text = (canonical.get("content") or "").replace("\\n", "\n")
                via = "fallback"
        except Exception as e:
            return pdf_path, False, str(e)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Post-OCR làm sạch markdown TRƯỚC khi lưu: chuẩn hóa ký tự tiếng Việt sai
    # + loại footer/chú thích cuối trang do OCR bóc vào giữa nội dung.
    md_text = _fix_ocr_diacritics(md_text)
    md_text = clean_footer(md_text)
    out_path.write_text(md_text, encoding="utf-8")
    print(f"  [{via}] {pdf_path.name}")
    return pdf_path, True, ""


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------
async def run(
    input_dir: Path,
    output_dir: Path,
    concurrency: int,
    overwrite: bool,
) -> int:
    """OCR tất cả PDF trong input_dir. Trả về số file OCR THẤT BẠI."""
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
        f"OCR {len(tasks_spec)}/{len(pdf_files)} file "
        f"(concurrency={concurrency}) -> '{output_dir}/'\n"
    )

    semaphore = asyncio.Semaphore(concurrency)
    failed = 0
    start_time = time.monotonic()

    async with httpx.AsyncClient(timeout=OCR_TIMEOUT) as client:
        results = await asyncio.gather(
            *[
                _ocr_one_file(pdf, out_md, client, semaphore)
                for pdf, out_md in tasks_spec
            ]
        )

    for path, ok, err in results:
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
        description="OCR hang loat PDF -> Markdown (async, concurrency control)."
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
        "-c", "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
        help=f"So file OCR dong thoi toi da (mac dinh: {DEFAULT_CONCURRENCY})",
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
    if args.concurrency < 1:
        parser.error("--concurrency phai >= 1")

    failed = asyncio.run(
        run(args.input_dir, args.output_dir, args.concurrency, args.overwrite)
    )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()