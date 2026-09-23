# -*- coding: utf-8 -*-
"""
Code dùng chung cho toàn bộ modules/<mã>.py (gom từ code byte-identical
đã verify hash trên cả 13 module: btctw/ca/ct/gddt/khcn/nnmt/nv/qp/tandtc/
ttcp/ubtwmttq/vh/yt).

Nội dung:
  1. Shared regexes: _FOOTNOTE_LINE_RE, _SUP_REF_RE.
  2. Toàn bộ postprocess.py cũ (post-OCR + postprocess noi_dung/tra_loi +
     strip_markdown/normalize_markdown).
  3. Shared functions: _clean_text, _extract_signer (stub, xem TODO),
     _is_header, find_entities, _BO_NAMES, _extract_bo, _MARKERS,
     _extract_daibieu.
  4. Batch runner: extract_one_file / run_batch / batch_cli_main
     (tham số hóa 2 callable extract_petitions/extract_metadata của module).

KHÔNG chứa gì đặc thù Bộ nào. Mọi thứ còn lại (classify, _extract_f*,
extract_petitions router, extract_metadata, regex mốc từng Bộ, _split_items,
_split_answer_blocks...) nằm ở per-module files và KHÔNG được chuyển vào
đây (chúng trỏ tới global riêng từng module — bê nguyên văn sẽ vỡ reference).

File này nằm ở root (cùng cấp functions.py). Mọi modules/<mã>.py đều
`from common import ...` (resolve qua sys.path root hoặc alias
sys.modules["common"] do router/CLI dựng sẵn).
Router (router.py) load file này 1 lần qua alias sys.modules["common"].
"""

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

from markdown_it import MarkdownIt


# ---------------------------------------------------------------------------
# Shared regexes (footnote): byte-identical trên cả 13 module.
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
# Postprocess (nguyên văn postprocess.py cũ — byte-identical trên 13 module).
# ---------------------------------------------------------------------------

_SENTENCE_END = ".!?;:"
_OPEN_PREFIX = "(_\"' "


# ---------------------------------------------------------------------------
# Post-process SAU bước OCR (áp dụng lên nguyên file markdown thô của OCR),
# để extract_petitions/extract_metadata chỉ làm đúng việc trích xuất.
# ---------------------------------------------------------------------------

# Ký tự OCR sinh sai: "e/o + MACRON + ACUTE" thay vì "e/o + CIRCUMFLEX + ACUTE"
# (vd OCR viết "Kḗt quả" thay vì "Kết quả" -> phá regex _S2_RE).
# LƯU Ý: KHÔNG được map ộ(U+1ED9)->ô: "Nội/Hà Nội" đúng chính tả dùng ộ,
# đổi thành ô sẽ hỏng text ("Hà Nội" -> "Hà Nôi") và phá regex metadata.
_OCR_DIACRITICS_MAP = {
    "\u1E17": "\u1EBF",  # ḗ (e+macron+acute) -> ế (e+circumflex+acute)
    "\u1E16": "\u1EBE",  # Ḗ (E+macron+acute) -> Ế (E+circumflex+acute)
    "\u1E53": "\u1ED1",  # ṓ (o+macron+acute) -> ố (o+circumflex+acute)
    "\u1E52": "\u1ED0",  # Ṓ (O+macron+acute) -> Ố (O+circumflex+acute)
}


def _fix_ocr_diacritics(text: str) -> str:
    """Chuẩn hóa ký tự tiếng Việt mà OCR sinh sai về dạng chuẩn."""
    for bad, good in _OCR_DIACRITICS_MAP.items():
        text = text.replace(bad, good)
    return text


