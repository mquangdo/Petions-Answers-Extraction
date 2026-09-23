import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _CLOSING_RE,
    _END_RE,
    _INTRO_RE,
    _ITEM_DASH_RE,
    _ITEM_NUM_RE,
    _NGAY_BAN_HANH_RE,
    _SIGN_TITLE_RE,
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


def _find_end(lines: list, start: int) -> int:
    """Tìm vị trí closing (câu kết / end marker) đầu tiên sau start."""
    for i in range(start, len(lines)):
        if _CLOSING_RE.search(lines[i]):
            return i
        if _END_RE.match(lines[i]):
            return i
    return len(lines)


def _locate(md_text: str):
    """
    Định vị 3 mốc của thư UBMTTQ: (i_intro, i_end).
    Trả về (None, None) nếu thiếu intro.
    """
    lines = md_text.splitlines()
    i_intro = next((i for i, l in enumerate(lines) if _INTRO_RE.search(l)), None)
    if i_intro is None:
        return None, None
    i_end = _find_end(lines, i_intro + 1)
    return i_intro, i_end


def _item_ranges(lines: list, start: int, end: int) -> list:
    """
    Tìm các dòng mở đầu item kiến nghị trong [start, end).
    Ưu tiên item đánh số ("N. Đối với/Về ..."); không có mới dùng item
    gạch đầu dòng ("- Về nội dung") để khỏi tách nhầm bullet đáp án.
    Trả về list chỉ số dòng.
    """
    numbered = [i for i in range(start, end) if _ITEM_NUM_RE.match(lines[i])]
    if numbered:
        return numbered
    return [i for i in range(start, end) if _ITEM_DASH_RE.match(lines[i])]


def _strip_item_marker(text: str) -> str:
    """Bỏ tiền tố marker ("- "/"N. ") ở đầu item, giữ nội dung kiến nghị."""
    text = text.strip()
    text = re.sub(r"^(?:-\s*|\d+\.\s*)", "", text)
    return text.strip()


def _split_inline_answer(item_line: str):
    """
    Tách dòng item chứa cả đáp án (trích dẫn + phần trả lời sau quote đóng),
    vd Hà Nội item 3: '3. Về nội dung "đề nghị ..." Ban Thường trực ... ghi
    nhận ...'. Trả về (noi_dung, tra_loi) hoặc (None, None) nếu không tách
    được (thiếu cặp quote mở/đóng cân bằng).
    """
    text = _strip_item_marker(item_line)
    m_open = re.search(r"[“\"]", text)
    if not m_open:
        return None, None
    m_close = re.search(r"[”\"]", text[m_open.end():])
    if not m_close:
        return None, None
    cut = m_open.end() + m_close.end()
    noi = text[:cut].strip()
    tra = text[cut:].strip().lstrip(":;,. ").strip()
    if not noi or not tra:
        return None, None
    return noi, tra


def classify_format(md_text: str) -> str:
    """
    Xác định format thư UBMTTQ:
      - "f1": không item -> 1 kiến nghị (trích dẫn trong intro + đáp án).
      - "f2": N>=1 item -> mỗi item + đáp án sau nó là 1 petition.
      - "llm": thiếu intro/end.
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return "llm"
    items = _item_ranges(lines, i_intro + 1, i_end)
    if not items:
        return "f1"
    return "f2"


def _extract_quote(text: str) -> str:
    """Lấy các đoạn trích dẫn "..." / "..." trong intro làm noi_dung (f1)."""
    quotes = re.findall(r"[“\"]([^“\"']+?)[”\"]", text)
    quotes = [q.strip() for q in quotes if q.strip()]
    if quotes:
        return " ".join(quotes)
    return text.strip()


def _extract_f1(md_text: str) -> list:
    """
    f1: 1 kiến nghị / thư (Tây Ninh, Lâm Đồng).
    noi_dung = trích dẫn trong đoạn intro; tra_loi = toàn bộ đáp án
    (sau intro, trước câu kết/Nơi nhận).
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return []
    noi_dung = _extract_quote(lines[i_intro])
    tra_loi = "\n".join(lines[i_intro + 1:i_end])
    if not noi_dung or not tra_loi.strip():
        return []
    return [{
        "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
        "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
    }]


def _extract_f2(md_text: str) -> list:
    """
    f2: N item, mỗi item + đáp án sau nó là 1 petition (Phú Thọ, TP HCM,
    An Giang, Hà Nội). Đáp án kéo dài tới item kế tiếp hoặc câu kết/end.
    Bỏ cặp rỗng.
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return []
    starts = _item_ranges(lines, i_intro + 1, i_end)
    if not starts:
        return []
    petitions = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else i_end
        # Item luôn là 1 dòng (tiêu đề); đáp án là các dòng sau tới mốc kế.
        noi_dung = _strip_item_marker(lines[st])
        tra_loi = "\n".join(lines[st + 1:end])
        if not tra_loi.strip():
            # Item 1 dòng chứa cả đáp án sau trích dẫn -> tách tại quote đóng.
            noi2, tra2 = _split_inline_answer(lines[st])
            if noi2 is None:
                continue
            noi_dung, tra_loi = noi2, tra2
        if not noi_dung:
            continue
        petitions.append({
            "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
            "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
        })
    return petitions


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
