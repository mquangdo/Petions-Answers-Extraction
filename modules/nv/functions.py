import re
import sys
from pathlib import Path

from postprocess import postprocess_noi_dung, postprocess_tra_loi
from regexes import (
    _ANS_HEAD_RE,
    _CLOSING_RE,
    _END_RE,
    _GROUP_RE,
    _ITEM_START_RE,
    _S1_RE,
    _S1_RESOLVER_RE,
    _S2_ALT_RE,
    _S2_RE,
    _S2_SHORT_RE,
    _SO_CONG_VAN_RE,
    _NGAY_BAN_HANH_RE,
    _NGUOI_KY_RE,
    _TRACH_NHIEM_RE,
)


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
)


def _extract_bo(line: str) -> str:
    low = line.lower()
    for name in _BO_NAMES:
        if name.lower() in low:
            return name
    return ""


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


def _is_s1(line: str) -> bool:
    return bool(_S1_RE.match(line))


def _s1_inline_tail(line: str) -> str:
    """Lấy nội dung kiến nghị nằm NGAY TRÊN dòng heading S1.

    Bộ Nội vụ có nhiều file ghi nội dung cùng dòng với tiêu đề, ví dụ:
      "**1. Nội dung kiến nghị của cử tri (*Kiến nghị số 68*):** "Cử tri ...""
      "**1. Nội dung kiến nghị của cử tri:** Cử tri tỉnh Đồng Nai phản ánh ..."
    Phần sau tiêu đề (bỏ ngoặc "(Kiến nghị số N)", dấu ":", markdown) chính là
    nội dung; nếu để nguyên thì slice lines[i+1:j] rỗng -> mất nội dung.
    """
    m = _S1_RE.match(line)
    if not m:
        return ""
    rest = line[m.end():]
    rest = re.sub(r"^\s*(?:\([^)]*\))?\s*[:：]?\s*", "", rest)
    return rest.strip(" *_#>").strip()


def _find_s2(lines: list) -> list:
    """Vị trí các mục trả lời (S2): ưu tiên heading chuẩn "Kết quả nghiên cứu, ...",
    rồi heading ngắn "Kết quả trả lời kiến nghị", cuối cùng heading
    "Nội dung kiến nghị đã được giải quyết" (chỉ dùng khi S2 chuẩn không có)."""
    s2 = [i for i, line in enumerate(lines) if _S2_RE.search(line)]
    if s2:
        return s2
    s2 = [i for i, line in enumerate(lines) if _S2_SHORT_RE.match(line)]
    if s2:
        return s2
    return [i for i, line in enumerate(lines) if _S2_ALT_RE.match(line)]


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


def classify_format(md_text: str):
    """
    Xác định format của file .md đầu vào:
      - "f1": 1 mục "Nội dung kiến nghị" (S1) + 1 mục "Kết quả nghiên cứu, giải
        quyết và trả lời" (S2) -> 1 kiến nghị / thư.
      - "f2": mỗi kiến nghị 1 mục riêng (GROUP header + S1 + S2) -> N kiến nghị.
      - "f3": liệt kê hết các kiến nghị trong 1 mục S1 (dạng "Kiến nghị số X:"),
        trả lời theo heading "2.x. ...".
      - "llm": không nhận diện được 3 format chuẩn (không có S1/S2 ->
        cần xử lý bằng LLM).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = _find_s2(lines)
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
        head_text = _ANS_HEAD_RE.search(slice_lines[h]).group(1)
        nums = (
            {int(x) for x in re.findall(r"\d+", head_text)}
            if re.search(r"kiến nghị số", head_text, re.I)
            else set()
        )
        body = slice_lines[h + 1:end]
        # Có file ghi đáp án NGAY TRÊN dòng heading, sau dấu ":", ví dụ:
        #   "2.2. Đối với chế độ, chính sách ... ở cấp phường: Tại khoản 2 Điều 3 ..."
        # -> body (các dòng sau) rỗng, phải lấy phần đuôi sau ":" của chính heading.
        if not any(l.strip() for l in body) and ":" in head_text:
            inline = head_text.split(":", 1)[1].strip()
            if inline:
                body = [inline]
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
    s2_pos = _find_s2(lines)
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
        if not noi_dung.strip():
            noi_dung = _s1_inline_tail(lines[i])
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
    "2.x. ...". Tách từng kiến nghị và map đáp án:
      - nếu heading đáp án ghi số ("... kiến nghị số X") -> map theo số;
      - nếu không ghi số (Bộ Nội vụ thường chỉ đánh "2.1./2.2./...") -> map theo
        THỨ TỰ item k <-> block k khi số block = số item;
      - còn lại -> tra_loi = toàn bộ phần trả lời (an toàn).
    """
    lines = md_text.splitlines()
    s1_pos = [i for i, line in enumerate(lines) if _is_s1(line)]
    s2_pos = _find_s2(lines)
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
            for idx, (num, item_text) in enumerate(items):
                try:
                    n = int(num)
                except ValueError:
                    n = None
                target = full_answer
                matched = False
                for nums, block_text in blocks:
                    if n is not None and n in nums:
                        target = block_text
                        matched = True
                        break
                # Bộ Nội vụ thường không ghi số kiến nghị trong heading đáp án
                # (chỉ "2.1./2.2./...") -> map theo THỨ TỰ khi số block = số item.
                if not matched and len(blocks) == len(items):
                    target = blocks[idx][1]
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
    print(f"[format] {fmt}")
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

    # 3. Người ký: chỉ tìm trong 40 dòng CUỐI (vùng chữ ký), tránh bắt nhầm
    #    "của Bộ trưởng ..." trong thân thư.
    tail = "\n".join(md_text.splitlines()[-40:])
    matches = list(_NGUOI_KY_RE.finditer(tail))
    if matches:
        # Ưu tiên match có tên "sạch" (không phải câu thân thư); nếu tất cả
        # đều bẩn thì vẫn trả về chức danh "Bộ trưởng".
        chosen = None
        for m in matches:
            name = (m.group(1) or "").strip()
            if (
                name
                and len(name) <= 60
                and not re.search(
                    r"\b(đã|quy định|ban hành|sửa đổi|bãi bỏ|thông tư|nghị định|quyết định)\b",
                    name,
                    re.I,
                )
            ):
                chosen = name
                break
        result["nguoi_ky"] = f"Bộ trưởng {chosen}" if chosen else "Bộ trưởng"

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