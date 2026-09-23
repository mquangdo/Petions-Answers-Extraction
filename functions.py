import asyncio
import re
import sys
import unicodedata
from pathlib import Path

from common import _clean_text, find_entities
from config import LLM_CONCURRENCY
from logger import get_logger
from postprocess import postprocess_noi_dung, postprocess_tra_loi

logger = get_logger("functions", "pipeline.log")
from regexes import (
    _ANS_HEAD_RE,
    _CLOSING_RE,
    _END_RE,
    _GROUP_RE,
    _ITEM_START_RE,
    _S1_RESOLVER_RE,
    _S2_RE,
    _SO_CONG_VAN_RE,
    _NGAY_BAN_HANH_RE,
    _NGUOI_KY_RE,
    _TRACH_NHIEM_RE,
)

# ---------------------------------------------------------------------------
# Tra cứu ID Đơn vị tiếp nhận (tỉnh/thành phố) từ ID_đoàn_ĐB.json
# ---------------------------------------------------------------------------
_DONVI_MAP_PATH = Path(__file__).resolve().parent / "ID_đoàn_ĐB.json"
_NORM_DONVI_MAP: dict[str, int] = {}


def _strip_accents_lower(s: str) -> str:
    """Chuyển text về chữ thường, thay 'đ' thành 'd' và loại bỏ toàn bộ dấu tiếng Việt."""
    if not s:
        return ""
    s = s.lower().replace("đ", "d")
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", s).strip()


def _load_donvi_map() -> dict[str, int]:
    """Tải và chuẩn hóa từ điển ID_đoàn_ĐB.json vào bộ nhớ."""
    global _NORM_DONVI_MAP
    if _NORM_DONVI_MAP:
        return _NORM_DONVI_MAP
    if not _DONVI_MAP_PATH.exists():
        logger.warning(f"[donvi] Không tìm thấy file từ điển {_DONVI_MAP_PATH}")
        return {}
    try:
        import json
        raw_data = json.loads(_DONVI_MAP_PATH.read_text(encoding="utf-8"))
        m: dict[str, int] = {}
        # Sắp xếp các key theo chiều dài giảm dần để ưu tiên khớp tên dài trước
        for k, v in raw_data.items():
            norm_k = _strip_accents_lower(k)
            m[norm_k] = int(v)
            # Map thêm tên tỉnh ngắn (bỏ tiền tố 'tinh ', 'thanh pho ')
            short_k = re.sub(r"^(tinh|thanh pho)\s+", "", norm_k)
            if short_k and short_k != norm_k:
                m[short_k] = int(v)
        _NORM_DONVI_MAP = m
    except Exception as e:
        logger.error(f"[donvi] Lỗi khi load {_DONVI_MAP_PATH}: {e}")
    return _NORM_DONVI_MAP


def extract_donvi_id_from_text(md_text: str) -> int | None:
    """
    Trích xuất tên Đoàn ĐBQH từ văn bản OCR (tìm trong 50 dòng đầu),
    chuẩn hóa không dấu + lowercase và tra cứu ID trong ID_đoàn_ĐB.json.
    Trả về ID đơn vị (int) hoặc None nếu không tìm thấy.
    """
    donvi_map = _load_donvi_map()
    if not donvi_map or not md_text:
        return None

    # Quét 50 dòng đầu (phần header công văn)
    lines = md_text.splitlines()[:50]
    
    # Cách 1: Tìm dòng chứa cụm "đoàn đại biểu quốc hội"
    for line in lines:
        norm_line = _strip_accents_lower(line)
        if "doan dai bieu quoc hoi" in norm_line:
            # Tra cứu khớp tên tỉnh trong dòng này (ưu tiên tên dài trước)
            for k in sorted(donvi_map.keys(), key=len, reverse=True):
                if k in norm_line:
                    return donvi_map[k]

    # Cách 2: Quét tổng quát trong toàn bộ header
    full_header_norm = _strip_accents_lower(" ".join(lines))
    for k in sorted(donvi_map.keys(), key=len, reverse=True):
        if f"doan dai bieu quoc hoi {k}" in full_header_norm or f"doan dbqh {k}" in full_header_norm:
            return donvi_map[k]

    return None


def _is_s1(line: str) -> bool:
    low = line.lower()
    if "nội dung kiến nghị" not in low:
        return False
    if _S1_RESOLVER_RE.search(low):
        return False
    s = line.strip()
    return s.startswith("#") or s.startswith("**") or s.startswith("_")


