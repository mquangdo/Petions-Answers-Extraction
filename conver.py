# -*- coding: utf-8 -*-
"""
Chuyển đổi tất cả PDF text-based trong 1 folder thành PDF scanned (image-only).

Usage:
    python convert.py <input_folder> [output_folder]

- Nếu không chỉ định output_folder → tạo folder "{input_folder}_scanned" cùng cấp.
- File scanned sẽ giữ nguyên tên gốc (không suffix _scanned).
"""

import sys
import shutil
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

from functions import is_text_based_pdf, convert_to_scanned_pdf


def main():
    if len(sys.argv) < 2:
        print("Usage: python convert.py <input_folder> [output_folder]")
        sys.exit(1)

    input_folder = Path(sys.argv[1])
    if not input_folder.is_dir():
        print(f"Loi: {input_folder} khong phai la folder")
        sys.exit(1)

    if len(sys.argv) >= 3:
        output_folder = Path(sys.argv[2])
    else:
        output_folder = input_folder.parent / f"{input_folder.name}_scanned"

    output_folder.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_folder.glob("*.pdf"))
    if not pdf_files:
        print(f"Khong tim thay file .pdf trong {input_folder}")
        sys.exit(0)

    print(f"Tim thay {len(pdf_files)} file PDF")
    print(f"Output: {output_folder}\n")

    text_based_count = 0
    scanned_count = 0
    skipped_count = 0
    error_count = 0

    for i, pdf_path in enumerate(pdf_files, 1):
        out_path = output_folder / pdf_path.name
        print(f"[{i}/{len(pdf_files)}] {pdf_path.name} ... ", end="", flush=True)

        try:
            if is_text_based_pdf(pdf_path):
                convert_to_scanned_pdf(pdf_path, output_path=out_path)
                text_based_count += 1
                print("converted (text-based -> scanned)")
            else:
                shutil.copy2(pdf_path, out_path)
                scanned_count += 1
                print("copied (da la scanned)")
        except Exception as e:
            error_count += 1
            print(f"ERROR: {e}")

    print(f"\n=== HOAN THANH ===")
    print(f"  Text-based da convert: {text_based_count}")
    print(f"  Scanned da copy:       {scanned_count}")
    print(f"  Loi:                   {error_count}")
    print(f"  Output: {output_folder}")


if __name__ == "__main__":
    main()
