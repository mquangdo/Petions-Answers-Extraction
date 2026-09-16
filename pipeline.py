# -*- coding: utf-8 -*-
"""
Pipeline xử lý dùng chung cho Answer Matching v2.

Chứa toàn bộ logic nghiệp vụ: đọc PDF từ volume dùng chung -> OCR -> trích
xuất cặp noi_dung/tra_loi -> map bằng Jaccard. Được 2 nơi tái sử dụng:
  - Celery worker  process_answer_matching       (run_pipeline, sync wrapper)

Input:
    data_list   : list dict theo alias ("KN_KIENNGHI.ID", ...) — đúng định
                  dạng message trong queue.
    file_paths  : list đường dẫn PDF trên volume dùng chung, mapping 1-1 theo
                  index với file_ids.
    file_ids    : list định danh file do HTTT cung cấp, giữ nguyên khi trả kết quả.

Output: dict đúng response schema (answers[].file_id thay cho hashFile).
"""

import asyncio
import concurrent.futures
import random
import re
import time
import unicodedata

import httpx

from functions import extract_petitions, extract_metadata
from postprocess import _fix_ocr_diacritics, clean_footer


OCR_URL = "https://8078--main--dev--sinhnq3.coder.vts-ai.space/step1/ocr"

JITTER = 1.0            # jitter ngẫu nhiên (giây) thêm vào delay retry
MAX_RETRIES = 5         # số lần thử lại tối đa cho 1 file
RETRY_BACKOFF_BASE = 1  # giây, nhân đôi sau mỗi lần thử lại
RETRYABLE_CODES = {429, 500, 502, 503, 504}
MAX_CONCURRENCY = 3     # số file PDF OCR đồng thời tối đa
OCR_TIMEOUT = 300       # giây, cho mỗi request OCR
JACCARD_THRESHOLD = 0.5


