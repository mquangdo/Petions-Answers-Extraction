import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _CLOSING_RE,
    _END_RE,
    _GROUP_RE,
    _NGAY_BAN_HANH_RE,
    _NGUOI_KY_RE,
    _S1_RE,
    _S1_RESOLVER_RE,
    _S2_RE,
    _SO_CONG_VAN_RE,
)


def _is_s1(line: str) -> bool:
    """
    Heading "Nội dung kiến nghị" (S1) của Bộ KH&CN: "## 1. Nội dung kiến nghị".
    2 lớp bảo vệ khỏi bẫy trong vùng đáp án ("... được/đang/đã ..."):
      1. _S1_RE yêu cầu HẾT DÒNG sau "kiến nghị";
      2. _S1_RESOLVER_RE loại dòng có "được/đang/đã/thuộc" theo sau.
    """
    if not _S1_RE.match(line):
        return False
    if _S1_RESOLVER_RE.search(line.lower()):
        return False
    return True


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
    Xác định format Bộ Khoa học và Công nghệ:
      - "f1": 1 cặp S1+S2 (không GROUP header) -> 1 kiến nghị / thư.
      - "f2": N GROUP header ("# I. Kiến nghị số ..."), mỗi GROUP 1 cặp
        S1+S2 riêng -> N kiến nghị.
      - "f3": không xuất hiện ở Bộ KH&CN (giữ nhãn dự phòng).
      - "llm": không có cặp S1+S2 hiệu lực (S1 nào cũng phải có S2 theo sau).
    File gộp nhiều thư (vd ThanhHoa2) có nhiều cặp S1+S2 -> "f2", extractor
    tự tách từng cặp nên không cần cắt đoạn thư trước.
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    # Chỉ s1 nào có s2 THEO SAU mới là section "Nội dung kiến nghị" thật.
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    if not s1_eff or not s2_pos:
        return "llm"
    if len(s1_eff) == 1 and len(s2_pos) == 1:
        return "f1"
    return "f2"


def _extract_standard(md_text: str) -> list:
    """
    Logic extract chung cho format 1 và 2: mỗi cặp S1+S2 sinh 1 petition.
    File F1 -> 1 phần tử; file F2 (kể cả file gộp nhiều thư) -> N phần tử,
    tự phân giới qua GROUP header ("# I. Kiến nghị số ..."), S1/S2 kế tiếp,
    câu kết và khối cuối thư (Nơi nhận / chữ ký).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    group_pos = [i for i, line in enumerate(lines) if _GROUP_RE.match(line)]
    end_pos = [i for i, line in enumerate(lines) if _END_RE.match(line)]
    closing_pos = [i for i, line in enumerate(lines) if _CLOSING_RE.search(line)]
    stops = sorted(set(group_pos + s1_eff + s2_pos + end_pos + closing_pos))

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
    """Format 2: mỗi GROUP 1 cặp S1+S2 riêng."""
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


def _extract_signer(md_text: str) -> str:
    """
    Trích xuất chức vụ và tên người ký từ 20 dòng cuối văn bản bằng model Qwen3 (localhost:8000).
    - Có chức vụ + tên -> '<Chức vụ> <Tên>'
    - Không có chức vụ -> '<Tên>'
    - Không có tên / lỗi -> ''
    """
    if not md_text:
        return ""

    lines = md_text.splitlines()[-20:]
    tail_text = "\n".join(lines).strip()
    if not tail_text:
        return ""

    url = "http://localhost:8000/v1/chat/completions"
    payload = {
        "model": "Qwen/Qwen3-4B-Instruct-2507",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Bạn là trợ lý trích xuất thông tin văn bản hành chính Việt Nam. "
                    "Nhiệm vụ: Tìm người ký văn bản ở các dòng cuối.\n"
                    "Quy tắc:\n"
                    "1. Nếu có cả Chức vụ và Họ tên: Trả về '<Chức vụ> <Họ tên>'.\n"
                    "2. Nếu có họ tên người ký nhưng không có chức vụ: Trả về nguyên '<Họ tên>'.\n"
                    "3. Chỉ trả về 'NONE' khi hoàn toàn không có tên người nào ở phần ký.\n"
                    "4. Tuyệt đối không giải thích."
                ),
            },
            {
                "role": "user",
                "content": f"Dưới đây là 20 dòng cuối của văn bản:\n```\n{tail_text}\n```\nHọ tên người ký:",
            },
        ],
        "temperature": 0,
        "max_tokens": 50,
    }

    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            res = client.post(url, json=payload)
            if res.status_code == 200:
                content = res.json()["choices"][0]["message"]["content"].strip()
                content = content.strip("'\"").strip()
                if content.upper() == "NONE" or len(content) > 60:
                    return ""
                return content
    except Exception:
        return ""

    return ""


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

    signer = _extract_signer(md_text)
    result["nguoi_ky"] = signer if signer else None

    return result
