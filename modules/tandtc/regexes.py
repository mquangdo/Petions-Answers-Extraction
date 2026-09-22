# -*- coding: utf-8 -*-
"""
Tập hợp REGEX dùng trong pipeline trích xuất kiến nghị của Tòa án nhân dân
tối cao.

Khung thư (giống nhau ở mọi file):
  - Header: letterhead ("TÒA ÁN NHÂN DÂN TỐI CAO", hay bị OCR thành
    "TÔI/TỔI CAO"), "Số: NNNN...", "V/v ...", "Hà Nội, ngày DD tháng MM
    năm YYYY", "Kính gửi: Đoàn ĐBQH ...".
  - Intro: "Tòa án nhân dân tối cao nhận được kiến nghị ... [liệt kê] ...
    ... trả lời như sau:" (biên intro).
  - Items (mỗi item là 1 kiến nghị, đáp án văn xuôi sau nó):
      "N. Đối với nội dung kiến nghị: ..."   (KN3)
      "**N.** Đối với nội dung thứ nhất/thứ hai:" (KN 1,2)
  - Câu kết: "Trên đây là ..." / "Trân trọng./.".
  - Cuối thư: "Nơi nhận:", "CHÁNH ÁN" + tên ("Nguyễn Văn Quảng").

Format (khung f2/llm, tối ưu riêng):
  - f2: N>=1 item -> mỗi item + đáp án sau nó là 1 petition.
  - f1: không dùng ở TANDTC (giữ nhãn dự phòng).
  - llm: thiếu intro/item/end.

Quy ước tên:
  - _INTRO_RE   : dòng intro "... trả lời như sau:" (biên trên đáp án)
  - _ITEM_RE    : item "N. Đối với nội dung ..." ở đầu dòng (f2)
  - _CLOSING_RE : câu kết cuối thư (chặn khỏi tra_loi)
  - _END_RE     : vùng kết thúc thư (Nơi nhận / chữ ký / Lưu)

Tất cả đều dùng cờ re.I (không phân biệt hoa thường) vì OCR thường không
đồng nhất về chữ hoa/thường.
"""

import re

# ---------------------------------------------------------------------------
# Biên intro: dòng kết thúc bằng "... trả lời như sau:"
#   "... Tòa án nhân dân tối cao trả lời như sau:"                    (KN3)
#   "... Đối với 02 nội dung nêu trên, ... trả lời như sau:"          (KN 1,2)
# ---------------------------------------------------------------------------
_INTRO_RE = re.compile(r"trả lời như sau:\s*$", re.I)

# ---------------------------------------------------------------------------
# (f2) Item kiến nghị ở đầu dòng:
#   "1. Đối với nội dung kiến nghị: ..."        (KN3)
#   "**1.** Đối với nội dung thứ nhất:"         (KN 1,2)
# Neo "^" + "Đối với nội dung" để KHÔNG bắt các dòng "(1) Quyết định ..."
# trong đáp án (trích dẫn footnote nội bộ).
# ---------------------------------------------------------------------------
_ITEM_RE = re.compile(
    r"^\s*(?:\*\*)?\d+\.\s*(?:\*\*)?\s*Đối với nội dung",
    re.I,
)

# ---------------------------------------------------------------------------
# Mốc câu kết cuối thư (ranh giới cuối tra_loi):
#   "Trên đây là trả lời ..." / "Trên đây là ý kiến trả lời ..." (cả 2 file)
#   "Trân trọng./."                                             (KN 1,2)
# "Trân trọng" neo "^" đầu dòng để KHÔNG bắt "trân trọng cảm ơn" giữa câu.
# ---------------------------------------------------------------------------
_CLOSING_RE = re.compile(r"Trên đây là|^\s*Trân trọng", re.I)

# ---------------------------------------------------------------------------
# Mốc kết thúc phần thư (không thuộc nội dung trả lời)
#   "Nơi nhận:" ("#### Nơi nhận:", "*Nơi nhận:*"), "CHÁNH ÁN",
#   "Lưu: ...", "<!-- Start of picture ... -->"
# ---------------------------------------------------------------------------
_END_RE = re.compile(
    r"^\s*[#*_ \s]*?(Nơi nhận|CHÁNH ÁN|Lưu:|<!-- Start of picture)",
    re.I,
)

# ---------------------------------------------------------------------------
# Footer / chú thích cuối trang (footnote) sau OCR. Copy chuẩn dùng chung
# các module (giữ đồng nhất để postprocess.clean_footer hoạt động).
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
# Metadata: Số công văn (trong dòng "Số: ..." ở header)
#   "Số: 727TANDTC-TCCB"    (không slash, không space)
#   "Số: 488/TANDTC-VP"
# ^ đầu dòng + nhãn "Số" để KHÔNG bắt "Công văn số 498/UBDNGS16",
# "Quy định số 301-QĐ/TW" trong thân. Lấy token đầu tiên sau nhãn.
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
# Metadata: Người ký. Chữ ký TANDTC:
#   "## CHÁNH ÁN" + tên dòng heading kế tiếp ("## Nguyễn Văn Quảng").
# Trả về tên (không chức danh), khớp ví dụ response schema "nguoi_ky".
# ---------------------------------------------------------------------------
_SIGN_TITLE_RE = re.compile(r"CHÁNH ÁN", re.I)
