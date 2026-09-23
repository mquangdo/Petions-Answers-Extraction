# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị BTCTW
(Ban Tổ chức Trung ương).

Khung thư BTCTW (giống nhau ở mọi file):
  - Header: "# BAN CHẨP/CHÁP HÀNH TRUNG ƯƠNG BAN TÔ CHỨC" (OCR sai dấu:
    CHẤP->CHẨP/CHÁP, TỔ->TÔ), "## ĐẢNG CỘNG SẢN VIỆT NAM" (cá biệt
    "VIẾT NAM"), đôi khi lẫn dòng tiếng Anh / dòng "-" rác do OCR.
  - Số: "Số 1534 - CV/BTCTW" (KHÔNG có dấu ":", nối bằng "-"; có file gộp
    chung dòng subject: "Số 1532 - CV/BTCTW về việc ...").
  - Ngày: "*Hà Nội, ngày 15 tháng 7 năm 2026*" (italic).
  - Intro: "Ban Tổ chức Trung ương nhận được kiến nghị của cử tri ..." +
    "Sau khi nghiên cứu, Ban Tổ chức Trung ương có ý kiến như sau:?"
    (Tuyên Quang, Đà Nẵng... thiếu dấu ":" cuối).
  - Mỗi kiến nghị 1 cặp heading (không có GROUP header riêng):
      S1: "## 1. Nội dung kiến nghị của cử tri"
          "## 1. Nội dung kiến nghị số 1 của cử tri"
          "## 1. Kiến nghị số 1 của cử tri"
      S2: "## 2. Trả lời kiến nghị" (cá biệt "Trả lời kiên nghị" thiếu "h")
          "### Trả lời kiến nghị:"
          "### Trao đổi, giải đáp:"
          "### Trả lời cử tri:"
  - Câu kết: "Ban Tổ chức Trung ương trân trọng gửi Đoàn đại biểu Quốc hội
    ... để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát của Quốc
    hội để tổng hợp, theo dõi, giám sát theo quy định."
  - "Nơi nhân/nhận:" (thường "nhân" thiếu "ậ") + list "- ...; - Lưu VP, TH."
  - Chữ ký: "## K/T TRƯỞNG BAN" + "## PHÓ TRƯỞNG BAN" (biến thể OCR:
    "PHÚ", "NHIỆU", "KT" thiếu "/", "~~PHÓ~~" gạch xóa, gộp 1 dòng) +
    "## Hà Minh Hải"; một số file chỉ còn tên trơ ("*## Hà Minh Hải*").

Format BTCTW (khung f1/f2/f3 chung các bộ, tối ưu riêng):
  - f1: 1 cặp S1+S2 -> 1 kiến nghị / thư (10 file).
  - f2: N cặp S1+S2 (heading kiến nghị lặp "## N. ...") -> N kiến nghị
    (6 file: 10 9 8 x3, 12 13 x2, 14 15 x2, 21 22 x2, 3 4 x3, 6 7 x2).
  - f3 (liệt kê gộp + trả lời theo số): KHÔNG xuất hiện ở BTCTW
    -> giữ nhãn dự phòng, classify không trả f3.
  - llm: không có cặp S1+S2 hiệu lực.

