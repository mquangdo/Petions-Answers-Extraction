import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _CLOSING_RE,
    _END_RE,
    _NGAY_BAN_HANH_RE,
    _SIGN_TITLE_RE,
    _S1_RE,
    _S2_RE,
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


def classify_format(md_text: str) -> str:
    """
    Xác định format BTCTW:
      - "f1": 1 cặp S1+S2 (heading "## 1. Nội dung kiến nghị ..." +
        "## 2. Trả lời kiến nghị") -> 1 kiến nghị / thư.
      - "f2": N cặp S1+S2 (heading kiến nghị lặp "## N. ...") -> N kiến nghị.
      - "f3": không xuất hiện ở BTCTW (giữ nhãn dự phòng).
      - "llm": không có cặp S1+S2 hiệu lực (S1 nào cũng phải có S2 theo sau).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _S1_RE.match(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.match(line)]
    # Chỉ s1 nào có s2 THEO SAU mới là section kiến nghị thật.
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    if not s1_eff or not s2_pos:
        return "llm"
    if len(s1_eff) == 1 and len(s2_pos) == 1:
        return "f1"
    return "f2"


def _extract_standard(md_text: str) -> list:
    """
    Logic extract chung cho format 1 và 2: mỗi cặp S1+S2 sinh 1 petition.
    File F1 -> 1 phần tử; file F2 -> N phần tử, tự phân giới qua S1/S2 kế
    tiếp, câu kết và khối cuối thư (Nơi nhận / chữ ký).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _S1_RE.match(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.match(line)]
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    end_pos = [i for i, line in enumerate(lines) if _END_RE.match(line)]
    closing_pos = [i for i, line in enumerate(lines) if _CLOSING_RE.search(line)]
    stops = sorted(set(s1_eff + s2_pos + end_pos + closing_pos))

    petitions = []
    for j in s2_pos:
        before = [i for i in s1_eff if i < j]
        if not before:
            continue
        i = before[-1]
        boundary = next((k for k in stops if k > j), None)
        noi_dung = "\n".join(lines[i + 1:j])
        tra_loi = "\n".join(lines[j + 1:boundary])
        if not noi_dung.strip() or not tra_loi.strip():
            continue
        petitions.append({"noi_dung": postprocess_noi_dung(_clean_text(noi_dung)), "tra_loi": postprocess_tra_loi(tra_loi, _clean_text)})
    return petitions


def _extract_f1(md_text: str) -> list:
    """Format 1: 1 kiến nghị / thư."""
    return _extract_standard(md_text)


def _extract_f2(md_text: str) -> list:
    """Format 2: N cặp S1+S2."""
    return _extract_standard(md_text)


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
    return []


def _looks_like_name(s: str) -> bool:
    """Dòng tên người: 2-4 từ, mỗi từ viết hoa đầu, không chứa từ khóa rác."""
    words = s.split()
    if not (2 <= len(words) <= 4):
        return False
    if not all(w and w[0].isupper() for w in words):
        return False
    if re.search(
        r"(TRƯỞNG BAN|Nơi|Lưu|Đ/c|đồng chí|Như trên|BỘ TRƯỞNG|quy định|nghị định|thông tư|quyết định)",
        s,
        re.I,
    ):
        return False
    return True


def _extract_signer(md_text: str):
    """
    Tên người ký BTCTW trong 40 dòng cuối, 2 đường:
      1. Có dòng chức danh ("K/T TRƯỞNG BAN", "PHÓ/PHÚ/NHIỆU TRƯỞNG BAN",
         ... — mọi biến thể đều chứa "TRƯỞNG BAN", mở đầu "#" chứ không phải
         bullet "-" của Nơi nhận) -> tên ở dòng không-chức-danh đầu tiên sau đó.
      2. Không có dòng chức danh (26, 27, 5HUe, file 1: chỉ còn tên trơ
         "*## Hà Minh Hải*") -> quét ngược tìm dòng giống tên người.
    Trả về tên hoặc None.
    """
    tail = md_text.splitlines()[-40:]
    title_idx = None
    for i, l in enumerate(tail):
        s = l.strip()
        if s.startswith("-"):
            continue
        if _SIGN_TITLE_RE.search(s):
            title_idx = i
            break
    if title_idx is not None:
        for line in tail[title_idx + 1:]:
            s = line.strip().strip("#*~").strip()
            if not s:
                continue
            if _SIGN_TITLE_RE.search(s):
                continue
            if len(s) > 60:
                continue
            if not _looks_like_name(s):
                continue
            return s
        return None
    for line in reversed(tail):
        s = line.strip()
        if not s or s.startswith("-"):
            continue
        s = s.strip("#*").strip()
        if _looks_like_name(s):
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

    # Người ký BTCTW là PHÓ TRƯỞNG BAN ký thay (K/T TRƯỞNG BAN) nên giữ đúng
    # chức danh; không tìm được tên thì trả cơ quan "Ban Tổ chức Trung ương".
    name = _extract_signer(md_text)
    result["nguoi_ky"] = f"Phó Trưởng ban {name}" if name else "Ban Tổ chức Trung ương"

    return result
