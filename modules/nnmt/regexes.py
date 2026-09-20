# -*- coding: utf-8 -*-
"""
Tập hợp toàn bộ các REGEX dùng trong pipeline trích xuất kiến nghị.
Tách riêng khỏi functions.py để dễ quản lý, tái sử dụng và kiểm thử.

Quy ước tên:
  - _S2_RE      : mốc mở đầu phần TRẢ LỜI (Kết quả nghiên cứu, giải quyết...)
  - _S1_*       : mốc liên quan phần NỘI DUNG kiến nghị
  - _GROUP_RE   : mốc phân giới khi 1 file có NHIỀU kiến nghị (format 2)
  - _END_RE     : mốc kết thúc phần thư (Nơi nhận / chữ ký / Lưu)
  - _CLOSING_RE : mốc câu kết cuối thư "trân trọng gửi..." (chặn khỏi tra_loi)
  - _ITEM_START_RE / _ANS_HEAD_RE : riêng cho format 3 (liệt kê gộp + trả lời theo số)

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì nội dung OCR thường
không đồng nhất về chữ hoa/thường.
"""

import re

# ---------------------------------------------------------------------------
# Mốc mở đầu phần TRẢ LỜI (S2)
# Dòng tiêu đề phần trả lời của thư, ví dụ:
#   "# II. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị"
#   "**2. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị**"
# ---------------------------------------------------------------------------
_S2_RE = re.compile(r"kết quả nghiên cứu, giải quyết và trả lời kiến nghị", re.I)

# ---------------------------------------------------------------------------
# Bẫy loại trừ S1 (S1 = phần "Nội dung kiến nghị")
# Dòng chứa "nội dung kiến nghị" lại theo sau bởi "được/đang/đã/thuộc" KHÔNG phải
# tiêu đề mục (vd: "Nội dung kiến nghị đang được giải quyết...") mà là câu mô tả
# nằm bên trong phần trả lời -> dùng để LOẠI nó khỏi danh sách S1.
# ---------------------------------------------------------------------------
_S1_RESOLVER_RE = re.compile(r"nội dung kiến nghị[^a-zà-ỹ]*?(?:được|đang|đã|thuộc)\b", re.I)

