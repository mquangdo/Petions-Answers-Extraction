# -*- coding: utf-8 -*-
"""
test_pipeline.py

Script kiểm thử toàn diện pipeline trích xuất Answer Matching từ một thư mục
chứa các file Markdown (.md), lưu kết quả ra thư mục output dưới dạng các file .json
cùng tên với file .md (tuân thủ định dạng chuẩn của folder extract_answer_result).

Cấu trúc mỗi file .json đầu ra:
{
  "file": "<tên_file_không_đuôi>",
  "petitions": [
    {
      "noi_dung": "<nội dung kiến nghị cử tri>",
      "tra_loi": "<nội dung cơ quan trả lời>"
    }
  ],
  "metadata": {
    "so_cong_van": "...",
    "ngay_ban_hanh": "DD/MM/YYYY",
    "nguoi_ky": "..."
  }
}

Luồng thực thi:
  1. Quét toàn bộ file .md trong thư mục đầu vào (-i).
  2. Bỏ qua các file đã có .json tương ứng trong thư mục đầu ra (-o), trừ khi dùng --overwrite.
  3. Với từng file:
     - Làm sạch text: _fix_ocr_diacritics, clean_footer
     - Trích xuất ID Đoàn ĐBQH: extract_donvi_id_from_text
     - Định tuyến theo Bộ (Ministry Router): nhận diện tên Bộ, nạp module chuyên biệt
       (hoặc fallback LLM qua vLLM/LiteLLM port 4000 nếu văn bản dị biệt)
     - Trích xuất Metadata (Số CV, Ngày ban hành, Người ký)
     - Trích xuất các cặp {noi_dung, tra_loi}
     - Ghi ngay file <tên_file>.json ra thư mục output
  4. In báo cáo tổng hợp chi tiết ra màn hình.

Cách sử dụng:
  # 1. Trích xuất toàn bộ thư mục md sang thư mục json:
  python test_pipeline.py -i "data_markdown_new/Bộ Nội vụ new" -o "extract_answer_result/Bộ Nội vụ new"

  # 2. Ghi đè các file json đã tồn tại:
  python test_pipeline.py -i "data_markdown_new/Bộ Nội vụ new" -o "extract_answer_result/Bộ Nội vụ new" --overwrite

  # 3. Chạy test nhanh 5 file đầu tiên:
  python test_pipeline.py -i "data_markdown_new/Bộ Nội vụ new" -o "extract_answer_result/Bộ Nội vụ new" -n 5
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Cấu hình & imports từ core pipeline
from config import (
    ENABLE_MINISTRY_ROUTER,
    LLM_CONCURRENCY,
    MAX_CONCURRENCY,
)
from functions import (
    extract_donvi_id_from_text,
    extract_metadata as root_extract_metadata,
    extract_petitions as root_extract_petitions,
)
from logger import get_logger
from postprocess import _fix_ocr_diacritics, clean_footer
from router import route_extract

logger = get_logger("test_pipeline", "test_pipeline.log")


# ---------------------------------------------------------------------------
# Xử lý trích xuất và ghi file JSON cho 1 file Markdown
# ---------------------------------------------------------------------------
async def process_and_save_one_file(
    file_path: Path,
    out_dir: Path,
    llm_semaphore: asyncio.Semaphore,
    use_router: bool = True,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Đọc 1 file .md, trích xuất petitions + metadata và ghi ra file .json cùng tên."""
    file_stem = file_path.stem
    filename = file_path.name
    out_file = out_dir / f"{file_stem}.json"
    t0 = time.perf_counter()

    # Bỏ qua nếu file json đã tồn tại và không bật --overwrite
    if out_file.exists() and not overwrite:
        try:
            with open(out_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
            kn_count = len(existing_data.get("petitions", []))
            return {
                "file_id": filename,
                "file_stem": file_stem,
                "status": "SKIPPED",
                "petitions_count": kn_count,
                "ministry": "cached",
                "module": "cached",
                "donvi_id": existing_data.get("metadata", {}).get("donvi_id"),
                "elapsed_s": 0.0,
                "error": None,
            }
        except Exception:
            pass  # Nếu file cũ bị hỏng, tiếp tục chạy lại

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            raw_text = f.read()

        if not raw_text.strip():
            logger.warning(f"  [{filename}] File rỗng.")
            out_data = {
                "file": file_stem,
                "petitions": [],
                "metadata": {
                    "so_cong_van": None,
                    "ngay_ban_hanh": None,
                    "nguoi_ky": None,
                },
            }
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(out_data, f, ensure_ascii=False, indent=2)

            return {
                "file_id": filename,
                "file_stem": file_stem,
                "status": "EMPTY",
                "petitions_count": 0,
                "ministry": None,
                "module": None,
                "donvi_id": None,
                "elapsed_s": round(time.perf_counter() - t0, 3),
                "error": "File rỗng",
            }

        # 1. Post-OCR làm sạch văn bản
        md_text = _fix_ocr_diacritics(raw_text)
        md_text = clean_footer(md_text)

        # 2. Trích xuất ID Đoàn ĐBQH
        donvi_id = extract_donvi_id_from_text(md_text)

        # 3. Định tuyến theo Bộ hoặc chạy Root Generic Extractor
        if use_router and ENABLE_MINISTRY_ROUTER:
            routed = await route_extract(
                md_text,
                llm_semaphore=llm_semaphore,
                filename=filename,
            )
            raw_petitions = routed.get("petitions", [])
            file_metadata = routed.get("metadata", {})
            file_ministry = routed.get("ministry")
            file_module = routed.get("module")
        else:
            raw_petitions = await root_extract_petitions(md_text, llm_semaphore=llm_semaphore)
            file_metadata = root_extract_metadata(md_text)
            file_ministry = None
            file_module = "root"

        # 4. Chuẩn hóa format đúng chuẩn extract_answer_result
        petitions = [
            {
                "noi_dung": p.get("noi_dung", ""),
                "tra_loi": p.get("tra_loi", ""),
            }
            for p in raw_petitions
        ]

        metadata = {
            "so_cong_van": file_metadata.get("so_cong_van"),
            "ngay_ban_hanh": file_metadata.get("ngay_ban_hanh"),
            "nguoi_ky": file_metadata.get("nguoi_ky"),
        }
        if donvi_id is not None:
            metadata["donvi_id"] = donvi_id

        out_data = {
            "file": file_stem,
            "petitions": petitions,
            "metadata": metadata,
        }

        # 5. Ghi file JSON kết quả
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)

        elapsed = round(time.perf_counter() - t0, 3)
        return {
            "file_id": filename,
            "file_stem": file_stem,
            "status": "SUCCESS",
            "petitions_count": len(petitions),
            "ministry": file_ministry,
            "module": file_module,
            "donvi_id": donvi_id,
            "elapsed_s": elapsed,
            "error": None,
        }

    except Exception as exc:
        logger.exception(f"  [{filename}] Lỗi xử lý: {exc}")
        return {
            "file_id": filename,
            "file_stem": file_stem,
            "status": "ERROR",
            "petitions_count": 0,
            "ministry": None,
            "module": None,
            "donvi_id": None,
            "elapsed_s": round(time.perf_counter() - t0, 3),
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Luồng Runner chính cho toàn bộ folder
# ---------------------------------------------------------------------------
async def run_batch_pipeline(
    input_dir: Path,
    output_dir: Path,
    concurrency: int = MAX_CONCURRENCY,
    llm_concurrency: int = LLM_CONCURRENCY,
    use_router: bool = True,
    overwrite: bool = False,
    limit: Optional[int] = None,
):
    """Chạy toàn bộ pipeline trên thư mục chứa file markdown và lưu ra thư mục json."""
    start_time = time.monotonic()

    md_files = sorted(input_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"Không tìm thấy file .md nào trong thư mục '{input_dir}'")

    if limit and limit > 0:
        md_files = md_files[:limit]

    total_files = len(md_files)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*75}")
    print(f"🚀 BẮT ĐẦU CHẠY PIPELINE TRÍCH XUẤT VỚI {total_files} FILE MARKDOWN")
    print(f"📁 Thư mục đầu vào:  {input_dir}")
    print(f"📂 Thư mục đầu ra:   {output_dir}")
    print(f"⚙️  Cấu hình:         Concurrency={concurrency} | LLM_Concurrency={llm_concurrency} | Router={use_router} | Overwrite={overwrite}")
    print(f"{'='*75}\n")

    file_semaphore = asyncio.Semaphore(concurrency)
    llm_semaphore = asyncio.Semaphore(llm_concurrency)

    async def _worker(file_p: Path) -> Dict[str, Any]:
        async with file_semaphore:
            res = await process_and_save_one_file(
                file_p,
                output_dir,
                llm_semaphore,
                use_router=use_router,
                overwrite=overwrite,
            )

            status = res["status"]
            if status == "SUCCESS":
                icon = "✅"
            elif status == "SKIPPED":
                icon = "⏭️ "
            else:
                icon = "❌"

            kn_count = res["petitions_count"]
            mod_info = f"[{res.get('module') or 'generic'}]"
            donvi_info = f"Đoàn:{res.get('donvi_id')}" if res.get("donvi_id") else "Đoàn:?"
            print(f" {icon} {res['file_id'][:35]:<35} | {mod_info:<10} | {donvi_info:<10} | {kn_count:>2} cặp KN-TL | {res['elapsed_s']:>6.2f}s")
            return res

    tasks = [_worker(fp) for fp in md_files]
    results = await asyncio.gather(*tasks)

    # Thống kê tổng kết cho thư mục đầu vào
    success_count = sum(1 for r in results if r["status"] == "SUCCESS")
    skipped_count = sum(1 for r in results if r["status"] == "SKIPPED")
    error_count = sum(1 for r in results if r["status"] == "ERROR")
    total_petitions = sum(r["petitions_count"] for r in results)
    total_duration = round(time.monotonic() - start_time, 2)
    avg_speed = round(total_duration / total_files, 2) if total_files > 0 else 0

    print(f"\n{'='*75}")
    print(f"📊 BÁO CÁO KẾT QUẢ CHO THƯ MỤC: {input_dir.name}")
    print(f"{'='*75}")
    print(f"• Thư mục đầu vào:            {input_dir}")
    print(f"• Thư mục lưu file JSON:      {output_dir.resolve()}")
    print(f"• Tổng số file .md:           {total_files} file")
    print(f"   - Thành công:              {success_count} file")
    print(f"   - Bỏ qua (đã có sẵn):      {skipped_count} file")
    print(f"   - Thất bại / Lỗi:          {error_count} file")
    print(f"• Tổng cặp KN-TL bóc tách:    {total_petitions} cặp")
    print(f"• Thời gian chạy:             {total_duration}s (Trung bình: {avg_speed}s/file)")
    print(f"{'='*75}\n")


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Chạy full pipeline trích xuất từ folder Markdown -> lưu folder JSON cùng tên (extract_answer_result)."
    )
    parser.add_argument(
        "-i", "--input-dir",
        type=str,
        required=True,
        help="Đường dẫn thư mục chứa các file .md đầu vào.",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=str,
        required=True,
        help="Đường dẫn thư mục lưu các file .json đầu ra (ví dụ: extract_answer_result/Bộ Nội vụ new).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Ghi đè file .json nếu đã tồn tại trong thư mục đầu ra.",
    )
    parser.add_argument(
        "-c", "--concurrency",
        type=int,
        default=MAX_CONCURRENCY,
        help=f"Số file xử lý song song tối đa (mặc định: {MAX_CONCURRENCY}).",
    )
    parser.add_argument(
        "--llm-concurrency",
        type=int,
        default=LLM_CONCURRENCY,
        help=f"Số lượt gọi LLM song song tối đa (mặc định: {LLM_CONCURRENCY}).",
    )
    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=None,
        help="Giới hạn số lượng file chạy thử (ví dụ: -n 5 để chạy 5 file đầu).",
    )
    parser.add_argument(
        "--no-router",
        action="store_true",
        help="Tắt Ministry Router để chạy toàn bộ qua Root Generic Extractor.",
    )

    args = parser.parse_args()

    input_path = Path(args.input_dir)
    if not input_path.exists() or not input_path.is_dir():
        print(f"❌ Lỗi: Thư mục đầu vào '{args.input_dir}' không tồn tại hoặc không phải là thư mục!", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output_dir)

    asyncio.run(
        run_batch_pipeline(
            input_dir=input_path,
            output_dir=output_path,
            concurrency=args.concurrency,
            llm_concurrency=args.llm_concurrency,
            use_router=not args.no_router,
            overwrite=args.overwrite,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
