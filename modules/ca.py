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
import sys
from pathlib import Path

from common import find_entities, postprocess_noi_dung, postprocess_tra_loi, _clean_text, _extract_signer


# ---------------------------------------------------------------------------
# Mốc mở đầu phần TRẢ LỜI (S2)
# Dòng tiêu đề phần trả lời của thư, ví dụ:
#   "# II. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị"
#   "**2. Kết quả nghiên cứu, giải quyết và trả lời kiến nghị**"
# ---------------------------------------------------------------------------
_S2_RE = re.compile(r"k(?:i)?[êếe]t\s+qu[ảa]\s+nghiên cứu, giải quyết và trả lời kiến ngh[ịi]", re.I)

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
    r"^\s*[#*\-_ ]*\s*(?:"
    r"[IVXLivxl]+\b[#*_ .]*(?:Kiến nghị|Đối với kiến nghị)\s*(?:số\s*)?\d+"
    r"|\d+\.\s*(?:Kiến nghị|Đối với kiến nghị)\s*(?:số\s*)?\d+"
    r")",
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
    r"(?:trân|tràn)\s+trọng\s*(?:kính\s*)?g\s*ửi\s*(?:tới\s*)?(?:Đoàn\s+đại biểu Quốc hội|Ủy ban Dân nguyện|Bộ Nội vụ)",
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
    r"^\s*[_\*#\- ]*(?:\(\d+\)|\d+\.\d+\.?|\d+\.)?\s*[_\*]*Kiến nghị số\s*(\d+)\s*[:：]?",
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
    r"^\s*[#*_>\s]*\d+\.\d+\.?\s*(?:Về|Đối với)?\s*kiến nghị số\s*([^:\n]{1,80})",
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
    r"_?Hà Nội,[\s_]*ngày[\s_]*\*{0,2}(\d{1,2})\*{0,2}[\s_]*tháng[\s_]*(?:\*{0,2}(\d{1,2})\*{0,2}[\s_]*)?năm[\s_]*\*{0,2}(\d{4})\*{0,2}",
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
    r"(?:\*\*)?(?:BỘ\s+TRƯỞNG|Thượng\s+tướng|Trung\s+tướng)(?:\s+([^\n\r\*<]+?))?(?:\*\*)?(?=\s*(?:\n|<|Nơi nhận|$))",
    re.I,
)


def _is_s1(line: str) -> bool:
    low = line.lower()
    if "nội dung kiến nghị" not in low:
        return False
    if _S1_RESOLVER_RE.search(low):
        return False
    s = line.strip()
    return s.startswith("#") or s.startswith("**") or s.startswith("_")


def classify_format(md_text: str):
    """
    Xác định format của file .md đầu vào:
      - "f1": 1 mục "Nội dung kiến nghị" (S1) + 1 mục "Kết quả nghiên cứu, giải
        quyết và trả lời" (S2) -> 1 kiến nghị / thư.
      - "f2": mỗi kiến nghị 1 mục riêng (GROUP header + S1 + S2) -> N kiến nghị.
      - "f3": liệt kê hết các kiến nghị trong 1 mục S1 (dạng "Kiến nghị số X:"),
        trả lời theo heading "2.x. Về/Đối với kiến nghị số ...".
      - "llm": không nhận diện được 3 format chuẩn (không có S1/S2 ->
        cần xử lý bằng LLM).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    # Chỉ s1 nào có s2 THEO SAU mới là section "Nội dung kiến nghị" thật
    # (loại các dòng "2.x Nội dung kiến nghị: ..." nằm trong phần trả lời).
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    if not s1_eff or not s2_pos:
        return "llm"
    if len(s1_eff) == 1 and len(s2_pos) == 1:
        start, end = s1_eff[0], s2_pos[0]
        n_items = (
            sum(1 for l in lines[start:end] if _ITEM_START_RE.search(l))
            if start < end
            else 0
        )
        if n_items >= 2:
            return "f3"
        return "f1"
    return "f2"


def _split_items(slice_lines: list) -> list:
    """Tách vùng S1 thành các kiến nghị liệt kê -> [(số, text_đã_nối), ...]."""
    starts = [i for i, l in enumerate(slice_lines) if _ITEM_START_RE.search(l)]
    if not starts:
        return []
    items = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(slice_lines)
        num = _ITEM_START_RE.search(slice_lines[st]).group(1)
        items.append((num, "\n".join(slice_lines[st:end])))
    return items


def _split_answer_blocks(slice_lines: list) -> list:
    """Tách vùng S2 thành các block trả lời theo heading số -> [(tập_số, text), ...]."""
    heads = [i for i, l in enumerate(slice_lines) if _ANS_HEAD_RE.search(l)]
    if not heads:
        return []
    blocks = []
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(slice_lines)
        nums = {int(x) for x in re.findall(r"\d+", _ANS_HEAD_RE.search(slice_lines[h]).group(1))}
        body = slice_lines[h + 1:end]
        if k == 0:
            body = slice_lines[:h] + body  # phần mở đầu trước heading đầu gắn vào block đầu
        blocks.append((nums, "\n".join(body)))
    return blocks


def _extract_standard(md_text: str) -> list:
    """
    Logic extract chung cho format 1 và 2: mỗi cặp S1+S2 sinh 1 petition.
    File F1 -> 1 phần tử; file F2 -> N phần tử, tự phân giới qua group header
    ("I. Kiến nghị số ..."/"n. Đối với kiến nghị số ...") và khối cuối thư.
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    group_pos = [i for i, line in enumerate(lines) if _GROUP_RE.match(line)]
    end_pos = [i for i, line in enumerate(lines) if _END_RE.match(line)]
    closing_pos = [i for i, line in enumerate(lines) if _CLOSING_RE.search(line)]
    trach_nhiem_pos = [i for i, line in enumerate(lines) if _TRACH_NHIEM_RE.match(line)]
    stops = sorted(set(group_pos + s1_eff + s2_pos + end_pos + closing_pos + trach_nhiem_pos))

    petitions = []
    for j in s2_pos:
        before = [i for i in s1_eff if i < j]
        if not before:
            continue
        i = before[-1]
        boundary = next((k for k in stops if k > j), None)
        noi_dung = "\n".join(lines[i + 1:j])
        tra_loi = "\n".join(lines[j + 1:boundary])
        petitions.append({"noi_dung": postprocess_noi_dung(_clean_text(noi_dung)), "tra_loi": postprocess_tra_loi(tra_loi, _clean_text)})
    return petitions