def clean_footer(md_text: str) -> str:
    """
    Nhận NGUYÊN file markdown (sau OCR) và loại bỏ footer/chú thích cuối trang.

    Footer sau OCR có 2 thành phần và hàm xử lý cả 2:
      1. Dòng footer GIẢI THÍCH dạng "<sup>N</sup> ..." (thường bị bọc "*"):
           "*<sup>1</sup> Khoản 1 Điều 94, khoản 1 Điều 110 Luật Đất đai năm 2024*"
         -> XÓA cả dòng (kèm dòng trống quanh); nếu footer chen GIỮA câu đang dở
         (dòng sau bắt đầu bằng chữ thường hoặc "(" và dòng trước chưa kết thúc
         câu) thì nối 2 mảnh lại bằng dấu cách (giống remove_footnotes).
      2. Dấu tham chiếu TRONG văn bản: "<sup>N</sup>", "²", "¹", "³", ... -> XÓA.
    """
    lines = md_text.splitlines()
    kept = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FOOTNOTE_LINE_RE.match(line):
            # bỏ dòng trống ngay trước cụm footer (nếu có)
            if kept and not kept[-1].strip():
                kept.pop()
            # bỏ qua toàn bộ cụm footer (các dòng "<sup>" liên tiếp, xen dòng trống)
            i += 1
            while i < len(lines):
                if _FOOTNOTE_LINE_RE.match(lines[i]):
                    i += 1
                    continue
                if not lines[i].strip():
                    i += 1
                    continue
                break
            # bỏ dòng trống ngay sau cụm footer
            while i < len(lines) and not lines[i].strip():
                i += 1
            # nối 2 mảnh câu nếu thỏa điều kiện
            if kept and i < len(lines):
                prev = kept[-1]
                nxt = lines[i]
                if (
                    prev.strip()
                    and not _ends_with_sentence_end(prev)
                    and (_starts_lowercase(nxt) or nxt.lstrip().startswith("("))
                ):
                    kept[-1] = prev.rstrip() + " " + nxt.strip()
                    i += 1
            continue
        kept.append(line)
        i += 1

    # Xóa dấu tham chiếu superscript còn sót trong văn bản
    return _SUP_REF_RE.sub("", "\n".join(kept))


def find_footnotes(raw_text: str) -> list:
    """
    Tìm các dòng footnote trong text thô (còn dấu ">" blockquote).
    Trả về danh sách các dòng khớp.
    """
    return [line for line in raw_text.splitlines() if line.lstrip().startswith(">")]


def has_footnote(raw_text: str) -> bool:
    """Kiểm tra text thô có chứa dòng footnote nào không."""
    return bool(find_footnotes(raw_text))


def _starts_lowercase(s: str) -> bool:
    """Kiểm tra chữ đầu của dòng (bỏ qua ký tự mở ngoặc/trích/gạch chân) là thường."""
    stripped = s.lstrip(_OPEN_PREFIX + "_")
    if not stripped:
        return False
    return stripped[0].islower()


def _ends_with_sentence_end(s: str) -> bool:
    """Kiểm tra dòng kết thúc bằng dấu câu kết đoạn (. ! ? ; :)."""
    return bool(s.rstrip()) and s.rstrip()[-1] in _SENTENCE_END


def _is_footnote_line(line: str) -> bool:
    return line.lstrip().startswith(">")


def remove_footnotes(raw_text: str) -> str:
    """
    Xóa các dòng blockquote (footnote) khỏi text thô cùng dòng trống xung
    quanh chúng.

    Nếu footnote chen GIỮA một câu đang dở (dòng sau bắt đầu bằng chữ thường và
    dòng trước không kết thúc bằng dấu câu) thì nối 2 mảnh lại bằng dấu cách.
    Các footnote liên tiếp được gom thành 1 cụm và chỉ nối 1 lần.
    """
    lines = raw_text.splitlines()
    kept = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_footnote_line(line):
            # bỏ dòng trống ngay trước cụm footnote (nếu có)
            if kept and not kept[-1].strip():
                kept.pop()
            # bỏ qua toàn bộ cụm footnote (các dòng ">" liên tiếp, xen dòng trống)
            i += 1
            while i < len(lines):
                if _is_footnote_line(lines[i]):
                    i += 1
                    continue
                if not lines[i].strip():
                    i += 1
                    continue
                break
            # bỏ dòng trống ngay sau cụm footnote
            while i < len(lines) and not lines[i].strip():
                i += 1
            # nối 2 mảnh câu nếu thỏa điều kiện
            if kept and i < len(lines):
                prev = kept[-1]
                if (
                    prev.strip()
                    and _starts_lowercase(lines[i])
                    and not _ends_with_sentence_end(prev)
                ):
                    kept[-1] = prev.rstrip() + " " + lines[i].strip()
                    i += 1
            continue
        kept.append(line)
        i += 1
    return "\n".join(kept)


