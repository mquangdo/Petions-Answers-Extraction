"""
Gửi PDF lên OCR server bằng request đồng bộ.

- Đọc file PDF từ downloaded/
- Gửi từng request tuần tự
- Đo thời gian request
"""

import time

import httpx


OCR_URL = "https://8078--main--dev--sinhnq3.coder.vts-ai.space/step1/ocr"

FILES = [
    "downloaded/ha noi_25.pdf"
]


def read_pdf(path: str) -> tuple[str, bytes]:
    name = path.rsplit("/", 1)[-1]

    with open(path, "rb") as f:
        return name, f.read()


def ocr_one(client: httpx.Client, name: str, pdf: bytes) -> str:
    start = time.monotonic()

    print(f"\n[+] Đang gửi: {name}")

    response = client.post(
        OCR_URL,
        files={
            "file": (
                name,
                pdf,
                "application/pdf",
            )
        },
        data={
            "use_celery": "false",
            "log_dir": "string",
        },
    )

    elapsed = time.monotonic() - start

    print(f"[+] HTTP status: {response.status_code}")
    print(f"[+] Client elapsed: {elapsed:.1f}s")

    # In response nếu server trả lỗi
    if response.status_code >= 400:
        print("[!] Server response:")
        print(response.text)

    response.raise_for_status()

    data = response.json()

    md = data["pdf_content"].replace("\\\n", "\n")

    server_elapsed = data.get("execution_time_seconds")

    if server_elapsed is not None:
        print(f"[+] Server execution: {server_elapsed:.1f}s")

    print(f"[xong] {name}: {len(md)} chars")

    return md


def main():
    payloads = [
        read_pdf(path)
        for path in FILES
    ]

    print(
        f"Gửi {len(payloads)} file lên OCR server "
        f"bằng request đồng bộ..."
    )

    total_start = time.monotonic()

    results = []

    with httpx.Client(timeout=300) as client:

        for name, pdf in payloads:
            try:
                result = ocr_one(
                    client,
                    name,
                    pdf,
                )

                results.append(result)

            except Exception as e:
                print(f"\n[ERROR] {name}")
                print(f"Loại lỗi: {type(e).__name__}")
                print(f"Chi tiết: {e}")

    total = time.monotonic() - total_start

    print(
        f"\nTỔNG: {total:.1f}s cho "
        f"{len(payloads)} file"
    )

    if payloads:
        print(
            f"Trung bình: "
            f"{total / len(payloads):.1f}s/file"
        )

    return results


if __name__ == "__main__":
    main()