# -*- coding: utf-8 -*-
"""
Các hàm postprocess áp dụng lên kết quả trích xuất kiến nghị.

- Field "tra_loi": loại bỏ footnote/chú thích cuối trang. Trong file .md gốc,
  footnote luôn là dòng blockquote bắt đầu bằng ">" (OCR bóc nhầm vào giữa nội
  dung). Cần lọc TRƯỚC khi _clean_text bóc dấu ">", nên các hàm nhận text THÔ
  (raw section lines).
- Field "noi_dung": làm sạch dấu thừa bám quanh text (dấu ngoặc kép/trích dẫn
  bao quanh, ký tự italic "_" còn sót của markdown).
- Post-OCR (sau bước OCR, trước khi lưu .md / trước khi trích xuất):
    - _fix_ocr_diacritics: chuẩn hóa ký tự tiếng Việt mà OCR sinh sai.
    - clean_footer: loại bỏ chú thích cuối trang (footnote) mà OCR bóc vào
      giữa nội dung.
"""

import re

from regexes import _FOOTNOTE_LINE_RE, _SUP_REF_RE

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