Quy ước tên (giống khcn):
  - _S1_RE : heading kiến nghị (có "kiến nghị", không có "trả lời"/"trao đổi")
  - _S2_RE : heading đáp án ("Trả lời kiến nghị/cử tri", "Trao đổi")
  - _END_RE / _CLOSING_RE : như nv nhưng "Nơi nhân" tolerant + "TRƯỞNG BAN"

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì OCR thường không
đồng nhất về chữ hoa/thường.
"""

import re
import sys
from pathlib import Path

from common import postprocess_noi_dung, postprocess_tra_loi, _clean_text, _extract_signer


# ---------------------------------------------------------------------------
# Heading KIẾN NGHỊ (S1) — 3 dạng quan sát được:
#   "## 1. Nội dung kiến nghị của cử tri"
#   "## 1. Nội dung kiến nghị số 1 của cử tri"
#   "## 1. Kiến nghị số 1 của cử tri"
# Liệt kê tường minh (thay vì phủ định "trả lời") để không bắt nhầm heading
# đáp án — vì đáp án cũng chứa chữ "kiến nghị" ("### Trả lời kiến nghị:").
# ---------------------------------------------------------------------------
_S1_RE = re.compile(
    r"^\s*#{1,4}\s*\d+\.\s*(?:Nội dung\s+kiến\s+ngh[ịi]|Kiến nghị\s+số\s+\d+)\b.*$",
    re.I,
)

# ---------------------------------------------------------------------------
# Heading ĐÁP ÁN (S2) — 4 dạng quan sát được:
#   "## 2. Trả lời kiến nghị"      (số + không dấu ":" )
#   "## 2. Trả lời kiên nghị"      (2.md: OCR rớt "h" -> ki[êếe]n)
#   "### Trả lời kiến nghị:"       (không số, có ":")
#   "### Trao đổi, giải đáp:"      (10 9 8)
#   "### Trả lời cử tri:"          (21 22)
# Yêu cầu mở đầu bằng heading "#" để không bắt nhầm câu văn trong body.
# ---------------------------------------------------------------------------
_S2_RE = re.compile(
    r"^\s*#{1,4}\s*(?:\d+\.\s*)?(?:Trả\s+lời\s+(?:ki[êếe]n\s+ngh[ịi]|cử\s+tr[ìi])|Trao\s+đổi)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư:
#   "Nơi nhân/nhận:" (BTCTW thường "nhân" thiếu "ậ" -> nh[âậa]n),
#   "K/T TRƯỞNG BAN" / "PHÓ TRƯỞNG BAN" (chữ ký; "KT" thiếu "/" vẫn khớp),
#   "Lưu VP, TH." (KHÔNG có ":" như các bộ khác -> Lưu\b),
#   "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(
    r"^\s*[#*_ \s]*?(Nơi\s+nh[âậa]n|(?:K/?T\.?\s*)?TRƯỞNG BAN|Lưu\b|<!-- Start of picture)",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư — như nv:
#   "Ban Tổ chức Trung ương trân trọng gửi Đoàn đại biểu Quốc hội ...
#    để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát ..."
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"trân trọng\s*(?:kính\s*)?g\s*ửi\s+(?:Đoàn\s+đại biểu Quốc hội|Ủy ban Dân nguyện)",
    re.I,
)

# ---------------------------------------------------------------------------
# Dòng CHỨC DANH thuần túy trong khối chữ ký (KHÔNG phải tên người ký):
# chứa "TRƯỞNG BAN" ở mọi biến thể ("K/T TRƯỞNG BAN", "PHÓ/PHÚ/NHIỆU
# TRƯỞNG BAN", "KT TRƯỞNG BAN", "~~PHÓ~~"). Dùng trong extract_metadata để
# bỏ qua và lấy tên ở dòng tiếp theo ("Hà Minh Hải").
# ---------------------------------------------------------------------------
_SIGN_TITLE_RE = re.compile(r"TRƯỞNG BAN", re.I)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR — như nv (postprocess
# import 2 tên này nên regexes mới phải export chúng).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata: Số công văn — "Số 1534 - CV/BTCTW" (KHÔNG dấu ":", nối "-").
# Biến thể: "## Số 1525 - CV/BTCTW" (heading), "Số 1532 - CV/BTCTW về việc
# ..." (gộp subject — capture chỉ lấy "số - CV/BTCTW", dừng trước "về").
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*:?\s*\*{0,2}\s*(\d+\s*-\s*CV/BTCTW)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành — như nv:
#   "*Hà Nội, ngày 15 tháng 7 năm 2026*"
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*\*{0,2}(\d{1,2})\*{0,2}[\s_]*tháng[\s_]*(?:(\d{1,2})[\s_]*)?năm[\s_]*(\d{4})",
    re.I,
)


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
