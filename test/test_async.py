# -*- coding: utf-8 -*-
"""Gửi 3 file PDF lên OCR server bất đồng bộ (viết mới, không tái sử dụng code cũ).

- Tải 3 file .pdf từ downloaded/.
- Gửi 3 request tới OCR server đồng thời bằng asyncio.gather.
- Đo thời gian từng request và tổng thời gian.
"""
import asyncio
import time

import httpx

import concurrent.futures
from pathlib import Path

OCR_URL = "https://8078--main--dev--sinhnq3.coder.vts-ai.space/step1/ocr"

FILES = [
    "downloaded/ha noi_25.pdf"
]


def _read_pdf(path: str) -> tuple[str, bytes]:
    name = path.rsplit("/", 1)[-1]
    # Fallback tìm trong data/ nếu downloaded/ không tồn tại
    if not Path(path).exists() and Path(f"data/{name}").exists():
        path = f"data/{name}"
    with open(path, "rb") as f:
        return name, f.read()


async def _ocr_one(client: httpx.AsyncClient, name: str, pdf: bytes) -> str | None:
    start = time.monotonic()
    print(f"\n[+] Đang gửi: {name}")
    try:
        resp = await client.post(
            OCR_URL,
            files={"file": (name, pdf, "application/pdf")},
            data={"use_celery": "false", "log_dir": "string"},
        )
        elapsed = time.monotonic() - start

        print(f"[+] HTTP status: {resp.status_code}")
        print(f"[+] Client elapsed: {elapsed:.1f}s")

        if resp.status_code >= 400:
            print(f"[!] Server response:")
            print(resp.text)

        resp.raise_for_status()
        data = resp.json()
        md = data["pdf_content"].replace("\\n", "\n")

        server_elapsed = data.get("execution_time_seconds")
        extra = f", server_exec={server_elapsed:.1f}s" if server_elapsed is not None else ""
        print(f"[xong] {name}: client_elapsed={elapsed:.1f}s{extra}, {len(md)} chars")
        return md
    except Exception as e:
        print(f"\n[ERROR] {name}")
        print(f"Loại lỗi: {type(e).__name__}")
        print(f"Chi tiết: {e}")
        return None


async def main():
    payloads = [_read_pdf(p) for p in FILES]
    print(f"Gửi {len(payloads)} file lên OCR server bất đồng bộ...")

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=300) as client:
        results = await asyncio.gather(
            *[_ocr_one(client, name, pdf) for name, pdf in payloads],
            return_exceptions=True
        )
    total = time.monotonic() - t0

    success_count = sum(1 for r in results if isinstance(r, str))
    print(f"\nTỔNG: {total:.1f}s cho {len(payloads)} file ({success_count}/{len(payloads)} thành công)")
    if payloads:
        print(f"Trung bình: {total / len(payloads):.1f}s/file")
    return results


if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Nếu đang chạy trong môi trường có sẵn Event Loop (như Jupyter Notebook / IPython)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(asyncio.run, main()).result()
    else:
        asyncio.run(main())