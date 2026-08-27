"""Sinh presigned URL cho các file trong bucket MinIO local.

Kịch bản mô phỏng HTTT gửi danh sách presigned URL (như input_schema.json):
  - Kết nối MinIO local (container minio_petitions, port 9000).
  - Sinh presigned GET URL cho từng file trong bucket.
  - Ghi ra presigned_urls.json: {filename: presigned_url}.

Sau đó thử download từng URL (giống _download_pdf trong extract_api_server_v2)
và so sánh kích thước với file gốc trong data/.
"""
import json
import os
from datetime import timedelta
from pathlib import Path
import httpx
from minio import Minio

MINIO_ENDPOINT = "9002--main--dev--sinhnq3.coder.vts-ai.space"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin123"
BUCKET = "petitions-answers"
EXPIRES_SECONDS = 14400 # 30 phút (khuyến nghị trong Mo_ta_API.md)

BASE_DIR = Path(__file__).resolve().parent
URLS_FILE = BASE_DIR / "presigned_urls.json"
DOWNLOAD_DIR = BASE_DIR / "downloaded"
DATA_DIR = BASE_DIR / "data"


def list_bucket_files(client: Minio) -> list[str]:
    files = []
    for obj in client.list_objects(BUCKET, recursive=True):
        if obj.object_name.endswith(".pdf"):
            files.append(obj.object_name)
    return sorted(files)


def generate_presigned_urls(client: Minio, filenames: list[str]) -> dict:
    urls = {}
    for name in filenames:
        urls[name] = client.presigned_get_object(
            BUCKET, name, expires=timedelta(seconds=EXPIRES_SECONDS)
        )
    return urls


def download_and_check(urls: dict) -> None:
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    for name, url in urls.items():
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.get(url)
            if resp.status_code != 200:
                print(f"  [FAIL] {name}: HTTP {resp.status_code}")
                continue
            target = DOWNLOAD_DIR / name
            target.write_bytes(resp.content)
            got = resp.content.__len__()
            original = DATA_DIR / name
            orig_size = original.stat().st_size if original.exists() else None
            match = "OK" if (orig_size is not None and orig_size == got) else "SIZE-MISMATCH"
            print(
                f"  [OK]   {name}: {got} bytes "
                f"(gốc: {orig_size if orig_size is not None else 'N/A'}) [{match}]"
            )
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")


def main():
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=True,
    )
    if not client.bucket_exists(BUCKET):
        print(f"Bucket {BUCKET} không tồn tại!")
        return

    filenames = list_bucket_files(client)
    print(f"File trong bucket {BUCKET}: {len(filenames)}")
    for f in filenames:
        print(f"  - {f}")

    urls = generate_presigned_urls(client, filenames)
    URLS_FILE.write_text(
        json.dumps(urls, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nĐã ghi {len(urls)} presigned URL vào {URLS_FILE}")

    print("\nThử download từng URL:")
    download_and_check(urls)


if __name__ == "__main__":
    main()