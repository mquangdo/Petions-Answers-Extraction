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
_FOOTNOTE_LINE_RE = re.compile(
    r"^\s*\*{0,2}<sup>\s*(?:\[\d+\]|\d{1,2})\s*</sup>",
    re.I,
)

_SUP_REF_RE = re.compile(
    r"<sup>\s*(?:\[\d+\]|\d{1,2})\s*</sup>|[\u00B2\u00B3\u00B9\u2070-\u2079]",
    re.I,
)

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
