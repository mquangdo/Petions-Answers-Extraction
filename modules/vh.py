# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị Bộ Văn hóa, Thể thao
và Du lịch.

Khung thư Bộ Văn hóa (giống nhau ở mọi file):
  - Header: "Số: NNNN /BVHTTDL-VP", "V/v trả lời kiến nghị ...",
    "Hà Nội, ngày DD tháng MM năm YYYY", "Kính gửi: Đoàn ĐBQH tỉnh/TP X".
  - Intro: "Bộ Văn hóa, Thể thao và Du lịch nhận được kiến nghị của cử tri ...
    với nội dung như sau:"  -> biên trên vùng NỘI DUNG (S1).
  - Mốc trả lời: "Bộ Văn hóa, Thể thao và Du lịch xin trả lời như sau:?"
    (Tây Ninh thiếu dấu ":") -> biên trên vùng TRẢ LỜI (S2).
  - Câu kết: "Bộ Văn hóa, Thể thao và Du lịch trân trọng gửi tới
    Đoàn Đại biểu Quốc hội ... để trả lời cử tri./." (+ số trang / Signature).
  - Cuối thư: "Nơi nhận:", "BỘ TRƯỞNG", tên người ký.

Format Bộ Văn hóa (khung f1/f2/f3 chung các bộ, tối ưu riêng):
  - f1: 1 kiến nghị (đoạn văn liền, không đánh số item) + đáp án văn xuôi
    (đáp án có thể chứa section "1)"/"2)" nội bộ như Hà Nội -> vẫn gộp
    thành 1 tra_loi duy nhất).
  - f3: N item kiến nghị "(1)...(N)" / "N)..." trong 1 vùng S1 + N section
    đáp án "N." / "N)" mở đầu bằng "Liên quan/Về/Đối với" -> map item k
    với block đáp án k theo THỨ TỰ.
  - f2 (GROUP header lặp lại, mỗi kiến nghị 1 mục S1+S2 riêng): KHÔNG xuất
    hiện ở Bộ Văn hóa -> giữ nhãn dự phòng, classify không trả f2.
  - llm: thiếu intro/mốc trả lời, hoặc số item và số block đáp án lệch nhau.

Quy ước tên:
  - _INTRO_RE   : dòng intro "... với nội dung như sau:" (biên trên S1)
  - _S2_MARK_RE : mốc mở đầu phần TRẢ LỜI (biên trên S2)
  - _S1_ITEM_RE : mốc mở đầu từng kiến nghị "(N)" / "N)" ở đầu dòng (f3)
  - _ANS_HEAD_RE: mốc mở đầu block TRẢ LỜI "N." / "N)" + keyword (f3)
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
# Biên trên vùng NỘI DUNG (S1): dòng intro kết thúc bằng
# "... với nội dung như sau:"
# ---------------------------------------------------------------------------
_INTRO_RE = re.compile(r"với nội dung như sau:\s*$", re.I)

# ---------------------------------------------------------------------------
# Mốc mở đầu phần TRẢ LỜI (S2):
#   "Bộ Văn hóa, Thể thao và Du lịch xin trả lời như sau:"
# Dấu ":" cuối là optional (Tây Ninh: "... như sau" không có ":").
# ---------------------------------------------------------------------------
_S2_MARK_RE = re.compile(
    r"Bộ Văn hóa, Thể thao và Du lịch xin trả lời như sau:?\s*$",
    re.I,
)

# ---------------------------------------------------------------------------
# (f3) Mốc mở đầu từng kiến nghị trong vùng S1:
#   "(1) Cử tri kiến nghị ..."   (Cao Bằng, Hải Phòng, Đồng Tháp)
#   "1) Cử tri kiến nghị ..."    (An Giang)
# Neo "^" đầu dòng để KHÔNG bắt các sub-item "(1)", "(2)" lồng GIỮA dòng
# (vd An Giang item 1 chứa "(1) Sớm ban hành ... (2) Xem xét bố trí ...").
# Vùng tìm kiếm đã giới hạn (intro, s2mark) nên không dính "(1)"-"_(6)_",
# "1)"/"2)" nằm trong vùng đáp án.
# ---------------------------------------------------------------------------
_S1_ITEM_RE = re.compile(r"^\s*(?:\(\d+\)|\d+\))\s+\S")