def _extract_f1(md_text: str) -> list:
    """Format 1: 1 kiến nghị / thư."""
    return _extract_standard(md_text)


def _extract_f2(md_text: str) -> list:
    """Format 2: mỗi kiến nghị 1 mục riêng."""
    return _extract_standard(md_text)


def _extract_f3(md_text: str) -> list:
    """
    Format 3: kiến nghị được liệt kê hết trong 1 mục S1, trả lời theo heading
    "2.x. Về/Đối với kiến nghị số ...". Tách từng kiến nghị và map theo số:
      - item có số thuộc tập số của block nào -> tra_loi = block đó.
      - item không khớp block nào -> tra_loi = toàn bộ phần trả lời (an toàn).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    s1_eff = [i for i in s1_pos if any(j > i for j in s2_pos)]
    group_pos = [i for i, line in enumerate(lines) if _GROUP_RE.match(line)]
    end_pos = [i for i, line in enumerate(lines) if _END_RE.match(line)]
    closing_pos = [i for i, line in enumerate(lines) if _CLOSING_RE.search(line)]
    trach_nhiem_pos = [i for i, line in enumerate(lines) if _TRACH_NHIEM_RE.match(line)]
    stops = sorted(set(group_pos + s1_eff + s2_pos + end_pos + closing_pos + trach_nhiem_pos))

    petitions = []
    for j in s2_pos:
        before = [i for i in s1_eff if i < j]
        if not before:
            continue
        i = before[-1]
        boundary = next((k for k in stops if k > j), None)
        full_answer = "\n".join(lines[j + 1:boundary])
        items = _split_items(lines[i + 1:j])
        blocks = _split_answer_blocks(lines[j + 1:boundary])
        if len(items) >= 2 and blocks:
            for num, item_text in items:
                try:
                    n = int(num)
                except ValueError:
                    n = None
                target = full_answer
                for nums, block_text in blocks:
                    if n is not None and n in nums:
                        target = block_text
                        break
                petitions.append({
                    "noi_dung": postprocess_noi_dung(_clean_text(item_text)),
                    "tra_loi": postprocess_tra_loi(target, _clean_text),
                })
        else:
            petitions.append({
                "noi_dung": postprocess_noi_dung(_clean_text("\n".join(lines[i + 1:j]))),
                "tra_loi": postprocess_tra_loi(full_answer, _clean_text),
            })
    return petitions


def extract_petitions(md_text: str) -> list:
    """
    Router: xác định format của file rồi route đến handler tương ứng.
    Trả về danh sách petition {"noi_dung", "tra_loi"}.

    - f1/f2/f3: xử lý bằng regex (_extract_f1/f2/f3).
    - "llm" (không nhận diện được 3 format chuẩn): xử lý bằng LLM
      (_extract_llm, semantic chunking — 2 LLM calls, cần openai + network).

    LƯU Ý: text đầu vào phải là markdown đã qua post-OCR
    (postprocess.clean_footer + _fix_ocr_diacritics) — do bước OCR đảm nhận.
    """
    fmt = classify_format(md_text)
    print(fmt)
    if fmt == "f1":
        return _extract_f1(md_text)
    if fmt == "f2":
        return _extract_f2(md_text)
    if fmt == "f3":
        return _extract_f3(md_text)
    return []


def extract_metadata(md_text: str) -> dict:
    """Trích xuất metadata từ markdown OCR: so_cong_van, ngay_ban_hanh, nguoi_ky.

    LƯU Ý: text đầu vào phải là markdown đã qua post-OCR
    (postprocess.clean_footer + _fix_ocr_diacritics) — do bước OCR đảm nhận.
    """
    result = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}

    # 1. Số công văn: tìm dòng "Số: ..."
    m = _SO_CONG_VAN_RE.search(md_text)
    if m:
        # Loại bỏ markdown bold (**) và MỌI khoảng trắng; giữ nguyên nếu chỉ có suffix
        val = re.sub(r"\*+", "", m.group(1))
        val = re.sub(r"\s+", "", val)
        result["so_cong_van"] = val

    # 2. Ngày ban hành: "Hà Nội, ngày DD tháng MM năm YYYY" (phải đủ ngày + tháng)
    m = _NGAY_BAN_HANH_RE.search(md_text)
    if m:
        day, month, year = m.groups()
        if day and month:
            result["ngay_ban_hanh"] = f"{int(day):02d}/{int(month):02d}/{year}"

    signer = _extract_signer(md_text)
    result["nguoi_ky"] = signer if signer else None

    return result


if __name__ == "__main__":
    folder = Path("markdown")
    for file_path in folder.glob('*.md'):
        text = file_path.read_text(encoding="utf-8")
        result = find_entities(text)
        print(f"{file_path}: {result}")
        if result:
            assert result["daibieu"], file_path
            assert result["bộ"], file_path
