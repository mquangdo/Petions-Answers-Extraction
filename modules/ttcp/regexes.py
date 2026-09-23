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
