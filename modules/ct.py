# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị Bộ Công Thương.

Khung thư Bộ Công Thương (giống nhau ở mọi file, cùng họ với Bộ Văn hóa):
  - Header: "BỘ CÔNG THƯƠNG", "Số: N N N N /BCT-KHTC" (OCR tách RỜI từng
    chữ số bằng space, thỉnh thoảng đọc "1" thành "|": "6 | 0 1 8",
    "6 | 0 | 10 |"), "V/v trả lời kiến nghị ...",
    "Hà Nội, ngày DD tháng MM năm YYYY" (không markdown),
    "Kính gửi:" + danh sách "- ...".
  - Intro: "Bộ Công Thương nhận được kiến nghị của cử tri ... nội dung
    như sau:" -> biên trên vùng NỘI DUNG (S1).
  - Mốc trả lời: "Sau khi nghiên cứu, Bộ Công Thương có ý kiến như sau:"
    -> biên trên vùng TRẢ LỜI (S2).
  - Đáp án file nhiều kiến nghị chia section: "#### Nội dung 1",
    "#### Nội dung 2", ... (luôn là heading Markdown trơ, hết dòng).
  - Câu kết: "Bộ Công Thương trân trọng gửi Đoàn đại biểu Quốc hội ...
    để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát của Quốc hội
    để tổng hợp, theo dõi, giám sát theo quy định./." (đuôi "./."/"./.✓").
  - Cuối thư: "Nơi nhận:" (+ chữ ký "KT. BỘ TRƯỞNG THỨ TRƯỞNG" + tên,
    một số file không có khối chữ ký).

Format Bộ Công Thương (khung f1/f2/f3 chung các bộ, tối ưu riêng):
  - f1: 1 kiến nghị (đoạn văn liền, không đánh số item) + đáp án văn xuôi
    -> 1 petition / thư (14 file).
  - f3: N item kiến nghị "(1)...(N)" trong 1 vùng S1 + đúng N section đáp án
    "#### Nội dung N" -> map item k với block k theo THỨ TỰ (6 file:
    An Giang 2, Hải Phòng 6009 4, Hải Phòng 6024 5, Lâm Đồng 2,
    Quảng Ngãi 6012 2, Đồng Nai 2).
  - f2 (GROUP header lặp lại): KHÔNG xuất hiện ở Bộ Công Thương
    -> giữ nhãn dự phòng, classify không trả f2.
  - llm: thiếu intro/mốc trả lời, hoặc số item và số block đáp án lệch nhau.

Sub-item trong S1 ("a)"-"d)", "(i)"-"(iii)" của An Giang) nằm GIỮA dòng
hoặc mở đầu bằng chữ cái nên neo "^" + yêu cầu số của _S1_ITEM_RE đã loại.

Quy ước tên (giống vh):
  - _INTRO_RE   : dòng intro "... nội dung như sau:" (biên trên S1)
  - _S2_MARK_RE : mốc mở đầu phần TRẢ LỜI (biên trên S2)
  - _S1_ITEM_RE : mốc mở đầu từng kiến nghị "(N)" / "N)" ở đầu dòng (f3)
  - _ANS_HEAD_RE: mốc mở đầu block TRẢ LỜI "#### Nội dung N" (f3)
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
# Biên trên vùng NỘI DUNG (S1): dòng intro kết thúc bằng
# "... nội dung như sau:"
# ---------------------------------------------------------------------------
_INTRO_RE = re.compile(r"nội dung như sau:\s*$", re.I)

# ---------------------------------------------------------------------------
# Mốc mở đầu phần TRẢ LỜI (S2):
#   "Sau khi nghiên cứu, Bộ Công Thương có ý kiến như sau:"
# Dấu phẩy sau "nghiên cứu" và dấu ":" cuối là optional cho file tương lai.
# ---------------------------------------------------------------------------
_S2_MARK_RE = re.compile(
    r"Sau khi nghiên cứu,?\s*Bộ Công Thương có ý kiến như sau:?\s*$",
    re.I,
)

# ---------------------------------------------------------------------------
# (f3) Mốc mở đầu từng kiến nghị trong vùng S1 (giống vh):
#   "(1) Cử tri kiến nghị ..." / "(2) Cử tri kiến nghị ..."
# Neo "^" đầu dòng để KHÔNG bắt sub-item "a)"-"d)" (chữ cái) và
# "(i)"-"(iii)" (chữ La Mã, nằm giữa dòng) của An Giang.
# Vùng tìm kiếm đã giới hạn (intro, s2mark) nên không dính nội dung đáp án.
# ---------------------------------------------------------------------------
_S1_ITEM_RE = re.compile(r"^\s*(?:\(\d+\)|\d+\))\s+\S")

