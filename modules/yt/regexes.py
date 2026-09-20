# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị Bộ Y tế.

Khung thư Bộ Y tế (giống nhau ở mọi file):
  - Header: "BỘ Y TÊ" (OCR: TẾ->TÊ), "CỘNG HOÀ ..." (HÒA->HOÀ),
    "**Số:** N /**BYT-VPB**" ("**" có thể bọc cả suffix),
    "*Hà Nội, ngày DD tháng MM năm YYYY*" (cá biệt "ngày07" dính),
    "Kính gửi: Đoàn ĐBQH ...".
  - Intro: "Bộ Y tế nhận được Công văn số 498/UBDNGS16 ..." +
    marker "Bộ Y tế xin trả lời ... cụ thể như sau:" (biến thể bs:
    "... tiếp tục trả lời kiến nghị ... cụ thể như sau:"; biến thể
    Cao Bằng: intro "..., cụ thể như sau:" + mốc đáp án "Sau khi nghiên
    cứu ... xin trân trọng trả lời như sau:").
  - Câu kết: "Bộ Y tế trân trọng kính gửi Đoàn ĐBQH ... để biết, thông tin
    tới cử tri." (Cao Bằng thiếu "kính": "... trân trọng gửi Đoàn ... để
    biết và trả lời cử tri./.") + "Xin trân trọng cảm ơn./.".
  - "Nơi nhận:" + "BỘ TRƯỞNG" + tên (cá biệt thiếu khối chữ ký).

Format Bộ Y tế (khung f1/f2/f3 chung các bộ, tối ưu riêng):
  - f1 (single): 1 kiến nghị không đánh số (đoạn văn mở đầu "Cử tri /
    Đề nghị / Kiến nghị ...", có thể bọc italic "*...*") + đáp án văn xuôi.
  - f2 (multi): >=2 item "N. ..." + đáp án inline đến item tiếp theo.
    Item đáp án rỗng (2 item liền nhau như Tây Ninh L21+L23, Đồng Tháp L21,
    Đồng Nai L17) -> đáp án chung của block sau DÙNG CHO CẢ 2 kiến nghị.
  - f3 (heading): 0-1 item + section đáp án "## N. Về/Đối với" (Cần Thơ có
    "1. Về..." không "##"; Cao Bằng petition kèm bullet "-") -> gộp các
    section thành 1 tra_loi duy nhất.
  - llm: thiếu marker/không tách được petition-đáp án.

Ranh giới item "N. ..." (khác module yt cũ chỉ nhận "Cử tri/Đề nghị"):
  thực tế còn "Kiến nghị" (HP), "Hiện nay" (Lạng Sơn, Thái Nguyên, Phú Thọ),
  "Theo"/"Việc" (Phú Thọ), "Thực hiện" (Tây Ninh). Allowlist này đã verify
  bao hết item thật trên 34 file. Nới lỏng hơn (mọi "N.") sẽ dính bẫy:
  trích dẫn "2. Nhân viên ..."/"3. Người làm ..." (Quảng Trị), liệt kê khảo
  sát "1. (i) ..."/"2. (ii) ..." (Cà Mau).
Sub-answer "(1)/(2)/(3) ...", "## (2) ...", "(i)-(vii)" mở đầu bằng "(" nên
không bao giờ làm boundary.

Quy ước tên:
  - _ITEM_RE      : mở đầu kiến nghị f2 (allowlist, neo "^")
  - _ANS_HEAD_RE  : mở đầu section đáp án f3 ("## N."/"N."/"## (N)" + Về/Đối với)
  - _MARK_RE      : mốc intro/mốc đáp án (biên trên vùng nội dung)
  - _CLOSING_RE   : câu kết cuối thư (chặn khỏi tra_loi)
  - _END_RE       : vùng kết thúc thư (Nơi nhận / chữ ký / cảm ơn / Lưu)

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì OCR thường không
đồng nhất về chữ hoa/thường.
"""

import re

# ---------------------------------------------------------------------------
# Mốc intro / mốc đáp án (biên trên vùng nội dung):
#   "Bộ Y tế xin trả lời đối với kiến nghị ... cụ thể như sau:"
#   "... tiếp tục trả lời kiến nghị ... cụ thể như sau:"   (Lâm Đồng bs)
#   "... có một kiến nghị của cử tri tỉnh Cao Bằng, cụ thể như sau:"
#   "Sau khi nghiên cứu ... Bộ Y tế xin trân trọng trả lời như sau:"
# ---------------------------------------------------------------------------
_MARK_RE = re.compile(
    r"(?:cụ thể như sau:\s*$|xin trân trọng trả lời như sau:\s*$)",
    re.I,
)

# ---------------------------------------------------------------------------
# (f2) Mở đầu kiến nghị: "N. ..." với allowlist đã verify trên 34 file:
#   "1. Cử tri ...", "2. Đề nghị ...", "1. Kiến nghị ...",
#   "1. Hiện nay ...", "1. Theo ...", "2. Việc ...", "4. Thực hiện ..."
# Neo "^" + yêu cầu chữ cái đầu (không phải "(") để loại:
#   - "2. Nhân viên ..."/"3. Người làm ..." (trích dẫn, Quảng Trị);
#   - "1. (i) ..."/"2. (ii) ..." (liệt kê khảo sát, Cà Mau).
# ---------------------------------------------------------------------------
_ITEM_RE = re.compile(
    r"^\s*\d+\.\s+(?:Cử tri|Đề nghị|Kiến nghị|Hiện nay|Theo|Việc|Thực hiện)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# (f3) Mở đầu section đáp án — 3 dạng quan sát được:
#   "## 1. Về ...", "## 2. Đối với ..."   (có "##")
#   "1. Về ..."                            (Cần Thơ: không "##")
#   "## (2) Đối với ..."                   ("##" + ngoặc đơn)
# Dạng "(1) Về ..." TRƠN (không "##") là sub-answer trong đáp án -> LOẠI
# bằng cách: nhánh "##" cho phép ngoặc, nhánh trơn bắt buộc "N." (số + chấm,
# "(" không khớp "\d").
# ---------------------------------------------------------------------------
_ANS_HEAD_RE = re.compile(
    r"^\s*(?:#{1,3}\s*\(?\d+\)?[\.\)]\s+|\d+\.\s+)(?:Về|Đối với)\b",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư (ranh giới cuối tra_loi):
#   "Bộ Y tế trân trọng kính gửi Đoàn ĐBQH ... để biết, thông tin tới cử tri."
#   "Trên đây là ý kiến trả lời ... Bộ Y tế trân trọng gửi Đoàn ĐBQH ...
#    để biết và trả lời cử tri./."   (Cao Bằng)
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(
    r"(?:Bộ Y tế trân trọng\s*(?:kính\s*)?gửi|Trên đây là ý kiến trả lời)",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư:
#   "Nơi nhận:", "BỘ TRƯỞNG", "Xin trân trọng cảm ơn", "Lưu: ...",
#   "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(
    r"^\s*[#*_ \s]*(?:Nơi nhận|BỘ TRƯỞNG|Xin trân trọng cảm ơn|Lưu:|<!-- Start of picture)",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR — giữ nguyên cho postprocess
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
# Metadata: Số công văn
# Các biến thể: "**Số:** 5958 /BYT-VPB", "**Số:** 5911 /**BYT-VPB**"
# ("**" bọc cả suffix -> cho phép \*{0,2} sau "/", như fix ở vh),
# "Số: 5933 /BYT-VPB".
# Dừng trước "V/v" hoặc hết dòng; trailing "**" được loại bằng \*{0,2}.
# ---------------------------------------------------------------------------
_SO_CONG_VAN_RE = re.compile(
    r"^\s*[#*_>\s]*\*{0,2}Số\*{0,2}\s*[:\-]?\s*\*{0,2}\s*([\d]*\s*/\*{0,2}[A-Za-z0-9Đđ\-\&()_.]+(?:\s*[A-Za-z0-9Đđ\-\&()_.]+)*)\*{0,2}(?=\s*V/v|\s*$)",
    re.I | re.M,
)

# ---------------------------------------------------------------------------
# Metadata: Ngày ban hành
# Biến thể: "*Hà Nội, ngày07 tháng 8 năm 2026*" (Hưng Yên: dính "ngày07")
# -> [\s_]* sau "ngày" chịu được dính số.
# ---------------------------------------------------------------------------
_NGAY_BAN_HANH_RE = re.compile(
    r"_?Hà Nội,[\s_]*ngày[\s_]*(\d{1,2})[\s_]*tháng[\s_]*(?:(\d{1,2})[\s_]*)?năm[\s_]*(\d{4})",
    re.I,
)
