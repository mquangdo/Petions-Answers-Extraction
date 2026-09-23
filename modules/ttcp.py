# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị của Thanh tra
Chính phủ (Báo cáo kết quả giải quyết và trả lời kiến nghị cử tri).

Khung thư (Báo cáo TTCP):
  - Header: "#### THANH TRA CHÍNH PHỦ", "# CỘNG HOÀ ...", "Số: 2-F26/BC-TTCP"
    (số-chữ/số-BC-TTCP, KHÔNG bắt buộc có "/"), "Hà Nội, ngày DD tháng MM
    năm YYYY", "# BÁO CÁO Kết quả giải quyết và trả lời ...",
    "Kính gửi: Trưởng đoàn ĐBQH ...".
  - Intro: đoạn "Thực hiện Văn bản số ...; Thanh tra Chính phủ báo cáo ..."
    + đoạn tổng hợp "Tổng số có 04 kiến nghị, ...".
  - GROUP (mỗi nhóm 1 nguồn chuyển): "## N. Kiến nghị do <nguồn> chuyển"
    (vd "## 1. Kiến nghị do Ủy ban Dân nguyên và Giám sát chuyển").
  - Item (mỗi item là 1 kiến nghị): "Câu hỏi N. ..." / "Câu hồi N. ..."
    (OCR hay đọc "hỏi" thành "hồi"); đáp án bắt đầu ở dòng "Trả lời:".
  - Câu kết: "Thanh tra Chính phủ ... trân trọng/trần trọng báo cáo ..."
    (OCR hay mất dấu: "trần trọng").
  - Cuối thư: "Nơi nhận:" (bullet "- - " do OCR tách đôi), chữ ký
    "KT. TỔNG THANH TRA PHÓ TỔNG THANH TRA" (Phó Tổng Thanh tra ký thay)
    + tên dòng sau.

Format (khung f1/f2/llm, tối ưu riêng):
  - f2: N>=1 item "Câu hỏi/hồi N." -> mỗi item + đáp án sau "Trả lời:"
    là 1 petition.
  - f1: không item (dự phòng) -> intro + toàn bộ đáp án là 1 petition.
  - llm: thiếu intro/item/end.

Quy ước tên:
  - _INTRO_RE   : dòng tiêu đề "# BÁO CÁO ..." (biên trên vùng nội dung)
  - _GROUP_RE   : GROUP "## N. Kiến nghị do ..." ở đầu dòng (f2)
  - _ITEM_RE    : item "Câu hỏi/hồi N." ở đầu dòng (f2)
  - _ANS_MARK_RE: mốc mở đầu đáp án "Trả lời:" (biên trên tra_loi)
  - _CLOSING_RE : câu kết cuối thư (chặn khỏi tra_loi)
  - _END_RE     : vùng kết thúc thư (Nơi nhận / chữ ký / Lưu)

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì OCR thường không
đồng nhất về chữ hoa/thường.
"""

import re
import sys
from pathlib import Path

from common import postprocess_noi_dung, postprocess_tra_loi, _clean_text, _extract_signer, _extract_llm


# ---------------------------------------------------------------------------
# Biên intro: dòng tiêu đề báo cáo
#   "# BÁO CÁO Kết quả giải quyết và trả lời kiến nghị ..."
# ---------------------------------------------------------------------------
_INTRO_RE = re.compile(r"^\s*#{1,6}\s*BÁO CÁO\b", re.I)

# ---------------------------------------------------------------------------
# (f2) GROUP: nhóm kiến nghị theo nguồn chuyển, ở đầu dòng:
#   "## 1. Kiến nghị do Ủy ban Dân nguyên và Giám sát chuyển"
#   "## 2. Kiến nghị do Văn phòng Chính phủ chuyển"
# ("Dân nguyên" là lỗi OCR của "Dân nguyện" — không neo vào tên nguồn,
# chỉ neo khung "N. Kiến nghị do".)
# ---------------------------------------------------------------------------
_GROUP_RE = re.compile(
    r"^\s*#{1,6}\s*\d+\.\s*Kiến nghị do\b",
    re.I,
)

# ---------------------------------------------------------------------------
# (f2) Item: mở đầu từng kiến nghị, ở đầu dòng:
#   "Câu hỏi 1. ..." / "Câu hỏi 1: ..."   (chuẩn)
#   "Câu hồi 2. ..."                       (OCR đọc "hỏi" thành "hồi")
# Lớp [ỏồo] chịu cả "hỏi"/"hồi"/"hoi" mất dấu.
# ---------------------------------------------------------------------------
_ITEM_RE = re.compile(
    r"^\s*Câu\s+h[ỏồo]i\s+\d+\s*[\.:]?",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc mở đầu đáp án: dòng "Trả lời:" (đứng riêng hoặc mở đầu đoạn đáp án)
#   "#### Trả lời:" / "Trả lời: thực hiện chỉ đạo ..."
# Dòng này KHÔNG đưa vào tra_loi (bóc khi dựng đáp án).
# ---------------------------------------------------------------------------
_ANS_MARK_RE = re.compile(
    r"^\s*#{0,6}\s*Trả lời\s*:",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư (ranh giới cuối tra_loi):
#   "Thanh tra Chính phủ trần trọng báo cáo và cảm ơn ..."
# Lớp [âầa]/[ọo] chịu OCR mất dấu ("trần trọng" thay "trân trọng").
# Neo thêm "báo cáo" để khỏi bắt nhầm "trân trọng cảm ơn" giữa câu.
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"tr[âầa]n\s+tr[ọo]ng\s+báo cáo",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư (không thuộc nội dung trả lời)
#   "Nơi nhận:" (bullet "- - " do OCR tách đôi), "KT. TỔNG THANH TRA ...",
#   "Lưu: ...", "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(
    r"^\s*[#*_ \s-]*?(Nơi nhận|TỔNG THANH TRA|Lưu:|<!-- Start of picture)",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR. Copy chuẩn dùng chung
# các module (giữ đồng nhất để postprocess.clean_footer hoạt động).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata: Số công văn (trong dòng "Số: ..." ở header)
#   "Số: 2-F26/BC-TTCP"   (dạng số-chữ/số-BC-TTCP, không bắt buộc "/")
# ^ đầu dòng + nhãn "Số" để KHÔNG bắt "Văn bản số 498/UBDNGS16",
# "Quyết định số 09-QĐ/TW" trong thân. Lấy token đầu tiên sau nhãn.
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*:?\s*([A-Za-z0-9Đđ\-\./]+)",
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
# Metadata: Người ký. Chữ ký TTCP:
#   "KT. TỔNG THANH TRA PHÓ TỔNG THANH TRA" (Phó Tổng Thanh tra ký thay)
#   + tên ở dòng heading kế tiếp ("Lê Sỹ Bảy").
# Trả về tên (không chức danh), khớp ví dụ response schema "nguoi_ky".
# ---------------------------------------------------------------------------
_SIGN_TITLE_RE = re.compile(r"TỔNG THANH TRA|PHÓ TỔNG THANH TRA", re.I)


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
    Định vị 2 mốc của Báo cáo TTCP: (i_intro, i_end).
    Trả về (None, None) nếu thiếu dòng tiêu đề BÁO CÁO.
    """
    lines = md_text.splitlines()
    i_intro = next((i for i, l in enumerate(lines) if _INTRO_RE.search(l)), None)
    if i_intro is None:
        return None, None
    i_end = _find_end(lines, i_intro + 1)
    return i_intro, i_end


