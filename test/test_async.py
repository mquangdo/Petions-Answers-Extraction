# -*- coding: utf-8 -*-
"""Gửi 3 file PDF lên OCR server bất đồng bộ (viết mới, không tái sử dụng code cũ).

- Tải 3 file .pdf từ downloaded/.
- Gửi 3 request tới OCR server đồng thời bằng asyncio.gather.
- Đo thời gian từng request và tổng thời gian.
"""
import asyncio
import time

import httpx

OCR_URL = "https://8078--main--dev--sinhnq3.coder.vts-ai.space/step1/ocr"

FILES = [
    "downloaded/ha noi_25.pdf"
]


def _read_pdf(path: str) -> tuple[str, bytes]:
    name = path.rsplit("/", 1)[-1]
    with open(path, "rb") as f:
        return name, f.read()


async def _ocr_one(client: httpx.AsyncClient, name: str, pdf: bytes) -> str:
    start = time.monotonic()
    resp = await client.post(
        OCR_URL,
        files={"file": (name, pdf, "application/pdf")},
        data={"use_celery": "false", "log_dir": "string"},
    )
    resp.raise_for_status()
    data = resp.json()
    md = data["pdf_content"].replace("\\n", "\n")
    elapsed = time.monotonic() - start
    server_elapsed = data.get("execution_time_seconds")
    extra = f", server_exec={server_elapsed:.1f}s" if server_elapsed is not None else ""
    print(f"  [xong] {name}: client_elapsed={elapsed:.1f}s{extra}, {len(md)} chars")
    return md


async def main():
    payloads = [_read_pdf(p) for p in FILES]
    print(f"Gửi {len(payloads)} file lên OCR server bất đồng bộ...")

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=300) as client:
        results = await asyncio.gather(
            *[_ocr_one(client, name, pdf) for name, pdf in payloads]
        )
    total = time.monotonic() - t0

    print(f"\nTỔNG: {total:.1f}s cho {len(payloads)} file "
          f"(trung bình {total / len(payloads):.1f}s/file)")
    return results


if __name__ == "__main__":
    asyncio.run(main())