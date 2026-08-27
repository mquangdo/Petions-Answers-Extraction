# -*- coding: utf-8 -*-
"""
Sinh các request mẫu (multipart/form-data) — dùng DỮ LIỆU THẬT.

Nguồn dữ liệu:
  - Kiến nghị: đọc tất cả file extract/*.json, lấy field "noi_dung" từ
    "petitions" (kết quả trích xuất thật từ OCR).
  - File PDF: đọc từ folder data/*.pdf.
  - Ghép theo nguồn: file extract json có field "source_file" (vd
    "ha noi_24.md") -> PDF cùng tên trong data/ ("ha noi_24.pdf"). Kiến nghị
    và PDF của 1 sample luôn cùng nguồn nên matching có ý nghĩa (matched cao).

Đầu ra cho mỗi sample (trong thư mục output, mặc định request_samples/):
  - sample_NN.data_list.txt : chuỗi JSON của field `data_list` (dán thẳng vào
    ô data_list của Swagger hoặc -F "data_list=$(cat ...)")
  - sample_NN.file_ids.txt  : chuỗi JSON của field `file_ids`
  - sample_NN.files.txt     : danh sách đường dẫn PDF, mỗi dòng 1 file,
    theo ĐÚNG thứ tự index khớp file_ids[i] ↔ files[i]

Cách chạy:
    python gen_sample_request.py                          # mặc định: 1 nguồn/sample, 5 sample
    python gen_sample_request.py -k 3 -s 42
    python gen_sample_request.py --min-petitions 2        # chỉ chọn nguồn có >= 2 kiến nghị
"""

import argparse
import json
import random
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXTRACT_DIR = BASE_DIR / "extract"
DATA_DIR = BASE_DIR / "data"

DONVI_TIEPNHAN = [1, 3, 5, 7, 9, 11]
API_URL = "http://127.0.0.1:8005/api/v1/answer-matching"


def load_sources(extract_dir: Path, data_dir: Path, min_petitions: int) -> list[dict]:
    """
    Đọc tất cả extract/*.json, ghép với PDF CÙNG TÊN trong data/.

    Chỉ giữ lại nguồn có cặp .json (extract) + .pdf (data) khớp tên — đảm bảo
    mọi kiến nghị trong sample luôn có file PDF cùng nguồn để matching.

    Trả về list nguồn: [{"name", "pdf_path": str, "petitions": [noi_dung,...]}].
    """
    files = sorted(extract_dir.glob("*.json"))
    if not files:
        raise SystemExit(
            f"Không tìm thấy file .json nào trong '{extract_dir}'. "
            f"Hãy chạy pipeline extract trước."
        )

    sources = []
    missing_pdf = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))

        petitions = [
            (p.get("noi_dung") or "").strip()
            for p in data.get("petitions", [])
            if (p.get("noi_dung") or "").strip()
        ]
        if len(petitions) < min_petitions:
            continue

        # "ha noi_24.md" -> "ha noi_24.pdf" (đường dẫn TƯƠNG ĐỐI để portable)
        source_name = data.get("source_file") or f.stem
        pdf_name = Path(source_name).stem + ".pdf"
        pdf_path = data_dir / pdf_name

        if not pdf_path.exists():
            # Bỏ nguồn không có PDF cùng tên -> sample luôn đủ cặp json+pdf
            missing_pdf.append(pdf_name)
            continue

        sources.append(
            {
                "name": f.stem,
                "pdf_path": f"data/{pdf_name}",
                "petitions": petitions,
            }
        )

    if not sources:
        raise SystemExit(
            f"Không có nguồn nào trong '{extract_dir}' có >= {min_petitions} kiến nghị."
        )
    if missing_pdf:
        print(
            f"[CẢNH BÁO] {len(missing_pdf)} nguồn không tìm thấy PDF trong "
            f"'{data_dir}': {', '.join(sorted(missing_pdf))}"
        )
    return sources