def postprocess_tra_loi(raw_tra_loi: str, clean_text) -> str:
    """
    Postprocess field tra_loi:
      1. loại bỏ footnote (blockquote "> ...") khỏi text thô;
      2. chạy _clean_text trên kết quả.
    """
    return clean_text(remove_footnotes(raw_tra_loi))


# ---------------------------------------------------------------------------
# Field "noi_dung": bỏ các dấu thừa bám quanh text.
# ---------------------------------------------------------------------------

# Dấu mở đầu/đóng bao quanh (ngoặc kép "..." hoặc “...”, trích dẫn '...')
_STRIP_HEAD_CHARS = "“”\"'‘’"
_TRAILING_JUNK = "”\"'‘’_"


def clean_noi_dung(text: str) -> str:
    """
    Làm sạch field noi_dung:
      - bỏ dấu ngoặc kép/trích dẫn bao quanh text (“...”, "...", '...');
      - bỏ ký tự italic "_" còn sót của markdown dính ở 2 đầu;
      - gộp dấu chấm câu lặp cuối ("...”." -> "...") và khoảng trắng thừa.
    Ví dụ: “Xem xét ... về môi trường.”."  ->  Xem xét ... về môi trường.
    """
    if not text:
        return text

    s = text.strip()
    # Đầu: bỏ dấu quote mở + italic "_" bám đầu (vd "“ _Cử tri...")
    while s and (s[0] in _STRIP_HEAD_CHARS or s[0] == "_"):
        s = s[1:].lstrip()

    # Cuối: lặp bỏ quote/italic thừa, gộp dấu câu lặp.
    changed = True
    while changed:
        changed = False
        prev = s
        # bỏ quote đóng/italic dính sát cuối
        while s and s[-1] in _TRAILING_JUNK:
            s = s[:-1].rstrip()
        # quote/italic đứng trước dấu câu cuối: "chữ” ."  -> "chữ."
        s = re.sub(r"[”\"'‘’_]\s*([.!?;:])\s*$", r"\1", s)
        # dấu câu trước quote/italic cuối: "chữ. ”"  -> "chữ."
        s = re.sub(r"([.!?;:])\s*[”\"'‘’_]\s*$", r"\1", s)
        # gộp nhiều dấu câu kết liên tiếp: "chữ.."  -> "chữ."
        s = re.sub(r"([.!?;:])\s*[.!?;:]+\s*$", r"\1", s)
        if s != prev:
            changed = True

    return s.strip()


def postprocess_noi_dung(cleaned_noi_dung: str) -> str:
    """
    Postprocess field noi_dung (text ĐÃ qua _clean_text): bỏ dấu thừa quanh text.
    Không lọc footnote ở đây (footnote chỉ xử lý cho field tra_loi).
    """
    return clean_noi_dung(cleaned_noi_dung)


# ---------------------------------------------------------------------------
# Chuyển đổi markdown cho FE (query param "plain_text")
# ---------------------------------------------------------------------------

from markdown_it import MarkdownIt

# Parser chuẩn CommonMark — dùng cho strip_markdown (bỏ format, giữ text).
# Tắt rule "list": văn bản pháp lý có nhiều dòng "1. ...", "11. ..." — nếu
# parse thành ordered list thì SỐ THỨ TỰ bị nuốt mất khi lấy text.
_MD_PARSER = MarkdownIt("commonmark").disable("list")