def classify_format(md_text: str):
    """
    Xác định format của file .md đầu vào:
      - "f1": 1 mục "Nội dung kiến nghị" (S1) + 1 mục "Kết quả nghiên cứu, giải
        quyết và trả lời" (S2) -> 1 kiến nghị / thư.
      - "f2": mỗi kiến nghị 1 mục riêng (GROUP header + S1 + S2) -> N kiến nghị.
      - "f3": liệt kê hết các kiến nghị trong 1 mục S1 (dạng "Kiến nghị số X:"),
        trả lời theo heading "2.x. Về/Đối với kiến nghị số ...".
      - "llm": không nhận diện được 3 format chuẩn (không có S1/S2 ->
        cần xử lý bằng LLM).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    # Chỉ s1 nào có s2 THEO SAU mới là section "Nội dung kiến nghị" thật
    # (loại các dòng "2.x Nội dung kiến nghị: ..." nằm trong phần trả lời).
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    if not s1_eff or not s2_pos:
        return "llm"
    if len(s1_eff) == 1 and len(s2_pos) == 1:
        start, end = s1_eff[0], s2_pos[0]
        n_items = (
            sum(1 for l in lines[start:end] if _ITEM_START_RE.search(l))
            if start < end
            else 0
        )
        if n_items >= 2:
            return "f3"
        return "f1"
    return "f2"


def _split_items(slice_lines: list) -> list:
    """Tách vùng S1 thành các kiến nghị liệt kê -> [(số, text_đã_nối), ...]."""
    starts = [i for i, l in enumerate(slice_lines) if _ITEM_START_RE.search(l)]
    if not starts:
        return []
    items = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(slice_lines)
        num = _ITEM_START_RE.search(slice_lines[st]).group(1)
        items.append((num, "\n".join(slice_lines[st:end])))
    return items


def _split_answer_blocks(slice_lines: list) -> list:
    """Tách vùng S2 thành các block trả lời theo heading số -> [(tập_số, text), ...]."""
    heads = [i for i, l in enumerate(slice_lines) if _ANS_HEAD_RE.search(l)]
    if not heads:
        return []
    blocks = []
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(slice_lines)
        nums = {int(x) for x in re.findall(r"\d+", _ANS_HEAD_RE.search(slice_lines[h]).group(1))}
        body = slice_lines[h + 1:end]
        if k == 0:
            body = slice_lines[:h] + body  # phần mở đầu trước heading đầu gắn vào block đầu
        blocks.append((nums, "\n".join(body)))
    return blocks


def _extract_standard(md_text: str) -> list:
    """
    Logic extract chung cho format 1 và 2: mỗi cặp S1+S2 sinh 1 petition.
    File F1 -> 1 phần tử; file F2 -> N phần tử, tự phân giới qua group header
    ("I. Kiến nghị số ..."/"n. Đối với kiến nghị số ...") và khối cuối thư.
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    group_pos = [i for i, line in enumerate(lines) if _GROUP_RE.match(line)]
    end_pos = [i for i, line in enumerate(lines) if _END_RE.match(line)]
    closing_pos = [i for i, line in enumerate(lines) if _CLOSING_RE.search(line)]
    trach_nhiem_pos = [i for i, line in enumerate(lines) if _TRACH_NHIEM_RE.match(line)]
    stops = sorted(set(group_pos + s1_eff + s2_pos + end_pos + closing_pos + trach_nhiem_pos))

    petitions = []
    for j in s2_pos:
        before = [i for i in s1_eff if i < j]
        if not before:
            continue
        i = before[-1]
        boundary = next((k for k in stops if k > j), None)
        noi_dung = "\n".join(lines[i + 1:j])
        tra_loi = "\n".join(lines[j + 1:boundary])
        petitions.append({"noi_dung": postprocess_noi_dung(_clean_text(noi_dung)), "tra_loi": postprocess_tra_loi(tra_loi, _clean_text)})
    return petitions


def _extract_f1(md_text: str) -> list:
    """Format 1: 1 kiến nghị / thư."""
    return _extract_standard(md_text)


def _extract_f2(md_text: str) -> list:
    """Format 2: mỗi kiến nghị 1 mục riêng."""
    return _extract_standard(md_text)


