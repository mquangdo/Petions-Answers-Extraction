import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _ANS_MARK_RE,
    _CLOSING_RE,
    _END_RE,
    _GROUP_RE,
    _INTRO_RE,
    _ITEM_RE,
    _NGAY_BAN_HANH_RE,
    _SIGN_TITLE_RE,
    _SO_CONG_VAN_RE,
)


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


def _find_end(lines: list, start: int) -> int:
    """Tìm vị trí closing (câu kết / end marker) đầu tiên sau start."""
    for i in range(start, len(lines)):
        if _CLOSING_RE.search(lines[i]):
            return i
        if _END_RE.match(lines[i]):
            return i
    return len(lines)


def _locate(md_text: str):
    """
    Định vị 2 mốc của Báo cáo TTCP: (i_intro, i_end).
    Trả về (None, None) nếu thiếu dòng tiêu đề BÁO CÁO.
    """
    lines = md_text.splitlines()
    i_intro = next((i for i, l in enumerate(lines) if _INTRO_RE.search(l)), None)
    if i_intro is None:
        return None, None
    i_end = _find_end(lines, i_intro + 1)
    return i_intro, i_end


def _item_ranges(lines: list, start: int, end: int) -> list:
    """Tìm các dòng mở đầu item "Câu hỏi/hồi N." trong [start, end)."""
    return [i for i in range(start, end) if _ITEM_RE.match(lines[i])]


def _strip_ans_mark(text: str) -> str:
    """Bóc mốc "Trả lời:" ở đầu đáp án (giữ nội dung trả lời).
    Chỉ bóc PREFIX marker, KHÔNG pop cả dòng (đáp án có thể mở đầu ngay sau
    "Trả lời:" trên cùng dòng, vd "Trả lời: Luật Khiếu nại quy định...").
    """
    return re.sub(r"\A\s*(?:#+\s*)?Trả lời\s*:\s*", "", text)


def classify_format(md_text: str) -> str:
    """
    Xác định format Báo cáo TTCP:
      - "f2": N>=1 item "Câu hỏi/hồi N." -> mỗi item + đáp án sau "Trả lời:"
        là 1 petition.
      - "f1": không dùng ở TTCP (giữ nhãn dự phòng).
      - "llm": thiếu intro/item/end.
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return "llm"
    items = _item_ranges(lines, i_intro + 1, i_end)
    if not items:
        return "llm"
    return "f2"


def _extract_f2(md_text: str) -> list:
    """
    f2: N item, mỗi item + đáp án sau nó là 1 petition. Đáp án kéo dài tới
    item/GROUP kế tiếp hoặc câu kết/end (Nơi nhận/chữ ký). Bỏ cặp rỗng.
    """
    lines = md_text.splitlines()
    i_intro, i_end = _locate(md_text)
    if i_intro is None:
        return []
    starts = _item_ranges(lines, i_intro + 1, i_end)
    if not starts:
        return []
    # Biên phụ: GROUP header cũng cắt đáp án (mỗi GROUP chứa các item riêng).
    bounds = sorted(
        set(
            starts
            + [i for i in range(i_intro + 1, i_end) if _GROUP_RE.match(lines[i])]
            + [i_end]
        )
    )
    petitions = []
    for k, st in enumerate(starts):
        end = next((b for b in bounds if b > st), i_end)
        # Đáp án bắt đầu ở dòng "Trả lời:" đầu tiên sau item (nếu có);
        # mọi dòng từ item tới đó đều thuộc noi_dung (đề phòng kiến nghị
        # ngắt dòng/trang giữa chừng như "Câu hồi 2." ở file mẫu).
        ans = next(
            (i for i in range(st + 1, end) if _ANS_MARK_RE.match(lines[i])),
            None,
        )
        if ans is not None:
            noi_dung = "\n".join(lines[st:ans])
            tra_loi = _strip_ans_mark("\n".join(lines[ans:end]))
        else:
            noi_dung = lines[st]
            tra_loi = _strip_ans_mark("\n".join(lines[st + 1:end]))
        if not noi_dung.strip() or not tra_loi.strip():
            continue
        petitions.append({
            "noi_dung": postprocess_noi_dung(_clean_text(noi_dung)),
            "tra_loi": postprocess_tra_loi(tra_loi, _clean_text),
        })
    return petitions


def extract_petitions(md_text: str) -> list:
    """
    Router: xác định format rồi route đến handler.
    Trả về danh sách {"noi_dung", "tra_loi"}.
    """
    fmt = classify_format(md_text)
    print(f"[format] {fmt}")
    if fmt == "f2":
        return _extract_f2(md_text)
    return []


def _looks_like_name(s: str) -> bool:
    """Dòng tên người: 2-4 từ, mỗi từ viết hoa đầu, không chứa từ khóa rác."""
    words = s.split()
    if not (2 <= len(words) <= 4):
        return False
    if not all(w and w[0].isupper() for w in words):
        return False
    if re.search(
        r"(Nơi nhận|Lưu|Như trên|Đ/c|đồng chí|Kính gửi|quy định|nghị định|thông tư|quyết định|TỔNG THANH TRA|Thanh tra Chính phủ)",
        s,
        re.I,
    ):
        return False
    return True


def _extract_signer_name(lines: list) -> str | None:
    """
    Tên người ký TTCP: dòng heading sau dòng chức danh trong 15 dòng cuối
    ("KT. TỔNG THANH TRA PHÓ TỔNG THANH TRA" -> "Lê Sỹ Bảy").
    Bỏ qua bullet "- ..." của Nơi nhận (vd "- - Tổng Thanh tra Chính phủ
    (để b/c);") — chỉ nhận dòng chức danh mở đầu bằng "#" hoặc "KT.".
    Trả về tên hoặc None.
    """
    tail = lines[-15:]
    for i, l in enumerate(tail):
        s = l.strip()
        if s.startswith("-"):
            continue
        t = s.strip("#*").strip()
        if not _SIGN_TITLE_RE.search(t):
            continue
        if not (s.startswith("#") or t.upper().startswith("KT.")):
            continue
        for nxt in tail[i + 1:]:
            s2 = nxt.strip().strip("#*").strip()
            if not s2:
                continue
            if len(s2) > 60:
                continue
            if not _looks_like_name(s2):
                continue
            return s2
        return None
    return None


def extract_metadata(md_text: str) -> dict:
    """Trích xuất metadata: so_cong_van, ngay_ban_hanh, nguoi_ky (tên)."""
    result = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}

    m = _SO_CONG_VAN_RE.search(md_text)
    if m:
        val = re.sub(r"\*+", "", m.group(1))
        val = val.strip()
        result["so_cong_van"] = val or None

    m = _NGAY_BAN_HANH_RE.search(md_text)
    if m:
        day, month, year = m.groups()
        if day and month:
            result["ngay_ban_hanh"] = f"{int(day):02d}/{int(month):02d}/{year}"

    name = _extract_signer_name(md_text.splitlines())
    if name:
        result["nguoi_ky"] = name

    return result