# Dấu ** / * MỒ CÔI: đứng ngay sau số thứ tự/closing paren ở đầu mục
# vd "(I)**", "1.**", "(4)**" — markdown bold bị OCR tách đôi, không còn cặp đóng
_ORPHAN_STARS_RE = re.compile(r"((?:\(\w+\)|\d+\.|\d+\))\s*)\*{1,2}\s+")

# Dấu heading # lẻ tắt GIỮA câu (không đầu dòng): "Bộ trưởng ### Trịnh" ->
# "Bộ trưởng Trịnh". (?<=\S) đảm bảo không đụng heading hợp lệ ở đầu dòng.
_MID_HEADING_RE = re.compile(r"(?m)(?<=\S)\s+#{1,6}\s+")


def _inline_plain_text(children) -> str:
    """Duyệt token inline của markdown-it, lấy text thuần."""
    out = []
    for tok in children:
        t = tok.type
        if t in ("text", "code_inline"):
            out.append(tok.content)
        elif t == "softbreak":
            out.append(" ")
        elif t == "hardbreak":
            out.append("\n")
        elif t == "image":
            # alt text của ảnh (children chứa token text alt)
            if tok.children:
                out.append(_inline_plain_text(tok.children))
            elif tok.content:
                out.append(tok.content)
        # các token mở/đóng (strong, em, link, s...) -> bỏ qua dấu, giữ text con
    return "".join(out)


def strip_markdown(text: str) -> str:
    """
    Markdown -> plain text bằng parser chuẩn (markdown-it-py): bỏ toàn bộ
    ký hiệu định dạng (bold/italic/heading/link/code/blockquote) nhưng GIỮ
    nguyên xuống dòng và cấu trúc đoạn/câu.

    >>> strip_markdown("thì **Chủ tịch** Ủy ban nhân dân")
    'thì Chủ tịch Ủy ban nhân dân'
    >>> strip_markdown("### Mục I")
    'Mục I'
    >>> strip_markdown("Xem [văn bản](http://x/y.pdf) tại đây")
    'Xem văn bản tại đây'
    >>> strip_markdown("Dòng 1\\n\\nDòng 2")
    'Dòng 1\\n\\nDòng 2'
    >>> strip_markdown("Bộ trưởng ### Trịnh Việt Hùng")
    'Bộ trưởng Trịnh Việt Hùng'
    """
    if not text:
        return text

    # Dọn artifact OCR trước khi parse: ** mồ côi sau số thứ tự + heading #
    # lẻ tắt giữa câu (parser chuẩn coi đây là text thường nên không tự bỏ)
    s = _ORPHAN_STARS_RE.sub(r"\1 ", text)
    s = _MID_HEADING_RE.sub(" ", s)

    tokens = _MD_PARSER.parse(s)
    out = []
    for tok in tokens:
        t = tok.type
        if t == "inline" and tok.children is not None:
            out.append(_inline_plain_text(tok.children))
        elif t in ("fence", "code_block"):
            out.append(tok.content.rstrip("\n"))
            out.append("\n")
        elif t in ("paragraph_close", "heading_close", "table_close"):
            # Paragraph/heading cách nhau bằng 1 dòng trống (giữ cấu trúc đoạn)
            out.append("\n\n")
        elif t == "list_item_close":
            out.append("\n")
    # Gộp nhiều dòng trống liên tiếp thành tối đa 1 dòng trống
    result = re.sub(r"\n{3,}", "\n\n", "".join(out))
    return result.strip()


