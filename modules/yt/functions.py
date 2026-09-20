import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _ANS_HEAD_RE,
    _CLOSING_RE,
    _END_RE,
    _ITEM_RE,
    _MARK_RE,
    _NGAY_BAN_HANH_RE,
    _SO_CONG_VAN_RE,
)


def _clean_text(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        s = re.sub(r"^(#+\s*|\*\*|\*|_+|>\s*)", "", s)
        s = s.strip(" *_#>")
        s = s.replace("<br>", " ").replace("<br/>", " ").replace("</br>", " ")
        s = re.sub(r"<sup>[^<]*</sup>", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        out.append(s)
    result = []
    blank = False
    for l in out:
        if l:
            result.append(l)
            blank = False
        else:
            if not blank:
                result.append("")
            blank = True
    while result and not result[0]:
        result.pop(0)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def _find_closing(lines: list, start: int) -> int:
    """Tìm vị trí closing (câu kết / end marker) đầu tiên sau start."""
    for i in range(start, len(lines)):
        if _CLOSING_RE.search(lines[i]):
            return i
        if _END_RE.match(lines[i]):
            return i
    return len(lines)


def _locate(md_text: str):
    """
    Định vị vùng nội dung thư Bộ Y tế: (i_mark, i_end).
    i_mark = marker đầu tiên ("... cụ thể như sau:" / "... trân trọng trả lời
    như sau:"); i_end = closing/END đầu tiên sau marker.
    Trả về (None, None) nếu thiếu marker.
    """
    lines = md_text.splitlines()
    marks = [i for i, l in enumerate(lines) if _MARK_RE.search(l)]
    if not marks:
        return None, None
    i_mark = marks[0]
    i_end = _find_closing(lines, i_mark)
    return i_mark, i_end


def classify_format(md_text: str) -> str:
    """
    Xác định format Bộ Y tế:
      - "f1": 0 item đánh số + 0 section đáp án -> 1 kiến nghị không đánh số
        (đoạn văn sau marker) + đáp án văn xuôi. Riêng Cà Mau có đúng 1 item
        "1. ..." (các liệt kê "1. (i)..." là sub, không tính) -> cũng f1.
      - "f2": >=2 item "N. ..." (allowlist Cử tri/Đề nghị/Kiến nghị/Hiện nay/
        Theo/Việc/Thực hiện) + đáp án inline.
      - "f3": <=1 item + >=1 section "## N. Về/Đối với" (Cần Thơ "1. Về..."
        không "##") -> 1 kiến nghị, gộp sections thành 1 tra_loi.
      - "llm": thiếu marker, hoặc không tách được petition-đáp án.
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return "llm"
    region = lines[i_mark + 1:i_end]
    n_items = sum(1 for l in region if _ITEM_RE.match(l))
    n_sec = sum(1 for l in region if _ANS_HEAD_RE.match(l))
    if n_items >= 2:
        return "f2"
    if n_sec >= 1:
        return "f3"
    return "f1"


def _split_first_para(block_lines: list):
    """
    Tách block thành (đoạn đầu, phần còn lại) tại dòng trống đầu tiên.
    noi_dung = đoạn văn đầu (kiến nghị), tra_loi = phần còn lại (đáp án).
    """
    noi, tra = [], []
    in_tra = False
    for line in block_lines:
        if not in_tra and not line.strip() and noi:
            in_tra = True
            continue
        (tra if in_tra else noi).append(line)
    return "\n".join(noi).strip(), "\n".join(tra).strip()


def _extract_f1(md_text: str) -> list:
    """
    f1: 1 kiến nghị / thư.
    - 0 item: noi_dung = đoạn văn đầu sau marker, tra_loi = phần còn lại.
    - 1 item (Cà Mau): xử lý như 1 block f2 (đoạn đầu = noi_dung).
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return []
    region = lines[i_mark + 1:i_end]
    starts = [i for i, l in enumerate(region) if _ITEM_RE.match(l)]
    block = region[starts[0]:] if starts else region
    noi_dung, tra_loi = _split_first_para(block)
    if not noi_dung or not tra_loi:
        return []
    return [{
        "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
        "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
    }]


def _extract_f2(md_text: str) -> list:
    """
    f2: >=2 item "N. ..." + đáp án inline đến item tiếp theo.
    Quy tắc trả lời gộp: item nào đáp án rỗng (2 item liền nhau như Tây Ninh
    L21+L23, Đồng Tháp L21, Đồng Nai L17) thì đáp án chung của block sau
    DÙNG CHO CẢ 2 kiến nghị (gán chứ không di chuyển).
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return []
    region = lines[i_mark + 1:i_end]
    starts = [i for i, l in enumerate(region) if _ITEM_RE.match(l)]
    if not starts:
        return []
    bounds = starts + [len(region)]
    pairs = []
    for k in range(len(starts)):
        block = region[bounds[k]:bounds[k + 1]]
        noi_dung, tra_loi = _split_first_para(block)
        pairs.append([noi_dung, tra_loi])
    # Trả lời gộp: block rỗng đáp án lấy đáp án block kế tiếp (giữ cho cả 2).
    for k in range(len(pairs) - 1):
        if pairs[k][0] and not pairs[k][1] and pairs[k + 1][1]:
            pairs[k][1] = pairs[k + 1][1]
    petitions = []
    for noi_dung, tra_loi in pairs:
        if not noi_dung or not tra_loi:
            continue
        petitions.append({
            "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
            "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
        })
    return petitions


def _extract_f3(md_text: str) -> list:
    """
    f3: 0-1 item + section đáp án "N. Về/Đối với" -> 1 kiến nghị, gộp các
    section (kèm preamble trước section đầu như Cao Bằng) thành 1 tra_loi.
    Dòng mốc đáp án phụ (Cao Bằng "... trân trọng trả lời như sau:") không
    đưa vào noi_dung.
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return []
    region = lines[i_mark + 1:i_end]
    marks_rel = [i for i, l in enumerate(region) if _MARK_RE.search(l)]
    heads = [i for i, l in enumerate(region) if _ANS_HEAD_RE.match(l)]
    if not heads:
        return []
    pet_end = marks_rel[0] if marks_rel else heads[0]
    noi_dung = "\n".join(region[:pet_end]).strip()
    ans_start = (marks_rel[0] + 1) if marks_rel else heads[0]
    tra_loi = "\n".join(region[ans_start:]).strip()
    if not noi_dung or not tra_loi:
        return []
    return [{
        "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
        "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
    }]


def extract_petitions(md_text: str) -> list:
    """
    Router: xác định format rồi route đến handler.
    Trả về danh sách {"noi_dung", "tra_loi"}.
    """
    fmt = classify_format(md_text)
    print(f"[format] {fmt}")
    if fmt == "f1":
        return _extract_f1(md_text)
    if fmt == "f2":
        return _extract_f2(md_text)
    if fmt == "f3":
        return _extract_f3(md_text)
    return []


def _extract_signer(md_text: str):
    """
    Tên người ký Bộ Y tế: dòng sau "## BỘ TRƯỞNG" trong 40 dòng cuối
    ("## Đào Hồng Lan"; Hưng Yên OCR "Đảo Hồng Lan" -> giữ nguyên văn,
    không đoán). Bóc markdown "#*". Không có khối chữ ký -> None.
    """
    tail = md_text.splitlines()[-40:]
    title_idx = None
    for i, l in enumerate(tail):
        s = l.strip()
        if s.startswith("-"):
            continue
        if "BỘ TRƯỞNG" in s.upper():
            title_idx = i
            break
    if title_idx is None:
        return None
    for line in tail[title_idx + 1:]:
        # Tên đánh máy luôn là heading "## ..." — bỏ qua dòng rác OCR từ ảnh
        # chữ ký (vd "*Joe Kalan*" ở Tây Ninh 58: italic trơn, không có "#").
        if "#" not in line:
            continue
        s = line.strip().strip("#*").strip()
        if not s:
            continue
        if len(s) > 60:
            continue
        if re.search(
            r"\b(đã|quy định|ban hành|sửa đổi|bãi bỏ|thông tư|nghị định|quyết định)\b",
            s,
            re.I,
        ):
            continue
        return s
    return None


def extract_metadata(md_text: str) -> dict:
    """Trích xuất metadata: so_cong_van, ngay_ban_hanh, nguoi_ky."""
    result = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}

    m = _SO_CONG_VAN_RE.search(md_text)
    if m:
        val = re.sub(r"\*+", "", m.group(1))
        val = re.sub(r"\s+", "", val)
        result["so_cong_van"] = val

    m = _NGAY_BAN_HANH_RE.search(md_text)
    if m:
        day, month, year = m.groups()
        if day and month:
            result["ngay_ban_hanh"] = f"{int(day):02d}/{int(month):02d}/{year}"

    name = _extract_signer(md_text)
    result["nguoi_ky"] = f"Bộ trưởng {name}" if name else "Bộ trưởng"

    return result