# ---------------------------------------------------------------------------
# Mốc phân giới giữa các kiến nghị trong cùng 1 thư (format 2)
# 1 file chứa NHIỀU kiến nghị, mỗi kiến nghị mở đầu bằng dòng:
#   "I. Kiến nghị số 50"        (số La Mã + "Kiến nghị số" / "Đối với kiến nghị số")
#   "1. Đối với kiến nghị số 5" (số thường + "Đối với kiến nghị số")
#   "1. Kiến nghị số 51 phụ lục..." (số thường + "Kiến nghị số")
# Đóng vai trò boundary: chặn phần trả lời cũ, mở phần nội dung mới.
# ---------------------------------------------------------------------------
_GROUP_RE = re.compile(
    r"^\s*[#*\-_ ]*\s*(?:[IVXL]+\b[#*_ .]*(?:Kiến nghị số|Đối với kiến nghị số)|\d+\.\s*(?:Kiến nghị số|Đối với kiến nghị số))",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư (không thuộc nội dung trả lời)
# Các dòng sau đây nằm sau phần trả lời và KHÔNG được đưa vào tra_loi:
#   "Nơi nhận:"            (danh sách người nhận)
#   "BỘ TRƯỞNG"           (chữ ký)
#   "Lưu: ..."            (số hồ sơ lưu)
#   "<!-- Start of picture text -->" (ảnh chữ ký)
# ---------------------------------------------------------------------------
_END_RE = re.compile(r"^\s*[#*_ \s]*?(Nơi nhận|BỘ TRƯỞNG|Lưu:|<!-- Start of picture)", re.I)

# ---------------------------------------------------------------------------
# Mốc mở đầu mục "3. Trách nhiệm tiếp tục theo dõi, thông tin kết quả"
# Mục này nằm ở CUỐI phần trả lời (sau mục 2 - Kết quả nghiên cứu ...) và KHÔNG
# được đưa vào tra_loi (đóng vai trò boundary: chặn phần trả lời). Mỗi kiến nghị
# trong file nhiều kiến nghị (format 2) đều có mục 3 RIÊNG của nó; mốc này đảm bảo
# cắt đúng mục của từng petition vì nó nằm trong tập "stops" (boundary = mốc gần
# nhất sau S2 của petition đang xét).
# Biến thể OCR thường gặp:
#   "## 3. Trách nhiệm tiếp tục theo dõi, thông tin kết quả"  (heading markdown)
#   "3. Trách nhiệm tiếp tục theo dõi, thông tin kết quả"      (không heading)
# ---------------------------------------------------------------------------
_TRACH_NHIEM_RE = re.compile(
    r"^\s*[#*_>\s]*\s*\d*\.?\s*Trách nhiệm tiếp tục theo dõi, thông tin kết quả",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR.
# Sau khi OCR, footnote được bóc nhầm vào giữa nội dung và có 2 thành phần:
#   1. Dấu tham chiếu TRONG văn bản: "<sup>1</sup>", "²", "¹", "³", ... -> sẽ bị
#      XÓA khỏi text (không phải nội dung chính).
#   2. Dòng footer GIẢI THÍCH ở cuối trang, bắt đầu bằng "<sup>N</sup>" (thường bị
#      OCR bọc thêm "*"), vd:
#        "*<sup>1</sup> Khoản 1 Điều 94, khoản 1 Điều 110 Luật Đất đai năm 2024*"
#        "*<sup>2</sup> Điều 23 Nghị định số 88/2024/NĐ-CP ...*"
#        "*<sup>[1]</sup> Khoản 3 và khoản 4 Điều 91 Luật Đất đai năm 2024.*"
#      Những dòng này (bắt đầu ở đầu dòng bằng <sup>N</sup>) sẽ bị LOẠI BỎ hoàn toàn.
# Yêu cầu bắt buộc có thẻ "<sup>" ở đầu dòng để KHÔNG nhầm với các dòng danh sách
# đánh số "1. ...", "2. ..." trong nội dung.
# ---------------------------------------------------------------------------
_FOOTNOTE_LINE_RE = re.compile(
    r"^\s*\*{0,2}<sup>\s*(?:\[\d+\]|\d{1,2})\s*</sup>",
    re.I,
)

_SUP_REF_RE = re.compile(
    r"<sup>\s*(?:\[\d+\]|\d{1,2})\s*</sup>|[\u00B2\u00B3\u00B9\u2070-\u2079]",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư
# Câu kết thường đứng GIỮA phần trả lời cuối và mốc "Nơi nhận", ví dụ:
#   "Bộ Nông nghiệp và Môi trường trân trọng gửi Đoàn đại biểu Quốc hội tỉnh X
#    để thông tin tới cử tri; gửi Ủy ban Dân nguyện và Giám sát của Quốc hội..."
# Có 2 biến thể đầu câu:
#   - "trân trọng gửi Đoàn đại biểu Quốc hội ..."
#   - "trân trọng kính gửi Ủy ban Dân nguyện ..." (vd dong thap_104)
# Regex chịu được lỗi OCR tách chữ "g ửi" (khoảng trắng giữa g và ửi).
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"trân trọng\s*(?:kính\s*)?g\s*ửi\s+(?:Đoàn\s+đại biểu Quốc hội|Ủy ban Dân nguyện)",
    re.I,
)

# ---------------------------------------------------------------------------
# (Format 3) Mốc mở đầu từng kiến nghị trong mục LIỆT KÊ GỘP
# File format 3 liệt kê hết các kiến nghị trong 1 mục S1, mỗi kiến nghị bắt đầu
# bằng một dòng, ví dụ:
#   "(1) Kiến nghị số 04: ..."
#   "1.1. Kiến nghị số 80: ..."
#   "Kiến nghị số 37: ..."
# Nhóm (\d+) chính là SỐ kiến nghị dùng để map với phần trả lời.
# Yêu cầu có dấu ":" sau số để không nhầm với heading F1 "(Kiến nghị số 104)".
# ---------------------------------------------------------------------------
_ITEM_START_RE = re.compile(
    r"^\s*[_\*#\- ]*(?:\(\d+\)|\d+\.\d+\.|\d+\.)?\s*[_\*]*Kiến nghị số\s*(\d+)\s*[:：]",
    re.I,
)

# ---------------------------------------------------------------------------
# (Format 3) Mốc mở đầu block TRẢ LỜI theo số
# Phần trả lời được chia theo heading, ví dụ:
#   "2.1. Về kiến nghị số 04 và số 13"
#   "2.1. Đối với kiến nghị số 80"
# Capture ([^:\n]{1,80}) là phần sau chữ "số" (vd "04 và số 13"), từ đó dùng
# re.findall(r"\d+", ...) để lấy TẬP số kiến nghị được trả lời trong block.
# ---------------------------------------------------------------------------
_ANS_HEAD_RE = re.compile(
    r"^\s*[#*_>\s]*\d+\.\d+\.?\s*(?:Về|Đối với)\s*kiến nghị số\s*([^:\n]{1,80})",
    re.I,
)

# ---------------------------------------------------------------------------
# Metadata: Số công văn (trong dòng "Số: ...")
# Các biến thể OCR thường gặp:
#   "Số: 7869 /BNNMT-PC"            (số + space + suffix)
#   "Số: 7993/BNNMT-ĐCKS"           (số + suffix, không space)
#   "## Số:7093 /BNNMT-VPĐP"        (tiền tố heading markdown)
#   "Số 7875 /BNNMT-TSKN"           (không có dấu ":")
#   "**Số:** 7880 /BNNMT-TTTV"      (bold quanh nhãn + dấu ":")
#   "Số: /BNNMT-PC"                 (chỉ suffix, không có số)
# Node: đuôi mã bộ có thể chứa "Đ"/"đ" (QLĐĐ, VPĐP, ĐCKS, ĐĐBĐ...) nên char class
# phải gồm cả Đ/đ (không chỉ A-Z ASCII) nếu không sẽ dừng/di-trước "Đ" và fail.
# ^ đầu dòng: cho phép tiền tố heading "#", bold "*"ốặc "_"; dấu ":" optional.
# Dừng trước "V/v" hoặc hết dòng; trailing "**" được loại bằng \*{0,2}.
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*[:\-]?\s*\*{0,2}\s*([\d]*\s*/[A-Za-z0-9Đđ\-]+(?:\s*[A-Za-z0-9Đđ\-]+)*)\*{0,2}(?=\s*V/v|\s*$)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành (dòng "Hà Nội, ngày DD tháng MM năm YYYY")
# Có thể có dấu gạch dưới từ markdown: "_Hà Nội, ngày_ 17 _tháng       năm 2026_ 7"
# Số tháng CÓ THỂ BỊ OCR LÀM MẤT ("tháng       năm 2026") -> nhóm tháng optional;
# thiếu tháng thì trả null (không đoán).
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*(\d{1,2})[\s_]*tháng[\s_]*(?:(\d{1,2})[\s_]*)?năm[\s_]*(\d{4})",
    re.I,
)

# ---------------------------------------------------------------------------
# Metadata: Người ký ("BỘ TRƯỞNG [Tên]" hoặc chỉ "BỘ TRƯỞNG")
# Các dạng:
#   - "**BỘ TRƯỞNG Trịnh Việt Hùng**" (markdown bold, có tên)
#   - "Nơi nhận :  BỘ TRƯỞNG<br><!-- ... -->" (không tên, theo sau là tag HTML)
# Tên không chứa ký tự "<" để không nuốt tag HTML; lookahead chấp nhận "<".
# ---------------------------------------------------------------------------
_NGUOI_KY_RE = re.compile(
    r"(?:\*\*)?BỘ\s+TRƯỞNG(?:\s+([^\n\r\*<]+?))?(?:\*\*)?(?=\s*(?:\n|<|Nơi nhận|$))",
    re.I,
)