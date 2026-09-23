# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị của Ban Thường trực
Ủy ban Trung ương MTTQ Việt Nam.

Khung thư (giống nhau ở mọi file):
  - Header: "Số: **NNNN** /MTTW-BTT", "V/v trả lời ý kiến kiến nghị ...",
    "Hà Nội, ngày DD tháng MM năm YYYY", "Kính gửi: Đoàn ĐBQH ...".
  - Intro: "Phúc đáp Công văn số ... qua nghiên cứu nội dung kiến nghị ...
    [trích dẫn] ..., Ban Thường trực ... có ý kiến như sau:" (biên intro).
  - Items (mỗi item là 1 kiến nghị, đáp án là đoạn văn sau nó):
      "- Về nội dung: ..."                       (Phú Thọ, An Giang)
      "N. Đối với/Về nội dung...:" / "N. Về ý kiến:" (TP HCM, Hà Nội)
  - Câu kết: "Ban Thường trực ... thông báo ... tổng hợp, báo cáo ...".
  - Cuối thư: "Nơi nhận:", chữ ký "TM. BAN THƯỜNG TRỰC ..." + tên
    (đuôi "TỔNG/TÔNG THƯ KÝ" hay bị OCR sai).

Format (khung f1/f2/llm, tối ưu riêng):
  - f1: không item -> 1 kiến nghị (trích dẫn trong intro + toàn bộ đáp án).
  - f2: N item (numbered ưu tiên, không có mới dùng dash) -> mỗi item +
    đáp án sau nó là 1 petition.
  - llm: thiếu intro/end.

Quy ước tên:
  - _INTRO_RE   : dòng intro "... có ý kiến như sau:" (biên trên đáp án)
  - _ITEM_NUM_RE: item đánh số "N. Đối với/Về ..." ở đầu dòng (f2)
  - _ITEM_DASH_RE: item gạch đầu dòng "- Về nội dung" ở đầu dòng (f2)
  - _CLOSING_RE : câu kết cuối thư (chặn khỏi tra_loi)
  - _END_RE     : vùng kết thúc thư (Nơi nhận / chữ ký / Lưu)

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì OCR thường không
đồng nhất về chữ hoa/thường.
"""

import re
import sys
from pathlib import Path

from common import postprocess_noi_dung, postprocess_tra_loi, _clean_text, _extract_signer


# ---------------------------------------------------------------------------
# Biên intro: dòng kết thúc bằng "... có ý kiến như sau:"
# Lấy occurrence ĐẦU TIÊN (Hà Nội có thêm "... có ý kiến như sau:" lồng giữa
# đáp án item 2 — locate từ đầu nên không dính).
# ---------------------------------------------------------------------------
_INTRO_RE = re.compile(r"có ý kiến như sau:\s*$", re.I)

# ---------------------------------------------------------------------------
# (f2) Item đánh số ở đầu dòng:
#   "1. Đối với nội dung (1)...(2)...:"            (TP HCM)
#   "2. Về ý kiến: ..." / "3. Về nội dung ..."    (Hà Nội)
# Ưu tiên dùng loại này khi tồn tại (tránh tách nhầm bullet đáp án "- ...").
# ---------------------------------------------------------------------------
_ITEM_NUM_RE = re.compile(r"^\s*\d+\.\s+(?:Về|Đối với)\s+(?:nội dung|ý kiến)", re.I)

# ---------------------------------------------------------------------------
# (f2) Item gạch đầu dòng ở đầu dòng (chỉ dùng khi KHÔNG có item đánh số):
#   "- Về nội dung: ..."                          (Phú Thọ, An Giang)
# Yêu cầu "nội dung" ngay sau "Về" để KHÔNG bắt bullet đáp án
# ("- Việc ...", "- Về cơ chế ..." trong đáp án TP HCM).
# ---------------------------------------------------------------------------
_ITEM_DASH_RE = re.compile(r"^\s*-\s*Về\s+nội dung", re.I)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư (ranh giới cuối tra_loi):
#   "Ban Thường trực ... thông báo ... tổng hợp, báo cáo ..."
# Biến thể OCR: "thông báo đề/để Đoàn ..." -> chỉ neo "thông báo" +
# "tổng hợp, báo cáo" cho chắc.
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(r"thông báo.*tổng hợp,\s*báo cáo", re.I)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư (không thuộc nội dung trả lời)
#   "Nơi nhận:" (## / ### / ####), "Lưu: ...", "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(
    r"^\s*[#*_ \s]*?(Nơi nhận|Lưu:|<!-- Start of picture)",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR. Copy chuẩn dùng chung
# các module (giữ đồng nhất để postprocess.clean_footer hoạt động).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata: Số công văn (trong dòng "Số: ...")
#   "Số: **1221** /MTTW-BTT"   (bold bọc TRẦN SỐ, space trước "/")
#   "Số: **1261** /MTTW-BTT"
#   "Số: **1218** /MTTW-BTT"
# ^ đầu dòng + nhãn "Số" để KHÔNG bắt "Công văn số 498/UBDNGS16" trong thân.
# Khác mẫu VH: cho phép "**" ngay sau dãy số ([\d]*\*{0,2}). Cleanup ở
# extract_metadata đã xóa "*" và khoảng trắng.
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*[:\-]?\s*\*{0,2}\s*([\d]*\*{0,2}\s*/\*{0,2}[A-Za-z0-9Đđ\-\&()_.]+(?:\s*[A-Za-z0-9Đđ\-\&()_.]+)*)\*{0,2}(?=\s*V/v|\s*$)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành (dòng "Hà Nội, ngày DD tháng MM năm YYYY")
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*\*{0,2}(\d{1,2})\*{0,2}[\s_]*tháng[\s_]*(?:(\d{1,2})[\s_]*)?năm[\s_]*(\d{4})",
    re.I,
)

# ---------------------------------------------------------------------------
# Metadata: Người ký. Chữ ký UBMTTQ:
#   "## TM. BAN THƯỜNG TRỰC PHÓ CHỦ TỊCH – TỔNG THƯ KÝ" (đuôi hay bị OCR
#   thành "TÔNG/TÔNG THƯ KÝ" -> chỉ neo "TM. BAN THƯỜNG TRỰC" cho chắc)
#   + tên ở dòng heading kế tiếp ("## Hà Thị Nga", "*## Hà Thị Nga*").
# Trả về tên (không chức danh), khớp ví dụ response schema "nguoi_ky".
# ---------------------------------------------------------------------------
_SIGN_TITLE_RE = re.compile(r"TM\.\s*BAN THƯỜNG TRỰC", re.I)


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
