# -*- coding: utf-8 -*-
"""
Trich xuat petition va metadata tu file .md trong input folder,
luu ket qua vao output folder.

Su dung ham extract_petitions() va extract_metadata() tu functions.py.

Cach chay:
    python batch_extract.py -i data_markdown -o extract_output
    python batch_extract.py -i data_markdown -o extract_output --overwrite

Moi file .md -> 1 file .json trong output folder, chua:
    {
        "file": "<ten_file>",
        "petitions": [...],
        "metadata": {...}
    }

Mac dinh:
    - Chi extract file chua co .json tuong ung trong output (bo qua neu da co,
      tru khi dung --overwrite).
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Them thu muc hien tai vao sys.path de import functions/regexes/postprocess
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from functions import extract_petitions, extract_metadata


def extract_one_file(md_path: Path) -> dict:
    """Doc 1 file .md, trich xuat petition + metadata. Tra ve dict ket qua."""
    text = md_path.read_text(encoding="utf-8")

    petitions = extract_petitions(text)
    metadata = extract_metadata(text)

    return {
        "file": md_path.stem,
        "petitions": petitions,
        "metadata": metadata,
    }


def run(
    input_dir: Path,
    output_dir: Path,
    overwrite: bool,
) -> int:
    """Extract tat ca .md trong input_dir. Trua ve so file THAT BAI."""
    md_files = sorted(input_dir.glob("*.md"))
    if not md_files:
        print(f"Khong co file .md nao trong '{input_dir}'.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    skipped = []
    for md in md_files:
        out_json = output_dir / (md.stem + ".json")
        if out_json.exists() and not overwrite:
            skipped.append(md.name)
            continue
        tasks.append((md, out_json))

    if skipped:
        print(f"Bo qua {len(skipped)} file da co .json (dung --overwrite de extract lai):")
        for name in skipped:
            print(f"  - {name}")

    if not tasks:
        print("Khong con file nao can extract.")
        return 0

    print(f"Extract {len(tasks)}/{len(md_files)} file -> '{output_dir}/'\n")

    failed = 0
    start_time = time.monotonic()
    total_petitions = 0

    for md_path, out_json in tasks:
        try:
            result = extract_one_file(md_path)
            out_json.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            n_pet = len(result["petitions"])
            total_petitions += n_pet
            print(f"  [OK]   {md_path.name}  ({n_pet} petition(s))")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {md_path.name}: {e}")

    elapsed = time.monotonic() - start_time
    done = len(tasks) - failed
    print(
        f"\nHoan tat: {done} thanh cong, {failed} that bai, "
        f"{total_petitions} petition(s) tong cong ({elapsed:.1f}s)"
    )
    return failed


def main():
    parser = argparse.ArgumentParser(
        description="Trich xuat petition & metadata tu file .md hang loat."
    )
    parser.add_argument(
        "-i", "--input-dir", type=Path, required=True,
        help="Folder chua file .md dau vao",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, required=True,
        help="Folder ghi ket qua .json dau ra",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Extract lai ca nhung file da co .json trong output",
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