def normalize_markdown(text: str) -> str:
    """
    Dọn markdown HỎNG do OCR sinh ra, GIỮ markdown hợp lệ.

    - Bỏ dấu ** / * mồ côi sau số thứ tự đầu mục: "(I)** Về..." -> "(I) Về..."
    - Bỏ dấu heading # lẻ tắt GIỮA câu (thường dính trong metadata ngắn,
      vd "Bộ trưởng ### Trịnh Việt Hùng" -> "Bộ trưởng Trịnh Việt Hùng")
    - GIỮ NGUYÊN bold/italic đúng cặp: **Chủ tịch** vẫn giữ nguyên để FE
      render thành chữ đậm.

    >>> normalize_markdown("Bộ trưởng ### Trịnh Việt Hùng")
    'Bộ trưởng Trịnh Việt Hùng'
    >>> normalize_markdown("(I)** Về ban hành Nghị định")
    '(I) Về ban hành Nghị định'
    >>> normalize_markdown("1.** Về kiến nghị")
    '1. Về kiến nghị'
    >>> normalize_markdown("thì **Chủ tịch** Ủy ban nhân dân")
    'thì **Chủ tịch** Ủy ban nhân dân'
    """
    if not text:
        return text

    s = text
    # Dấu ** mồ côi sau số thứ tự đầu mục (thêm lại 1 dấu cách sau group)
    s = _ORPHAN_STARS_RE.sub(r"\1 ", s)
    # Heading # lẻ tắt giữa câu (không đụng heading hợp lệ ở đầu dòng)
    s = _MID_HEADING_RE.sub(" ", s)
    # Gộp khoảng trắng thừa sinh ra sau khi bỏ dấu (trong từng dòng)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s


