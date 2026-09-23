import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _ANS_HEAD_RE,
    _CLOSING_RE,
    _END_RE,
    _INTRO_RE,
    _NGAY_BAN_HANH_RE,
    _NGUOI_KY_RE,
    _S1_ITEM_RE,
    _S2_MARK_RE,
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
    Định vị 3 mốc của thư Bộ Văn hóa: (i_intro, i_s2, i_end).
    Trả về (None, None, None) nếu thiếu intro hoặc mốc trả lời.
    """
    lines = md_text.splitlines()
    i_intro = next((i for i, l in enumerate(lines) if _INTRO_RE.search(l)), None)
    i_s2 = next((i for i, l in enumerate(lines) if _S2_MARK_RE.search(l)), None)
    if i_intro is None or i_s2 is None or i_s2 <= i_intro:
        return None, None, None
    i_end = _find_closing(lines, i_s2)
    return i_intro, i_s2, i_end


def classify_format(md_text: str) -> str:
    """
    Xác định format Bộ Văn hóa:
      - "f1": 1 kiến nghị (không item "(N)"/"N)") + đáp án lấy NGUYÊN 1 khối
        (đáp án có thể chứa section "1)"/"2)" nội bộ như Hà Nội -> vẫn gộp
        thành 1 tra_loi duy nhất).
      - "f3": N>=2 item kiến nghị "(N)"/"N)" trong 1 vùng S1 + đúng N block
        đáp án "N."/"N)" -> map item k với block k theo thứ tự.
      - "f2": không xuất hiện ở Bộ Văn hóa (giữ nhãn dự phòng).
      - "llm": thiếu intro/mốc trả lời, hoặc số item và số block đáp án
        lệch nhau.
    """
    lines = md_text.splitlines()
    i_intro, i_s2, i_end = _locate(md_text)
    if i_intro is None:
        return "llm"
    n_items = sum(1 for l in lines[i_intro + 1:i_s2] if _S1_ITEM_RE.match(l))
    n_ans = sum(1 for l in lines[i_s2 + 1:i_end] if _ANS_HEAD_RE.match(l))
    if n_items == 0:
        return "f1"
    if n_items >= 2 and n_ans == n_items:
        return "f3"
    return "llm"


def _extract_f1(md_text: str) -> list:
    """
    f1: 1 kiến nghị / thư.
    noi_dung = toàn vùng S1 (sau intro, trước mốc trả lời).
    tra_loi = toàn vùng đáp án (sau mốc trả lời, trước câu kết/Nơi nhận).
    """
    lines = md_text.splitlines()
    i_intro, i_s2, i_end = _locate(md_text)
    if i_intro is None:
        return []
    noi_dung = "\n".join(lines[i_intro + 1:i_s2]).strip()
    tra_loi = "\n".join(lines[i_s2 + 1:i_end]).strip()
    if not noi_dung or not tra_loi:
        return []
    return [{
        "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
        "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
    }]


def _split_items(slice_lines: list) -> list:
    """Tách vùng S1 thành các kiến nghị liệt kê (bỏ tiền tố "(N)"/"N)")."""
    starts = [i for i, l in enumerate(slice_lines) if _S1_ITEM_RE.match(l)]
    if not starts:
        return []
    items = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(slice_lines)
        text = "\n".join(slice_lines[st:end]).strip()
        text = re.sub(r"^\s*(?:\(\d+\)|\d+\))\s*", "", text)
        items.append(text)
    return items


def _split_answer_blocks(slice_lines: list) -> list:
    """
    Tách vùng đáp án thành các block theo heading số.
    Phần mở đầu trước heading đầu (thường chỉ dòng trống) gắn vào block đầu.
    Dòng heading ("1. Liên quan ...") không đưa vào tra_loi.
    """
    heads = [i for i, l in enumerate(slice_lines) if _ANS_HEAD_RE.match(l)]
    if not heads:
        return []
    blocks = []
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(slice_lines)
        body = slice_lines[h + 1:end]
        if k == 0:
            body = slice_lines[:h] + body
        blocks.append("\n".join(body).strip())
    return blocks


def _extract_f3(md_text: str) -> list:
    """
    f3: N item kiến nghị + N block đáp án -> map item k với block k
    theo THỨ TỰ (classify đã đảm bảo số lượng bằng nhau).
    """
    lines = md_text.splitlines()
    i_intro, i_s2, i_end = _locate(md_text)
    if i_intro is None:
        return []
    items = _split_items(lines[i_intro + 1:i_s2])
    blocks = _split_answer_blocks(lines[i_s2 + 1:i_end])
    if not items or not blocks:
        return []
    petitions = []
    for k in range(min(len(items), len(blocks))):
        noi_dung = items[k]
        tra_loi = blocks[k]
        if not noi_dung or not tra_loi:
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
    if fmt == "f3":
        return _extract_f3(md_text)
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