# ---------------------------------------------------------------------------
# (f3) Mốc mở đầu block TRẢ LỜI:
#   "1. Liên quan đến nội dung kiến nghị ..."   (Cao Bằng)
#   "## 2. Về kiến nghị ..."                   (Hải Phòng)
#   "1) Liên quan đến ..."                     (Đồng Tháp, Hà Nội)
#   "# 2) Liên quan đến ..."                   (An Giang)
# Bắt buộc có keyword "Liên quan/Về/Đối với" để KHÔNG bắt nhầm:
#   - sub-item đáp án "(1)"-"_"(6)_" (có ngoặc đơn), "a)"-"_"e)_" (chữ cái);
#   - bullet "- " / "- - " (OCR tách đôi);
#   - số trang lẻ ("28") sau câu kết.
# ---------------------------------------------------------------------------
_ANS_HEAD_RE = re.compile(
    r"^\s*[#*_>\s]*\d+[.)]\s+(?:Liên quan|Về|Đối với)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư (ranh giới cuối tra_loi):
#   "Bộ Văn hóa, Thể thao và Du lịch trân trọng gửi tới Đoàn Đại biểu
#    Quốc hội tỉnh/thành phố X để trả lời cử tri./." (+ số trang/Signature)
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"trân trọng gửi tới Đoàn [Đđ]ại biểu Quốc hội.*để trả lời cử tri",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư (không thuộc nội dung trả lời)
#   "Nơi nhận:" (### / #### / không heading), "BỘ TRƯỞNG" / "Bộ TRƯỞNG",
#   "Lưu: ...", "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(
    r"^\s*[#*_ \s]*?(Nơi nhận|(?:KT\.?\s*)?BỘ TRƯỞNG|Lưu:|<!-- Start of picture)",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR.
# Sau khi OCR, footnote được bóc nhầm vào giữa nội dung và có 2 thành phần:
#   1. Dấu tham chiếu TRONG văn bản: "<sup>1</sup>", "²", "¹", "³", ... -> sẽ bị
#      XÓA khỏi text (không phải nội dung chính).
#   2. Dòng footer GIẢI THÍCH ở cuối trang, bắt đầu bằng "<sup>N</sup>" (thường bị
#      OCR bọc thêm "*") -> những dòng này sẽ bị LOẠI BỎ hoàn toàn.
# Yêu cầu bắt buộc có thẻ "<sup>" ở đầu dòng để KHÔNG nhầm với các dòng danh sách
# đánh số "1. ...", "2. ..." trong nội dung.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata: Số công văn (trong dòng "Số: ...")
# Các biến thể OCR thường gặp ở Bộ Văn hóa:
#   "**Số:** 4974 /**BVHTTDL-VP**"   (bold quanh nhãn + suffix: "**" dính
#                                    ngay sau "/", 6/10 file dùng dạng này)
#   "Số: 4973/BVHTTDL-VP"            (không space, không bold)
#   "**Số:4970 /BVHTTDL-VP**"        (dính "Số:" với số, có space trước "/")
#   "**Số:4971 /BVHTTDL-VP**"
# ^ đầu dòng: cho phép tiền tố heading "#", bold "*"; dấu ":" optional.
# \*{0,2} sau "/" để chịu "**" bọc suffix (".../ **BVHTTDL-VP**"); cleanup
# ở extract_metadata đã xóa "*" nên thêm vào đây là an toàn.
# Dừng trước "V/v" hoặc hết dòng; trailing "**" được loại bằng \*{0,2}.
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*[:\-]?\s*\*{0,2}\s*([\d]*\s*/\*{0,2}[A-Za-z0-9Đđ\-\&()_.]+(?:\s*[A-Za-z0-9Đđ\-\&()_.]+)*)\*{0,2}(?=\s*V/v|\s*$)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành (dòng "Hà Nội, ngày DD tháng MM năm YYYY")
# Biến thể: "*Hà Nội, ngày07 tháng 8 năm 2026*" (Đồng Tháp: mất space sau
# "ngày") -> [\s_]* sau "ngày" chịu được dính số.
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*\*{0,2}(\d{1,2})\*{0,2}[\s_]*tháng[\s_]*(?:(\d{1,2})[\s_]*)?năm[\s_]*(\d{4})",
    re.I,
)

# ---------------------------------------------------------------------------
# Metadata: Người ký ("BỘ TRƯỞNG [Tên]" hoặc chỉ "BỘ TRƯỞNG")
# Các dạng ở Bộ Văn hóa:
#   - "## Lâm Thị Phương Thanh" sau "## BỘ TRƯỞNG" (có tên dòng riêng)
#   - "*## Lâm Thị Phương Thanh*" (italic + heading)
#   - thiếu tên (Đà Nẵng, Hà Nội: kết thúc ở "## BỘ TRƯỞNG"/"## Bộ TRƯỞNG")
# Tên không chứa ký tự "<" để không nuốt tag HTML; lookahead chấp nhận "<".
# ---------------------------------------------------------------------------
_NGUOI_KY_RE = re.compile(
    r"(?:\*\*)?BỘ\s+TRƯỞNG(?:\s+([^\n\r\*<]+?))?(?:\*\*)?(?=\s*(?:\n|<|Nơi nhận|$))",
    re.I,
)


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