# ---------------------------------------------------------------------------
# Shared functions (byte-identical trên các module ghi chú — KHÔNG sửa logic
# khi chuyển vào đây).
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        s = re.sub(r"^(#+\s*|\*\*|\*|_+|>\s*)", "", s)
        s = s.strip(" *_#>")
        s = s.replace("<br>", " ").replace("<br/>", " ").replace("</br>", " ")
        s = re.sub(r"<sup>[^<]*</sup>", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        out.append(s)
    result = []
    blank = False
    for l in out:
        if l:
            result.append(l)
            blank = False
        else:
            if not blank:
                result.append("")
            blank = True
    while result and not result[0]:
        result.pop(0)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def _extract_signer(md_text: str) -> str:
    """
    Trích xuất chức vụ và tên người ký từ 20 dòng cuối văn bản bằng model Qwen3 (localhost:8000).
    - Có chức vụ + tên -> '<Chức vụ> <Tên>'
    - Không có chức vụ -> '<Tên>'
    - Không có tên / lỗi -> ''
    """
    if not md_text:
        return ""

    lines = md_text.splitlines()[-20:]
    tail_text = "\n".join(lines).strip()
    if not tail_text:
        return ""

    url = "https://8000--main--dev--sinhnq3.coder.vts-ai.space/v1/chat/completions"
    payload = {
        "model": "Qwen/Qwen3-4B-Instruct-2507",
        "messages": [
            {
                "role": "system",
                "content": (
                    "Bạn là trợ lý trích xuất thông tin văn bản hành chính Việt Nam. "
                    "Nhiệm vụ: Tìm người ký văn bản ở các dòng cuối.\n"
                    "Quy tắc:\n"
                    "1. Nếu có cả Chức vụ và Họ tên: Trả về '<Chức vụ> <Họ tên>'.\n"
                    "2. Nếu có họ tên người ký nhưng không có chức vụ: Trả về nguyên '<Họ tên>'.\n"
                    "3. Chỉ trả về 'NONE' khi hoàn toàn không có tên người nào ở phần ký.\n"
                    "4. Tuyệt đối không giải thích."
                ),
            },
            {
                "role": "user",
                "content": f"Dưới đây là 20 dòng cuối của văn bản:\n```\n{tail_text}\n```\nHọ tên người ký:",
            },
        ],
        "temperature": 0,
        "max_tokens": 50,
    }

    try:
        import httpx
        with httpx.Client(timeout=5.0) as client:
            res = client.post(url, json=payload)
            if res.status_code == 200:
                content = res.json()["choices"][0]["message"]["content"].strip()
                content = content.strip("'\"").strip()
                if content.upper() == "NONE" or len(content) > 60:
                    return ""
                return content
    except Exception:
        return ""

    return ""


def _is_header(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    return (
        s.startswith("#")
        or s.startswith("**")
        or s.startswith("Kính gửi")
        or s.startswith("Số:")
        or s.startswith("-")
    )


def find_entities(text: str):
    """
    Chỉ duyệt các dòng header (heading '#', letterhead '**...**', 'Kính gửi:',
    'Số:', danh sách người nhận '- ...') để giảm số dòng phải duyệt.
    Khi gặp header chứa Đoàn đại biểu Quốc hội (tỉnh hoặc thành phố) và header
    chứa tên Bộ thì trả ngay {"daibieu": ..., "bộ": ...}. Không có thì trả None.
    """
    bo_val = None
    db_val = None
    for line in text.splitlines():
        if not _is_header(line):
            continue
        if bo_val is None and "bộ" in line.lower():
            v = _extract_bo(line)
            if v:
                bo_val = v
        if db_val is None:
            low = line.lower()
            if "đoàn đại biểu quốc hội tỉnh" in low or "đoàn đại biểu quốc hội thành phố" in low:
                v = _extract_daibieu(line)
                if v:
                    db_val = v
        if bo_val and db_val:
            return {"daibieu": db_val, "bộ": bo_val}
    return None


_BO_NAMES = (
    "Bộ Nông nghiệp và Môi trường",
    "Bộ Tài nguyên và Môi trường",
    "Bộ Tài chính",
    "Bộ Công Thương",
    "Bộ Y tế",
    "Bộ Xây dựng",
    "Bộ Giao thông vận tải",
    "Bộ Giáo dục và Đào tạo",
    "Bộ Kế hoạch và Đầu tư",
    "Bộ Văn hóa, Thể thao và Du lịch",
    "Bộ Lao động - Thương binh và Xã hội",
    "Bộ Khoa học và Công nghệ",
    "Bộ Thông tin và Truyền thông",
    "Bộ Tư pháp",
    "Bộ Nội vụ",
    "Bộ Quốc phòng",
    "Bộ Công an",
    "Bộ Ngoại giao",
    "Ban tổ chức Trung ương",
    "Ủy ban Dân nguyện và Giám sát",
)


def _extract_bo(line: str) -> str:
    low = line.lower()
    for name in _BO_NAMES:
        if name.lower() in low:
            return name
    return "Bộ Y tế"


_MARKERS = ("Đoàn đại biểu Quốc hội tỉnh", "Đoàn đại biểu Quốc hội thành phố")


def _extract_daibieu(line: str) -> str:
    low = line.lower()
    for marker in _MARKERS:
        idx = low.find(marker.lower())
        if idx >= 0:
            after = line[idx + len(marker):]
            words = []
            for w in after.split():
                wc = w.strip(";,:.")
                if wc and wc[0].isupper():
                    words.append(wc)
                else:
                    break
            return marker + (" " + " ".join(words) if words else "")
    return "Đoàn đại biểu Quốc hội"


# ---------------------------------------------------------------------------
# Batch runner (logic y hệt batch_extract.py cũ — chỉ khác: 2 callable
# extract_petitions/extract_metadata truyền vào dưới dạng tham số thay vì
# import module-global; và sửa 1 bug tiềm ẩn của bản cũ: nhánh FAIL in
# `path.name` trong khi biến vòng lặp là `md_path` (NameError nếu file lỗi).
# Per-module batch_extract.py giờ chỉ còn shim ~12 dòng gọi batch_cli_main.
# ---------------------------------------------------------------------------

def extract_one_file(md_path: Path, extract_petitions, extract_metadata) -> dict:
    """Doc 1 file .md, trich xuat petition + metadata. Tra ve dict ket qua."""
    text = md_path.read_text(encoding="utf-8")

    petitions = extract_petitions(text)
    metadata = extract_metadata(text)

    return {
        "file": md_path.stem,
        "petitions": petitions,
        "metadata": metadata,
    }


def run_batch(
    input_dir: Path,
    output_dir: Path,
    overwrite: bool,
    extract_petitions,
    extract_metadata,
) -> int:
    """Extract tat ca .md trong input_dir. Trua ve so file THAT BAI."""
    md_files = sorted(input_dir.glob("*.md"))
    if not md_files:
        print(f"Khong co file .md nao trong '{input_dir}'.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)

    tasks_spec = []
    skipped = []
    for md in md_files:
        out_json = output_dir / (md.stem + ".json")
        if out_json.exists() and not overwrite:
            skipped.append(md.name)
            continue
        tasks_spec.append((md, out_json))

    if skipped:
        print(f"Bo qua {len(skipped)} file da co .json (dung --overwrite de extract lai):")
        for name in skipped:
            print(f"  - {name}")

    if not tasks_spec:
        print("Khong con file nao can extract.")
        return 0

    print(f"Extract {len(tasks_spec)}/{len(md_files)} file -> '{output_dir}/'\n")

    failed = 0
    start_time = time.monotonic()
    total_petitions = 0

    for md_path, out_json in tasks_spec:
        try:
            result = extract_one_file(md_path, extract_petitions, extract_metadata)
            out_json.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            n_pet = len(result["petitions"])
            total_petitions += n_pet
            print(f"  [OK]   {md_path.name}  ({n_pet} petition(s))")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {md_path.name}: {e}")

    elapsed = time.monotonic() - start_time
    done = len(tasks_spec) - failed
    print(
        f"\nHoan tat: {done} thanh cong, {failed} that bai, "
        f"{total_petitions} petition(s) tong cong ({elapsed:.1f}s)"
    )
    return failed


def batch_cli_main(extract_petitions, extract_metadata) -> int:
    """CLI dùng chung: parse -i/-o/--overwrite, chạy batch, trả exit code
    (0 = hết thành công, 1 = có file thất bại). Shim per-module gọi hàm này."""
    parser = argparse.ArgumentParser(
        description="Trich xuat petition & metadata tu file .md hang loat."
    )
    parser.add_argument(
        "-i", "--input-dir", type=Path, required=True,
        help="Folder chua file .md dau vao",
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, required=True,
        help="Folder ghi ket qua .json dau ra",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Extract lai ca nhung file da co .json trong output",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not args.input_dir.is_dir():
        raise SystemExit(f"Folder dau vao khong ton tai: '{args.input_dir}'")

    failed = run_batch(
        args.input_dir, args.output_dir, args.overwrite,
        extract_petitions, extract_metadata,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    # CLI batch theo Bộ (thay 13 lệnh cũ `python modules/<mã>/batch_extract.py`):
    #   python common.py --module <mã> -i <input_dir> -o <output_dir> [--overwrite]
    _here = Path(__file__).resolve().parent / "modules"
    _valid = sorted(p.stem for p in _here.glob("*.py"))
    _cli = argparse.ArgumentParser(
        description="Batch extract theo Bộ (chọn module bằng --module).",
    )
    _cli.add_argument(
        "--module", required=True, choices=_valid,
        help="Mã module (file modules/<mã>.py)",
    )
    _cli.add_argument(
        "-i", "--input-dir", type=Path, required=True,
        help="Folder chứa file .md đầu vào",
    )
    _cli.add_argument(
        "-o", "--output-dir", type=Path, required=True,
        help="Folder ghi kết quả .json đầu ra",
    )
    _cli.add_argument(
        "--overwrite", action="store_true",
        help="Extract lại cả những file đã có .json trong output",
    )
    _args = _cli.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not _args.input_dir.is_dir():
        raise SystemExit(f"Folder đầu vào không tồn tại: '{_args.input_dir}'")

    # File <mã>.py `from common import ...` nên alias __main__ (chính file
    # này) thành "common" TRƯỚC khi load — tránh exec common.py lần 2.
    sys.modules.setdefault("common", sys.modules["__main__"])
    _spec = importlib.util.spec_from_file_location(
        f"modules_{_args.module}", _here / f"{_args.module}.py"
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

    _failed = run_batch(
        _args.input_dir, _args.output_dir, _args.overwrite,
        _mod.extract_petitions, _mod.extract_metadata,
    )
    raise SystemExit(1 if _failed else 0)