async def _ocr_pdf_async(
    filename: str,
    pdf_bytes: bytes,
    client: httpx.AsyncClient,
) -> str:
    """Gọi OCR server cho 1 file PDF (async, có retry + backoff). Trả markdown."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.post(
                OCR_URL,
                files={
                    "file": (
                        filename,
                        pdf_bytes,
                        "application/pdf",
                    )
                },
                data={
                    "use_celery": "false",
                    "log_dir": "string",
                },
            )

            print(f"  [{filename}] Status code: {response.status_code}")

            if response.status_code in RETRYABLE_CODES:
                raise RuntimeError(
                    f"HTTP {response.status_code} (có thể bị chặn/quá tải)"
                )
            if response.status_code >= 400:
                raise RuntimeError(response.text)

            result = response.json()
            text = result["pdf_content"]

            # Chuyển literal \n thành newline thật
            return text.replace("\\n", "\n")

        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            last_err = e
            if attempt == MAX_RETRIES:
                break
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, JITTER)
            print(
                f"  [{filename}] Thử lại {attempt}/{MAX_RETRIES - 1} sau {wait:.1f}s "
                f"(lỗi: {e})"
            )
            await asyncio.sleep(wait)

    raise last_err


def _normalize(text: str) -> set[str]:
    """Chuẩn hóa text: chữ thường, bỏ dấu tiếng Việt, giữ token chữ/số."""
    text = (text or "").lower()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return set(re.findall(r"[a-z0-9]+", text))


def _jaccard(a: str, b: str) -> float:
    """Jaccard index của 2 chuỗi dựa trên set token."""
    sa = _normalize(a)
    sb = _normalize(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _read_pdf(path: str) -> bytes:
    """Đọc bytes PDF từ volume dùng chung."""
    with open(path, "rb") as f:
        return f.read()


async def _ocr_extract_one(
    filename: str,
    pdf_bytes: bytes,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> dict:
    """OCR 1 file (giới hạn concurrency), extract petitions + metadata."""
    async with semaphore:
        md_text = await _ocr_pdf_async(filename, pdf_bytes, client)
    # Post-OCR làm sạch trước khi trích xuất (OCR bóc footer/chú thích vào nội dung)
    md_text = _fix_ocr_diacritics(md_text)
    md_text = clean_footer(md_text)
    # Chạy extract_petitions trong thread riêng để nếu rơi vào fallback LLM (_extract_llm),
    # lệnh asyncio.run() bên trong llm_chunking không bị xung đột với event loop hiện tại.
    petitions = await asyncio.to_thread(extract_petitions, md_text)
    return {
        "petitions": petitions,
        "metadata": extract_metadata(md_text),
    }


async def run_pipeline_async(
    data_list: list[dict],
    file_paths: list[str],
    file_ids: list[str],
) -> dict:
    """
    Luồng xử lý đầy đủ cho 1 job:
      1. Đọc tất cả file PDF từ disk (1 file lỗi -> lỗi cả job)
      2. OCR + extract từng file (giới hạn MAX_CONCURRENCY)
      3. Gom cặp {noi_dung, tra_loi, file_id, metadata}
      4. Map từng kiến nghị -> câu trả lời Jaccard cao nhất (>= threshold)
    Trả dict đúng response schema.
    """
    start_time = time.monotonic()

    # 1) Đọc tất cả file PDF từ disk (sync tuần tự — đọc disk nhanh,
    #    KHÔNG dùng asyncio.gather vì _read_pdf là hàm thường trả bytes,
    #    không phải coroutine)
    reads = []
    read_errors = []
    for path in file_paths:
        try:
            reads.append(_read_pdf(path))
        except Exception as e:
            read_errors.append(f"{path}: {e}")
    if read_errors:
        raise RuntimeError(
            f"Đọc file thất bại {len(read_errors)}/{len(file_paths)}: "
            f"{'; '.join(read_errors)}"
        )

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(timeout=OCR_TIMEOUT) as client:
        # 2) OCR + extract từng file
        tasks = [
            _ocr_extract_one(
                file_id,
                pdf_bytes,
                client,
                semaphore,
            )
            for file_id, pdf_bytes in zip(file_ids, reads)
        ]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    # Lỗi tổng: 1 file lỗi -> lỗi cả job
    ok_ids = list(file_ids)
    errors = [
        f"{fid}: {err}"
        for fid, err in zip(ok_ids, outcomes)
        if isinstance(err, Exception)
    ]
    if errors:
        raise RuntimeError(
            f"OCR/trích xuất thất bại {len(errors)}/{len(tasks)} file: "
            f"{'; '.join(errors)}"
        )

    # 3) Gom cặp {noi_dung, tra_loi} kèm file_id + metadata.
    pairs = []
    for file_id, pdf_bytes, result in zip(file_ids, reads, outcomes):
        if not isinstance(result, dict):
            continue

        for p in result["petitions"]:
            pairs.append(
                (
                    p["noi_dung"],
                    p["tra_loi"],
                    file_id,
                    result["metadata"],
                )
            )

    # 4) Map từng kiến nghị đầu vào -> câu trả lời có Jaccard cao nhất
    results = []
    matched = 0

    for item in data_list:
        noi_dung_kn = item["KN_KIENNGHI.NOI_DUNG"]
        if not pairs:
            answers = []
        else:
            best = max(
                pairs,
                key=lambda pr: _jaccard(noi_dung_kn, pr[0]),
            )

            best_score = _jaccard(noi_dung_kn, best[0])

            if best_score >= JACCARD_THRESHOLD:
                answers = [
                    {
                        "content": best[1],
                        "file_id": best[2],
                        "metadata": best[3],
                    }
                ]
                matched += 1
            else:
                answers = []

        results.append(
            {
                "KN_KIENNGHI.ID": item["KN_KIENNGHI.ID"],
                "answers": answers,
            }
        )

    try:
        print(
            f"  [pipeline] {len(data_list)} kiến nghị, "
            f"{len(file_ids)} file, {len(pairs)} cặp trích xuất, "
            f"matched={matched}, elapsed={time.monotonic() - start_time:.2f}s"
        )
    except UnicodeEncodeError:
        pass

    return {
        "data_list": results,
        "metadata_all": {
            "matched_count": matched,
            "unmatched_count": len(data_list) - matched,
        },
    }


def run_pipeline(
    data_list: list[dict],
    file_paths: list[str],
    file_ids: list[str],
) -> dict:
    """Wrapper sync cho Celery worker (worker là sync, pipeline là async)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Đang trong event loop (gevent/eventlet pool) → chạy async task
        # trong thread riêng để tránh conflict
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run, run_pipeline_async(data_list, file_paths, file_ids)
            ).result()
    else:
        return asyncio.run(run_pipeline_async(data_list, file_paths, file_ids))