# ---------------------------------------------------------------------------
# (f3) Mốc mở đầu block TRẢ LỜI: heading Markdown trơ, hết dòng:
#   "#### Nội dung 1" ... "#### Nội dung 5"
# Yêu cầu có ít nhất 1 dấu "#" đầu dòng + HẾT DÒNG sau số để không bắt nhầm
# câu văn trong body ("Nội dung ..." không bao giờ đứng đầu dòng kiểu này).
# ---------------------------------------------------------------------------
_ANS_HEAD_RE = re.compile(
    r"^\s*#{1,4}\s*Nội dung\s+\d+\s*$",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư (ranh giới cuối tra_loi) — như nv:
#   "Bộ Công Thương trân trọng gửi Đoàn đại biểu Quốc hội tỉnh/thành phố X
#    để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát ..."
# Khớp phần đầu là đủ (đuôi "./." hay "./.✓" không ràng buộc).
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"trân trọng\s*(?:kính\s*)?g\s*ửi\s+(?:Đoàn\s+đại biểu Quốc hội|Ủy ban Dân nguyện)",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư — như nv:
#   "Nơi nhận:", "KT. BỘ TRƯỞNG THỨ TRƯỞNG" (chứa "KT. BỘ TRƯỞNG"),
#   "Lưu: ...", "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(r"^\s*[#*_ \s]*?(Nơi nhận|(?:KT\.?\s*)?BỘ TRƯỞNG|Lưu:|<!-- Start of picture)", re.I)

# ---------------------------------------------------------------------------
# Dòng CHỨC DANH thuần túy trong khối chữ ký (KHÔNG phải tên người ký):
#   "KT. BỘ TRƯỞNG THỨ TRƯỞNG" / "KT. BỘ TRƯỞNG THỬ TRƯỞNG" (OCR sai "THỬ"),
#   "KT. BỘ TRƯỞNG", "BỘ TRƯỞNG", "THỨ TRƯỞNG" / "THỬ TRƯỞNG"
# (đã strip markdown "#*"). Dùng trong extract_metadata để bỏ qua các dòng
# này và lấy tên ở dòng tiếp theo ("Trương Thanh Hoài",
# "Nguyễn Sinh Nhật Tân").
# ---------------------------------------------------------------------------
_SIGN_TITLE_RE = re.compile(
    r"^(KT\.\s*)?(BỘ\s+TRƯỞNG(\s+TH[ỨỬ]\s+TRƯỞNG)?|TH[ỨỬ]\s+TRƯỞNG)$",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR — như nv (postprocess
# import 2 tên này nên regexes mới phải export chúng).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata: Số công văn (dòng "Số: ..." ở đầu thư).
# OCR Bộ Công Thương tách RỜI từng chữ số bằng space và đọc "1" thành "|":
#   "Số: 6 0 29 /BCT-KHTC"      -> 6029/BCT-KHTC (khớp tên file 6029)
#   "Số: 6 | 0 1 8 /BCT-KHTC"   -> 6018/BCT-KHTC (khớp tên file 6018)
#   "Số: 6 | 0 | 10 | /BCT-KHTC"-> 6010/BCT-KHTC (khớp tên file 6010)
#   "Số: 6 017 /BCT-KHTC"       -> 6017/BCT-KHTC
#   "Số:6 0 2 5 /BCT-KHTC"      (dính "Số:", có space trước "/")
#   "Số: 6 0 2 1 / BCT-KHTC"    (space cả 2 bên "/")
#   "Số: 6 0 0 6/BCT-KHTC"      (không space trước "/")
# Vì vậy phần số gồm [chữ số | space | "|"]; đuôi neo "BCT-KHTC" trên CÙNG
# dòng (class KHÔNG chứa \n để không bắt xuyên dòng — an toàn với các dòng
# body mở đầu bằng "Số ..." như "Số lượng ..." vì chúng không có BCT-KHTC).
# extract_metadata xóa thêm "|" (cùng "*" và khoảng trắng).
# Trường hợp OCR làm hỏng cả nhãn (Lâm Đồng 6019): "Số: 6019" thành
# "S6.6.019" (ố->6, ":"->".") -> nhãn chấp nhận thêm "S" và phần số chấp
# nhận thêm "."; lấy nguyên văn ("6.6.019/BCT-KHTC"), KHÔNG đoán số đúng.
# Nhãn "S" đơn lẻ vẫn an toàn vì đuôi neo "BCT-KHTC" trên cùng dòng (class
# không chứa \n) — dòng body không bao giờ có "BCT-KHTC".
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}(?:Số|S)\*{0,2}[ \t]*[:\-.]?[ \t]*\*{0,2}[ \t]*([\d|. \t]+/?[ \t]*BCT-KHTC)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành — như nv (đủ vì BCT ghi trơn, không markdown):
#   "Hà Nội, ngày 31 tháng 7 năm 2026"
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*\*{0,2}(\d{1,2})\*{0,2}[\s_]*tháng[\s_]*(?:(\d{1,2})[\s_]*)?năm[\s_]*(\d{4})",
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
    Định vị 3 mốc của thư Bộ Công Thương: (i_intro, i_s2, i_end).
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
    Xác định format Bộ Công Thương:
      - "f1": 1 kiến nghị (không item "(N)"/"N)") + đáp án văn xuôi.
      - "f3": N>=2 item kiến nghị "(N)"/"N)" trong 1 vùng S1 + đúng N block
        đáp án "#### Nội dung N" -> map item k với block k theo thứ tự.
      - "f2": không xuất hiện ở Bộ Công Thương (giữ nhãn dự phòng).
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
    Tách vùng đáp án thành các block theo heading "#### Nội dung N".
    Phần mở đầu trước heading đầu (thường chỉ dòng trống) gắn vào block đầu.
    Dòng heading không đưa vào tra_loi.
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
    return _extract_llm(md_text)


def extract_metadata(md_text: str) -> dict:
    """Trích xuất metadata: so_cong_van, ngay_ban_hanh, nguoi_ky."""
    result = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}

    m = _SO_CONG_VAN_RE.search(md_text)
    if m:
        # Xóa bold "*", khoảng trắng (OCR tách rời chữ số) và "|" (OCR đọc
        # sai "1" thành "|": "6 | 0 1 8" -> "6018").
        val = re.sub(r"[\*|\s]+", "", m.group(1))
        result["so_cong_van"] = val

    m = _NGAY_BAN_HANH_RE.search(md_text)
    if m:
        day, month, year = m.groups()
        if day and month:
            result["ngay_ban_hanh"] = f"{int(day):02d}/{int(month):02d}/{year}"

    signer = _extract_signer(md_text)
    result["nguoi_ky"] = signer if signer else None

    return result