def generate_sample(
    sources: list[dict],
    n_sources: int,
    start_id: int,
    rng: random.Random,
) -> dict:
    """
    Sinh 1 sample: chọn n nguồn, mỗi nguồn lấy toàn bộ petitions của nguồn đó
    kèm ĐÚNG file PDF tương ứng.

    Trả về {"data_list": [...], "file_ids": [...], "files": [...]}
    với files[i] là đường dẫn PDF của file_ids[i] (file_ids[i] ↔ files[i]).
    """
    chosen = rng.sample(sources, min(n_sources, len(sources)))

    data_list = []
    file_ids = []
    files = []

    next_id = start_id
    for k, src in enumerate(chosen):
        file_ids.append(f"FILE_{k + 1:03d}")
        files.append(src["pdf_path"])

        for noi_dung in src["petitions"]:
            data_list.append(
                {
                    "KN_KIENNGHI.ID": next_id,
                    "KN_KIENNGHI.DONVI_TIEPNHAN": DONVI_TIEPNHAN[next_id % len(DONVI_TIEPNHAN)],
                    "KN_KIENNGHI.NOI_DUNG": noi_dung,
                }
            )
            next_id += 1

    # Mọi nguồn đều có PDF cùng tên -> len(file_ids) == len(files) luôn khớp
    return {
        "data_list": data_list,
        "file_ids": file_ids,
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Sinh sample dữ liệu cho POST /api/v1/answer-matching: "
            "kiến nghị thật từ extract/*.json ghép đúng PDF cùng nguồn trong data/. "
            "Output chỉ gồm data_list + file_ids + files (không sinh script curl)."
        )
    )
    parser.add_argument(
        "-n", "--sources", type=int, default=1,
        help="Số nguồn (file văn bản) mỗi sample (mặc định: 1)",
    )
    parser.add_argument(
        "-k", "--samples", type=int, default=5,
        help="Số sample cần sinh (mặc định: 5)",
    )
    parser.add_argument(
        "--min-petitions", type=int, default=1,
        help="Chỉ chọn nguồn có ít nhất số kiến nghị này (mặc định: 1)",
    )
    parser.add_argument(
        "-o", "--out-dir", type=Path, default=Path("request_samples"),
        help="Thư mục output (mặc định: request_samples)",
    )
    parser.add_argument(
        "--start-id", type=int, default=1,
        help="KN_KIENNGHI.ID bắt đầu của sample đầu (mặc định: 1)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Seed random để sinh lặp lại được cùng dữ liệu",
    )
    args = parser.parse_args()

    # Console Windows mặc định cp1252 không in được tiếng Việt
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.sources < 1:
        parser.error("--sources phải >= 1")
    if args.samples < 1:
        parser.error("--samples phải >= 1")
    if args.min_petitions < 1:
        parser.error("--min-petitions phải >= 1")

    sources = load_sources(EXTRACT_DIR, DATA_DIR, args.min_petitions)

    n_with_pdf = sum(1 for s in sources if s["pdf_path"])
    print(
        f"Pool: {len(sources)} nguồn "
        f"({sum(len(s['petitions']) for s in sources)} kiến nghị, "
        f"{n_with_pdf} nguồn có PDF)"
    )

    rng = random.Random(args.seed)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    next_id = args.start_id
    for i in range(1, args.samples + 1):
        sample = generate_sample(sources, args.sources, next_id, rng)
        n_pets = len(sample["data_list"])
        next_id += n_pets

        sample_dir = args.out_dir / f"sample_{i:02d}"
        sample_dir.mkdir(parents=True, exist_ok=True)

        (sample_dir / "data_list.txt").write_text(
            json.dumps({"data_list": sample["data_list"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (sample_dir / "file_ids.txt").write_text(
            json.dumps(sample["file_ids"], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (sample_dir / "files.txt").write_text(
            "\n".join(sample["files"]) + ("\n" if sample["files"] else ""),
            encoding="utf-8",
        )

        src_names = ", ".join(Path(p).stem for p in sample["files"])
        print(
            f"[OK] {sample_dir.name}/  "
            f"({n_pets} kiến nghị, ID {next_id - n_pets}-{next_id - 1}, "
            f"{len(sample['file_ids'])} file: {src_names})"
        )

    print(f"\nHoàn tất: {args.samples} sample trong '{args.out_dir}/'.")


if __name__ == "__main__":
    main()
