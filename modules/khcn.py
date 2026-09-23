# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị
Bộ Khoa học và Công nghệ.

Khung thư Bộ KH&CN (giống nhau ở mọi file / mọi đoạn thư):
  - Header: "BỘ KHOA HỌC VÀ CÔNG NGHỆ", "Số: NNNN/BKHCN-VP" (số thường bị
    OCR đọc sai chữ số thành chữ cái: 6174->"6H4", 6182->"G182", ...),
    "V/v trả lời kiến nghị ...", "Hà Nội, ngày DD tháng MM năm YYYY"
    (ngày/tháng có thể bị bọc "**"), "Kính gửi:" + danh sách "- ..." .
  - Mỗi kiến nghị 1 cặp mục (không có GROUP hoặc có GROUP "# I./II./...
    Kiến nghị số N"):
      "## 1. Nội dung kiến nghị"                      (S1)
      "## 2. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị"  (S2)
  - Bẫy trong vùng đáp án (KHÔNG phải S1):
      "### Nội dung kiến nghị được/đang/đã ... giải trình/cung cấp ..."
      "*Nội dung kiến nghị đã được giải quyết thông qua ...*"
  - Câu kết: "Bộ Khoa học và Công nghệ trân trọng gửi Đoàn đại biểu
    Quốc hội ... để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát
    của Quốc hội để tổng hợp, theo dõi, giám sát theo quy định.//"
  - Cuối thư: "Nơi nhận:", "BỘ TRƯỞNG", tên người ký.

Format Bộ KH&CN (khung f1/f2/f3 chung các bộ, tối ưu riêng):
  - f1: 1 cặp S1+S2 (không GROUP header) -> 1 kiến nghị / thư.
  - f2: N GROUP header ("# I. Kiến nghị số 1", ...), mỗi GROUP 1 cặp
    S1+S2 riêng -> N kiến nghị.
  - f3 (liệt kê gộp + trả lời theo số): KHÔNG xuất hiện ở Bộ KH&CN
    -> giữ nhãn dự phòng, classify không trả f3.
  - llm: không có cặp S1+S2 hiệu lực.

Quy ước tên (giống nv/nnmt):
  - _S2_RE      : mốc mở đầu phần TRẢ LỜI (copy nv, đã tolerant dấu)
  - _S1_RE      : heading "Nội dung kiến nghị" TRẦN (khác nv: KHÔNG yêu cầu
    hậu tố "của cử tri"/"số" vì KH&CN ghi "## 1. Nội dung kiến nghị" cụt)
  - _S1_RESOLVER_RE : bẫy loại trừ các dòng "Nội dung kiến nghị được/đang/
    đã/thuộc ..." trong vùng đáp án (copy nv)
  - _GROUP_RE   : "# I. Kiến nghị số N" (copy nv)
  - _END_RE / _CLOSING_RE : copy nv (khớp "Nơi nhận", "BỘ TRƯỞNG",
    "trân trọng gửi Đoàn đại biểu Quốc hội")

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì OCR thường không
đồng nhất về chữ hoa/thường.
"""

import re
import sys
from pathlib import Path

from common import postprocess_noi_dung, postprocess_tra_loi, _clean_text, _extract_signer, _extract_llm


# ---------------------------------------------------------------------------
# Mốc mở đầu phần TRẢ LỜI (S2) — copy nv (đã tolerant lỗi dấu KÊt/KÉt):
#   "## 2. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị"
# ---------------------------------------------------------------------------
_S2_RE = re.compile(
    r"k[ÊÉếe]t\s+qu[ảa]\s+nghiên cứu,?\s+giải quyết\s+và\s+trả\s+lời\s+kiến\s+ngh[ịi]",
    re.I,
)

# ---------------------------------------------------------------------------
# Bẫy loại trừ S1 — copy nv:
# Dòng chứa "nội dung kiến nghị" lại theo sau bởi "được/đang/đã/thuộc" KHÔNG phải
# tiêu đề mục mà là câu mô tả trong vùng đáp án, ví dụ:
#   "### Nội dung kiến nghị đang được giải trình, cung cấp thông tin"
#   "*Nội dung kiến nghị đã được giải quyết thông qua hoạt động quản lý nhà nước*"
# ---------------------------------------------------------------------------
_S1_RESOLVER_RE = re.compile(r"nội dung kiến nghị[^a-zà-ỹ]*?(?:được|đang|đã|thuộc)\b", re.I)

# ---------------------------------------------------------------------------
# Heading "Nội dung kiến nghị" (S1) của Bộ KH&CN:
#   "## 1. Nội dung kiến nghị"
# Khác nv ở chỗ KHÔNG yêu cầu hậu tố "của cử tri"/"số" (KH&CN luôn ghi cụt).
# Yêu cầu HẾT DÒNG sau cụm "kiến nghị" (cho phép markdown "*" "_" đuôi) để
# loại các bẫy có chữ theo sau ("... được/đang/đã ..."); kết hợp thêm
# _S1_RESOLVER_RE trong _is_s1 thành 2 lớp bảo vệ.
# ---------------------------------------------------------------------------
_S1_RE = re.compile(
    r"^\s*[#*_>\s]*\d*\.?\s*[#*_>\s]*Nội dung\s+kiến\s+ngh[ịi]\s*[*_]*\s*$",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc phân giới giữa các kiến nghị trong cùng 1 thư (format 2) — copy nv:
#   "# I. Kiến nghị số 1" ... "# IV. Kiến nghị số 4"
# (số La Mã + "Kiến nghị số"; nhánh số thường "n. Đối với/Kiến nghị số"
# giữ lại để tương thích file tương lai).
# ---------------------------------------------------------------------------
_GROUP_RE = re.compile(
    r"^\s*[#*\-_ ]*\s*(?:[IVXL]+\b[#*_ .]*(?:Kiến nghị số|Đối với kiến nghị số)|\d+\.\s*(?:Kiến nghị số|Đối với kiến nghị số))",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư — copy nv:
#   "Nơi nhận:", "BỘ TRƯỞNG", "Lưu: ...", "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(r"^\s*[#*_ \s]*?(Nơi nhận|(?:KT\.?\s*)?BỘ TRƯỞNG|Lưu:|<!-- Start of picture)", re.I)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư — copy nv:
#   "Bộ Khoa học và Công nghệ trân trọng gửi Đoàn đại biểu Quốc hội ...
#    để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát ..."
# Khớp phần đầu là đủ (đuôi "./." hay ".//" không ràng buộc).
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"trân trọng\s*(?:kính\s*)?g\s*ửi\s+(?:Đoàn\s+đại biểu Quốc hội|Ủy ban Dân nguyện)",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR — copy nv (postprocess
# import 2 tên này nên regexes mới phải export chúng).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata: Số công văn (dòng "Số: ..." ở đầu thư).
# OCR Bộ KH&CN đọc sai nặng và KHÔNG nhất quán:
#   "Số: **6H4**/BKHCN-VP"    (6174 -> "6H4": số 1, 7 thành H)
#   "Số: **6 HFB**BKHCN-VP"   (mất "/", space trong số, không "/")
#   "Số: **6478** BKHCN-VP"   (mất "/", space thay "/"; số cũng sai)
#   "Số: **G182/BKHCN-VP**"   (6 -> G; bold bọc cả suffix)
#   "Số: **647** / BKHCN-VP"  (space quanh "/")
#   "Số: 6180/BKHCN-VP"       (sạch)
# Vì vậy KHÔNG yêu cầu "số thuần chữ số + /" như nv; chỉ neo 2 đầu:
#   đầu "Số" ở đầu dòng (mod markdown) và đuôi "BKHCN-VP" trên CÙNG dòng
#   (class KHÔNG chứa \n để không bắt xuyên dòng). Phần giữa lấy nguyên
#   văn OCR (kể cả chữ cái/dấu sao/space); extract_metadata chỉ xóa "*"
#   và khoảng trắng — KHÔNG đoán số đúng (vd không sửa "6H4" thành "6174").
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*[:\-]?\s*\*{0,2}\s*([A-Za-z0-9Đđ\* \t]*?/?[ \t]*\*{0,2}BKHCN-VP)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành — như nv nhưng thêm \*{0,2} quanh THÁNG vì KH&CN
# bọc bold cả tháng: "Hà Nội, ngày **14** tháng **8** năm 2026".
# Dạng thường "*Hà Nội, ngày 14 tháng 8 năm 2026*" vẫn khớp (stars optional).
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*\*{0,2}(\d{1,2})\*{0,2}[\s_]*tháng[\s_]*\*{0,2}(?:(\d{1,2})\*{0,2}[\s_]*)?năm[\s_]*(\d{4})",
    re.I,
)

# ---------------------------------------------------------------------------
# Metadata: Người ký ("BỘ TRƯỞNG [Tên]" hoặc chỉ "BỘ TRƯỞNG") — copy nv.
# Ở KH&CN tên nằm dòng riêng ("## BỘ TRƯỞNG" / "## Vũ Hải Quân").
# ---------------------------------------------------------------------------
_NGUOI_KY_RE = re.compile(
    r"(?:\*\*)?BỘ\s+TRƯỞNG(?:\s+([^\n\r\*<]+?))?(?:\*\*)?(?=\s*(?:\n|<|Nơi nhận|$))",
    re.I,
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
    return _extract_llm(md_text)


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