def _item_ranges(lines: list, start: int, end: int) -> list:
    """Tìm các dòng mở đầu item "Câu hỏi/hồi N." trong [start, end)."""
    return [i for i in range(start, end) if _ITEM_RE.match(lines[i])]


def _strip_ans_mark(text: str) -> str:
    """Bóc mốc "Trả lời:" ở đầu đáp án (giữ nội dung trả lời).
    Chỉ bóc PREFIX marker, KHÔNG pop cả dòng (đáp án có thể mở đầu ngay sau
    "Trả lời:" trên cùng dòng, vd "Trả lời: Luật Khiếu nại quy định...").
    """
    return re.sub(r"\A\s*(?:#+\s*)?Trả lời\s*:\s*", "", text)


def classify_format(md_text: str) -> str:
    """
    Xác định format Báo cáo TTCP:
      - "f2": N>=1 item "Câu hỏi/hồi N." -> mỗi item + đáp án sau "Trả lời:"
        là 1 petition.
      - "f1": không dùng ở TTCP (giữ nhãn dự phòng).
      - "llm": thiếu intro/item/end.
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return "llm"
    items = _item_ranges(lines, i_intro + 1, i_end)
    if not items:
        return "llm"
    return "f2"


def _extract_f2(md_text: str) -> list:
    """
    f2: N item, mỗi item + đáp án sau nó là 1 petition. Đáp án kéo dài tới
    item/GROUP kế tiếp hoặc câu kết/end (Nơi nhận/chữ ký). Bỏ cặp rỗng.
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return []
    starts = _item_ranges(lines, i_intro + 1, i_end)
    if not starts:
        return []
    # Biên phụ: GROUP header cũng cắt đáp án (mỗi GROUP chứa các item riêng).
    bounds = sorted(
        set(
            starts
            + [i for i in range(i_intro + 1, i_end) if _GROUP_RE.match(lines[i])]
            + [i_end]
        )
    )
    petitions = []
    for k, st in enumerate(starts):
        end = next((b for b in bounds if b > st), i_end)
        # Đáp án bắt đầu ở dòng "Trả lời:" đầu tiên sau item (nếu có);
        # mọi dòng từ item tới đó đều thuộc noi_dung (đề phòng kiến nghị
        # ngắt dòng/trang giữa chừng như "Câu hồi 2." ở file mẫu).
        ans = next(
            (i for i in range(st + 1, end) if _ANS_MARK_RE.match(lines[i])),
            None,
        )
        if ans is not None:
            noi_dung = "\n".join(lines[st:ans])
            tra_loi = _strip_ans_mark("\n".join(lines[ans:end]))
        else:
            noi_dung = lines[st]
            tra_loi = _strip_ans_mark("\n".join(lines[st + 1:end]))
        if not noi_dung.strip() or not tra_loi.strip():
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
    if fmt == "f2":
        return _extract_f2(md_text)
    return _extract_llm(md_text)


def extract_metadata(md_text: str) -> dict:
    """Trích xuất metadata: so_cong_van, ngay_ban_hanh, nguoi_ky (tên)."""
    result = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}

    m = _SO_CONG_VAN_RE.search(md_text)
    if m:
        val = re.sub(r"\*+", "", m.group(1))
        val = val.strip()
        result["so_cong_van"] = val or None

    m = _NGAY_BAN_HANH_RE.search(md_text)
    if m:
        day, month, year = m.groups()
        if day and month:
            result["ngay_ban_hanh"] = f"{int(day):02d}/{int(month):02d}/{year}"

    signer = _extract_signer(md_text)
    if signer:
        result["nguoi_ky"] = signer

    return result