def _extract_f3(md_text: str) -> list:
    """
    Format 3: kiến nghị được liệt kê hết trong 1 mục S1, trả lời theo heading
    "2.x. Về/Đối với kiến nghị số ...". Tách từng kiến nghị và map theo số:
      - item có số thuộc tập số của block nào -> tra_loi = block đó.
      - item không khớp block nào -> tra_loi = toàn bộ phần trả lời (an toàn).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    group_pos = [i for i, line in enumerate(lines) if _GROUP_RE.match(line)]
    end_pos = [i for i, line in enumerate(lines) if _END_RE.match(line)]
    closing_pos = [i for i, line in enumerate(lines) if _CLOSING_RE.search(line)]
    trach_nhiem_pos = [i for i, line in enumerate(lines) if _TRACH_NHIEM_RE.match(line)]
    stops = sorted(set(group_pos + s1_eff + s2_pos + end_pos + closing_pos + trach_nhiem_pos))

    petitions = []
    for j in s2_pos:
        before = [i for i in s1_eff if i < j]
        if not before:
            continue
        i = before[-1]
        boundary = next((k for k in stops if k > j), None)
        full_answer = "\n".join(lines[j + 1:boundary])
        items = _split_items(lines[i + 1:j])
        blocks = _split_answer_blocks(lines[j + 1:boundary])
        if len(items) >= 2 and blocks:
            for num, item_text in items:
                try:
                    n = int(num)
                except ValueError:
                    n = None
                target = full_answer
                for nums, block_text in blocks:
                    if n is not None and n in nums:
                        target = block_text
                        break
                petitions.append({
                    "noi_dung": postprocess_noi_dung(_clean_text(item_text)),
                    "tra_loi": postprocess_tra_loi(target, _clean_text),
                })
        else:
            petitions.append({
                "noi_dung": postprocess_noi_dung(_clean_text("\n".join(lines[i + 1:j]))),
                "tra_loi": postprocess_tra_loi(full_answer, _clean_text),
            })
    return petitions


async def extract_petitions(md_text: str, llm_semaphore: asyncio.Semaphore | None = None) -> list:
    """
    Router: xác định format của file rồi route đến handler tương ứng.
    Trả về danh sách petition {"noi_dung", "tra_loi"}.

    - f1/f2/f3: xử lý bằng regex (_extract_f1/f2/f3).
    - "llm" (không nhận diện được 3 format chuẩn): _extract_llm HIỆN TẮT,
      trả 1 petition rỗng (không gọi LLM).

    LƯU Ý: text đầu vào phải là markdown đã qua post-OCR
    (postprocess.clean_footer + _fix_ocr_diacritics) — do bước OCR đảm nhận.
    """
    fmt = classify_format(md_text)
    logger.info(f"[classifier] Định dạng văn bản nhận diện được: {fmt}")
    if fmt == "f1":
        return _extract_f1(md_text)
    if fmt == "f2":
        return _extract_f2(md_text)
    if fmt == "f3":
        return _extract_f3(md_text)
    if fmt == "llm":
        return await _extract_llm(md_text, llm_semaphore=llm_semaphore)
    return []

def extract_metadata(md_text: str) -> dict:
    """Trích xuất metadata từ markdown OCR: so_cong_van, ngay_ban_hanh, nguoi_ky.

    LƯU Ý: text đầu vào phải là markdown đã qua post-OCR
    (postprocess.clean_footer + _fix_ocr_diacritics) — do bước OCR đảm nhận.
    """
    result = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}

    # 1. Số công văn: tìm dòng "Số: ..."
    m = _SO_CONG_VAN_RE.search(md_text)
    if m:
        # Loại bỏ markdown bold (**) và MỌI khoảng trắng; giữ nguyên nếu chỉ có suffix
        val = re.sub(r"\*+", "", m.group(1))
        val = re.sub(r"\s+", "", val)
        result["so_cong_van"] = val

    # 2. Ngày ban hành: tìm mốc "ngày ... tháng ... năm ..."
    m = _NGAY_BAN_HANH_RE.search(md_text)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        result["ngay_ban_hanh"] = f"{int(d):02d}/{int(mo):02d}/{y}"

    # 3. Người ký: chỉ tìm trong 40 dòng CUỐI (vùng chữ ký), tránh bắt nhầm
    #    "của Bộ trưởng ..." trong thân thư.
    tail = "\n".join(md_text.splitlines()[-40:])
    matches = list(_NGUOI_KY_RE.finditer(tail))
    if matches:
        chosen = None
        for m in matches:
            name = (m.group(1) or "").strip()
            if (
                name
                and len(name) <= 60
                and not re.search(
                    r"\b(đã|quy định|ban hành|sửa đổi|bãi bỏ|thông tư|nghị định|quyết định)\b",
                    name,
                    re.I,
                )
            ):
                chosen = name
                break
        result["nguoi_ky"] = f"Bộ trưởng {chosen}" if chosen else "Bộ trưởng"

    return result


async def _extract_llm(md_text: str, llm_semaphore: asyncio.Semaphore | None = None) -> list:
    """
    Trích xuất bằng LLM — fallback khi file không nhận diện được format chuẩn
    (dùng cho pipeline hybrid). Trả về SAME contract với extract_petitions:
    list[{"noi_dung", "tra_loi"}].

    LƯU Ý: mỗi lần gọi thực hiện 2 LLM calls (semantic chunking — xem
    llm_chunking.py). Cảnh báo (VALIDATE/CAN_BANG/MISMATCH...) được in ra
    logger; hàm vẫn trả về list pairs thuần.
    """
    if llm_semaphore is not None:
        async with llm_semaphore:
            result = await extract_from_md(md_text, file_name="document")
    else:
        result = await extract_from_md(md_text, file_name="document")
    for w in result.get("warnings", []):
        logger.warning(f"      [CẢNH BÁO LLM] {w}")
    return [
        {"noi_dung": kn, "tra_loi": tl}
        for kn, tl in zip(result.get("kien_nghi", []), result.get("tra_loi", []))
    ]


if __name__ == "__main__":
    folder = Path("markdown")
    for file_path in folder.glob('*.md'):
        text = file_path.read_text(encoding="utf-8")
        result = find_entities(text)
        print(f"{file_path}: {result}")
        if result:
            assert result["daibieu"], file_path
            assert result["bộ"], file_path