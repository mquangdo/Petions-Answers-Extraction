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
import sys
from pathlib import Path

from common import postprocess_noi_dung, postprocess_tra_loi, _clean_text, _extract_signer


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
    Định vị vùng nội dung thư Bộ Y tế: (i_mark, i_end).
    i_mark = marker đầu tiên ("... cụ thể như sau:" / "... trân trọng trả lời
    như sau:"); i_end = closing/END đầu tiên sau marker.
    Trả về (None, None) nếu thiếu marker.
    """
    lines = md_text.splitlines()
    marks = [i for i, l in enumerate(lines) if _MARK_RE.search(l)]
    if not marks:
        return None, None
    i_mark = marks[0]
    i_end = _find_closing(lines, i_mark)
    return i_mark, i_end


def classify_format(md_text: str) -> str:
    """
    Xác định format Bộ Y tế:
      - "f1": 0 item đánh số + 0 section đáp án -> 1 kiến nghị không đánh số
        (đoạn văn sau marker) + đáp án văn xuôi. Riêng Cà Mau có đúng 1 item
        "1. ..." (các liệt kê "1. (i)..." là sub, không tính) -> cũng f1.
      - "f2": >=2 item "N. ..." (allowlist Cử tri/Đề nghị/Kiến nghị/Hiện nay/
        Theo/Việc/Thực hiện) + đáp án inline.
      - "f3": <=1 item + >=1 section "## N. Về/Đối với" (Cần Thơ "1. Về..."
        không "##") -> 1 kiến nghị, gộp sections thành 1 tra_loi.
      - "llm": thiếu marker, hoặc không tách được petition-đáp án.
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return "llm"
    region = lines[i_mark + 1:i_end]
    n_items = sum(1 for l in region if _ITEM_RE.match(l))
    n_sec = sum(1 for l in region if _ANS_HEAD_RE.match(l))
    if n_items >= 2:
        return "f2"
    if n_sec >= 1:
        return "f3"
    return "f1"


def _split_first_para(block_lines: list):
    """
    Tách block thành (đoạn đầu, phần còn lại) tại dòng trống đầu tiên.
    noi_dung = đoạn văn đầu (kiến nghị), tra_loi = phần còn lại (đáp án).
    """
    noi, tra = [], []
    in_tra = False
    for line in block_lines:
        if not in_tra and not line.strip() and noi:
            in_tra = True
            continue
        (tra if in_tra else noi).append(line)
    return "\n".join(noi).strip(), "\n".join(tra).strip()


def _extract_f1(md_text: str) -> list:
    """
    f1: 1 kiến nghị / thư.
    - 0 item: noi_dung = đoạn văn đầu sau marker, tra_loi = phần còn lại.
    - 1 item (Cà Mau): xử lý như 1 block f2 (đoạn đầu = noi_dung).
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return []
    region = lines[i_mark + 1:i_end]
    starts = [i for i, l in enumerate(region) if _ITEM_RE.match(l)]
    block = region[starts[0]:] if starts else region
    noi_dung, tra_loi = _split_first_para(block)
    if not noi_dung or not tra_loi:
        return []
    return [{
        "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
        "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
    }]


def _extract_f2(md_text: str) -> list:
    """
    f2: >=2 item "N. ..." + đáp án inline đến item tiếp theo.
    Quy tắc trả lời gộp: item nào đáp án rỗng (2 item liền nhau như Tây Ninh
    L21+L23, Đồng Tháp L21, Đồng Nai L17) thì đáp án chung của block sau
    DÙNG CHO CẢ 2 kiến nghị (gán chứ không di chuyển).
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return []
    region = lines[i_mark + 1:i_end]
    starts = [i for i, l in enumerate(region) if _ITEM_RE.match(l)]
    if not starts:
        return []
    bounds = starts + [len(region)]
    pairs = []
    for k in range(len(starts)):
        block = region[bounds[k]:bounds[k + 1]]
        noi_dung, tra_loi = _split_first_para(block)
        pairs.append([noi_dung, tra_loi])
    # Trả lời gộp: block rỗng đáp án lấy đáp án block kế tiếp (giữ cho cả 2).
    for k in range(len(pairs) - 1):
        if pairs[k][0] and not pairs[k][1] and pairs[k + 1][1]:
            pairs[k][1] = pairs[k + 1][1]
    petitions = []
    for noi_dung, tra_loi in pairs:
        if not noi_dung or not tra_loi:
            continue
        petitions.append({
            "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
            "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
        })
    return petitions


def _extract_f3(md_text: str) -> list:
    """
    f3: 0-1 item + section đáp án "N. Về/Đối với" -> 1 kiến nghị, gộp các
    section (kèm preamble trước section đầu như Cao Bằng) thành 1 tra_loi.
    Dòng mốc đáp án phụ (Cao Bằng "... trân trọng trả lời như sau:") không
    đưa vào noi_dung.
    """
    lines = md_text.splitlines()
    i_mark, i_end = _locate(md_text)
    if i_mark is None:
        return []
    region = lines[i_mark + 1:i_end]
    marks_rel = [i for i, l in enumerate(region) if _MARK_RE.search(l)]
    heads = [i for i, l in enumerate(region) if _ANS_HEAD_RE.match(l)]
    if not heads:
        return []
    pet_end = marks_rel[0] if marks_rel else heads[0]
    noi_dung = "\n".join(region[:pet_end]).strip()
    ans_start = (marks_rel[0] + 1) if marks_rel else heads[0]
    tra_loi = "\n".join(region[ans_start:]).strip()
    if not noi_dung or not tra_loi:
        return []
    return [{
        "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
        "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
    }]


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
