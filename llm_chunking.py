# -*- coding: utf-8 -*-
"""
llm_chunking: pipeline "semantic chunking" trong map_content_answer_v3
(là bản sao của extract_v3.py bên map_content_answer_v4, đổi tên + tự chứa
các helper parse/clean lấy từ extract.py — v3 không có module extract.py nên
các hàm _strip_code_fence/_clean_line/_clean_block/_extract_outer_json được
inline để file đứng độc lập, không import chéo module).

Pipeline:
  1. Cắt .md thành atomic unit (segment_units_simple — sao từ chunking_utils):
     mỗi dòng \n là 1 unit; dòng heading (#...) là unit riêng; dòng còn lại được
     tách tiếp theo cue "Đề nghị/Kiến nghị/Đồng thời đề nghị/Ngoài ra đề nghị/
     Bên cạnh đó đề nghị" xuất hiện ngay sau [.;].
  2. Gọi LLM HAI LẦN RIÊNG (cùng danh sách unit):
     - Call 1: gộp unit thành từng nhóm KIẾN NGHỊ (prompt = SEMANTIC_CHUNKING_
       SYSTEM_PROMPT mở rộng: khối in nghiêng/chữ thường = trích dẫn PAKN = KN;
       mốc con (1)(2)(3)/"-" trong trích PAKN = cùng 1 nhóm).
       Output: JSON array-of-arrays ID.
     - Call 2: gộp unit thành từng nhóm CÂU TRẢ LỜI (prompt tương tự, nhóm trả
       lời; bao gồm heading con "(1) Về...", "## (2) Về..."; trừ PAKN đã dùng).
       Output: JSON array-of-arrays ID.
  3. validate_and_resolve (sao từ chunking_utils): kiểm tra ID tồn tại, không
     trùng, sắp theo thứ tự gốc, cảnh báo nhóm không liên tục, discarded_ids;
     kiểm tra chéo: 1 unit không được nằm cả KN lẫn TL (shared seen).
  4. Cân bằng số KN == số TL (retry ≤ max_tries với hint; hết lượt gộp nhóm
     thừa -> warning CAN_BANG).
  5. resolve_chunk_text: join text các unit trong nhóm bằng "\n\n" rồi
     _clean_block (inline từ extract.py).

Chạy:  python llm_chunking.py "path/to/file.md" [output_dir]
"""

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Helpers parse/clean LLM (inline từ map_content_answer_v4/extract.py —
# để llm_chunking đứng độc lập, v3 không có module extract.py)
# ---------------------------------------------------------------------------
def _strip_code_fence(raw: str) -> str:
    """Bỏ code fence ```json ... ``` nếu model vẫn kèm dù prompt cấm."""
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.S | re.I)
    if m:
        return m.group(1).strip()
    return s


def _extract_outer_json(raw: str) -> str:
    """
    Bóc mảng/đối tượng JSON ngoài cùng đầu tiên từ raw output của LLM.
    Chịu được trailing garbage/nhận xét sau dấu đóng ngoài cùng.
    """
    s = _strip_code_fence(raw)
    open_ch = s.find("[")
    alt = s.find("{")
    if open_ch == -1 or (0 <= alt < open_ch):
        open_ch = alt
    if open_ch == -1:
        raise ValueError("Output LLM không chứa JSON.")
    close_ch = "]" if s[open_ch] == "[" else "}"
    depth = 0
    in_str = False
    esc = False
    for i in range(open_ch, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == s[open_ch]:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return s[open_ch:i + 1]
    raise ValueError("Output LLM không phải JSON có dấu ngoặc cân bằng.")


def _clean_line(line: str) -> str:
    """Bỏ markdown/đánh số đầu dòng của 1 dòng text."""
    line = re.sub(r"^\s*[-*•◦▪▸]\s+", "", line)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^\d{1,3}\s*[.．)\]]\s+", "", line)
    return line.strip()


def _clean_block(raw: str) -> str:
    """
    Làm sạch text thô của 1 khối: bỏ markdown, quote thừa, đánh số đầu dòng;
    giữ phân đoạn \n\n giữa các đoạn, chuẩn hóa khoảng trắng.
    """
    lines = [_clean_line(l) for l in raw.split("\n")]
    out = []
    prev_blank = True
    for l in lines:
        if not l:
            prev_blank = True
            continue
        if prev_blank and out:
            out.append("")
        out.append(l)
        prev_blank = False
    text = "\n".join(out)
    text = re.sub(r"[”“\"'‘’]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("*", "").replace("_", "")
    # cắt đoạn trống thừa ở đầu
    text = re.sub(r"^\s*\n+", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Config & Logger
# ---------------------------------------------------------------------------
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, LLM_TIMEOUT
from logger import get_logger

logger = get_logger("llm", "pipeline.log")

client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
MODEL_NAME = LLM_MODEL_NAME
_LLM_TIMEOUT = LLM_TIMEOUT
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Atomic unit segmentation (sao từ modules/chunking_utils.py)
# ---------------------------------------------------------------------------
HEADER_RE = re.compile(r"^#{1,6}\s")
NEW_PETITION_CUE_RE = re.compile(
    r"(?<=[.;])\s*(?=(?:Đề nghị|Kiến nghị|Đồng thời[, ]+đề nghị|"
    r"Ngoài ra,?\s*đề nghị|Bên cạnh đó,?\s*đề nghị)\b)"
)
# LƯU Ý: dùng nhóm KHÔNG ghi (?:...) trong lookahead — nếu dùng capturing group thì
# re.split() chèn cả cue "Kiến nghị" thành 1 unit riêng biệt (bug trong chunking_utils gốc).


@dataclass
class Unit:
    id: str
    text: str
    index: int


def _split_paragraph_into_units(paragraph: str) -> list[str]:
    """Tách 1 dòng thành các phần theo cue bắt đầu kiến nghị mới sau [.;]."""
    parts = NEW_PETITION_CUE_RE.split(paragraph)
    return [p for p in parts if p.strip()]


def segment_units_simple(source_text: str) -> list[Unit]:
    """Tách theo từng dòng \n (bản thực tế được dùng trong main pipeline)."""
    units: list[Unit] = []
    counter = 0
    idx = 0
    for para in source_text.strip().split("\n"):
        para = para.strip()
        if not para:
            idx += 1
            continue
        if HEADER_RE.match(para):
            counter += 1
            units.append(Unit(id=f"U{counter}", text=para, index=idx))
            idx += 1
            continue
        for sp in _split_paragraph_into_units(para):
            counter += 1
            units.append(Unit(id=f"U{counter}", text=sp, index=idx))
            idx += 1
    return units


# ---------------------------------------------------------------------------
# System prompts (gộp unit thành từng kiến nghị / từng câu trả lời)
# ---------------------------------------------------------------------------
KIEN_NGHI_SYSTEM_PROMPT = """\
Bạn là công cụ phân đoạn ngữ nghĩa (semantic chunking) cho văn bản hành chính
tiếng Việt loại "công văn trả lời phản ánh, kiến nghị cử tri" (đã qua OCR, có
thể lẫn ký tự markdown, xuống dòng/dấu câu bị lỗi là bình thường). Bạn KHÔNG
được viết lại, tóm tắt hay sinh ra bất kỳ đoạn text mới nào. Input là danh
sách các khối văn bản đã được đánh số ID (dạng "U<số>"), liệt kê đúng theo
thứ tự xuất hiện trong văn bản gốc.

## NHIỆM VỤ

Với TOÀN BỘ danh sách unit được cung cấp, hãy xác định các NHÓM UNIT liên tiếp
mà khi ghép lại tạo thành ĐÚNG MỘT kiến nghị/phản ánh/đề nghị của cử tri.

Quy tắc bắt buộc:

1. MỘT kiến nghị KHÔNG được xuất hiện trong 2 nhóm khác nhau (không được tách
   một kiến nghị hoàn chỉnh ra làm nhiều nhóm).
2. MỘT nhóm KHÔNG được chứa 2 kiến nghị độc lập khác nhau (không được gộp
   nhầm hai yêu cầu khác nhau vào một nhóm chỉ vì chúng đứng cạnh nhau).
3. MẶC ĐỊNH KHÔNG gộp hai unit liền kề, TRỪ KHI có bằng chứng rõ ràng rằng
   chúng là các mảnh của CÙNG MỘT kiến nghị bị tách rời (OCR ngắt dòng giữa
   câu, hoặc unit là phần tiếp theo về ngữ pháp/ngữ nghĩa của unit trước).
4. DẤU HIỆU MỞ ĐẦU KIẾN NGHỊ: câu chứa "Cử tri kiến nghị/phản ánh/đề
   nghị...", "1. Cử tri...", "N. Cử tri...", "N. Đề nghị...", "Kiến nghị...",
   "Đề nghị...", "Hiện nay...", "Theo quy định..." (trong phần PAKN), hoặc nội
   dung TRÍCH DẪN PAKN.
5. KHỐI IN NGHIÊNG (*...*) hoặc CHỮ THƯỜNG đặt giữa câu dẫn "...cụ thể như
   sau:" (hoặc "...chuyển kiến nghị ... gửi tới...") và phần trả lời của cơ
   quan LÀ TRÍCH DẪN LẠI PAKN -> thuộc nhóm KIẾN NGHỊ, KHÔNG phải câu trả lời.
6. MỐC CON dạng "(1) (2) (3)" hoặc bullet "-" XUẤT HIỆN BÊN TRONG một khối
   trích dẫn PAKN (đoạn chép lại nội dung kiến nghị, thường in nghiêng/chữ
   thường giữa câu dẫn và phần trả lời) là CÁC PHẦN CỦA CÙNG MỘT kiến nghị
   -> KHÔNG tách, gộp chung 1 nhóm.
7. TIÊU ĐỀ PHẦN TRẢ LỜI KHÔNG phải kiến nghị: các unit bắt đầu bằng
   "(1) Về ...", "(2) Về ...", "(3) Về ...", "## (1) ...", "## (2) ...",
   "### (3) Về ...", "(2) Đối với ...", "2. Về ..." nằm TRONG PHẦN TRẢ LỜI
   (sau câu dẫn chính thức như "...xin trả lời ... cụ thể như sau:",
   "...trả lời như sau:", "...có ý kiến như sau:") là MỞ ĐẦU CỦA CÂU TRẢ LỜI
   -> BỎ QUA, không đưa vào nhóm KN. Chỉ khi mốc "(1)(2)(3)" nằm BÊN TRONG
   khối trích dẫn PAKN (quy tắc 6) mới thuộc kiến nghị.
8. RANH GIỚI CÁC KIẾN NGHỊ: chỉ các mốc "1.", "2.", "3." đứng TRƯỚC cụm
   "Cử tri kiến nghị/phản ánh/đề nghị..." (hoặc thân/trích dẫn PAKN) mới là
   ranh giới giữa các kiến nghị khác nhau; các mốc khác đều không phải.
9. Bỏ qua (không đưa vào bất kỳ nhóm nào) các unit KHÔNG phải kiến nghị:
   quốc hiệu, "**Số:**", ngày tháng, "Kính gửi...", "V/v trả lời kiến nghị
   của cử tri", câu dẫn "...có ý kiến như sau:", câu kết thư ("Xin trân trọng
   cảm ơn./.", "Bộ Y tế trân trọng kính gửi ... để biết, thông tin tới cử
   tri."), "Nơi nhận:", bullet "Như trên;", chữ ký, "## CHÁNH ÁN", và TOÀN BỘ
   nội dung TRẢ LỜI của cơ quan.
10. Bỏ qua (không đưa vào nhóm nào) các unit thuộc phần "I. Kiến nghị chung",
    "Tình hình chung", tổng hợp chung.
11. Mỗi kiến nghị đánh số "N." có ĐÚNG MỘT câu trả lời tương ứng ngay sau đó
    (có thể cách bởi heading như "## 1. Nội dung kiến nghị của cử tri"), nhưng
    CÂU TRẢ LỜI KHÔNG được đưa vào nhóm kiến nghị.

## ĐỊNH DẠNG OUTPUT (bắt buộc, không kèm gì khác)

Một JSON array of arrays, mỗi array con là 1+ ID unit (dạng "U12") thuộc cùng
một kiến nghị, liệt kê theo đúng thứ tự xuất hiện. Ví dụ:

[
  ["U4"],
  ["U9", "U10"],
  ["U15"]
]

Không bịa ID không có trong danh sách cung cấp. Không xuất unit KHÔNG PHẢI
kiến nghị vào bất kỳ nhóm nào. Số nhóm = số kiến nghị của văn bản.
"""

TRA_LOI_SYSTEM_PROMPT = """\
Bạn là công cụ phân đoạn ngữ nghĩa (semantic chunking) cho văn bản hành chính
tiếng Việt loại "công văn trả lời phản ánh, kiến nghị cử tri" (đã qua OCR, có
thể lẫn ký tự markdown, xuống dòng/dấu câu bị lỗi là bình thường). Bạn KHÔNG
được viết lại, tóm tắt hay sinh ra bất kỳ đoạn text mới nào. Input là danh
sách các khối văn bản đã được đánh số ID (dạng "U<số>"), liệt kê đúng theo
thứ tự xuất hiện trong văn bản gốc.

## NHIỆM VỤ

Với TOÀN BỘ danh sách unit được cung cấp, hãy xác định các NHÓM UNIT liên tiếp
mà khi ghép lại tạo thành ĐÚNG MỘT câu trả lời/giải trình của CƠ QUAN dành
cho một kiến nghị cụ thể của cử tri.

Quy tắc bắt buộc:

1. MỘT câu trả lời KHÔNG được xuất hiện trong 2 nhóm khác nhau (không được
   tách một câu trả lời hoàn chỉnh ra làm nhiều nhóm).
2. MỘT nhóm KHÔNG được chứa 2 câu trả lời độc lập khác nhau.
3. MẶC ĐỊNH KHÔNG gộp hai unit liền kề, TRỪ KHI có bằng chứng rõ ràng rằng
   chúng là các mảnh của CÙNG MỘT câu trả lời bị tách rời (OCR ngắt dòng
   giữa câu, hoặc unit là phần tiếp theo về ngữ pháp/ngữ nghĩa của unit
   trước — một trả lời thường trải nhiều đoạn).
4. CÂU TRẢ LỜI thường nằm SAU khối kiến nghị, sau heading "2. Kết quả nghiên
   cứu, giải quyết và trả lời kiến nghị", hoặc sau câu dẫn "...có ý kiến như
   sau:". Cụm "Nội dung kiến nghị được giải trình, cung cấp thông tin như
   sau" là MỞ ĐẦU CỦA TRẢ LỜI -> NẰM TRONG nhóm trả lời, không phải phần khác.
5. MỐC CON (1)(2)(3), "## (2) Về...", "### (3) Về..." XUẤT HIỆN BÊN TRONG
   câu trả lời là CÁC PHẦN CỦA CÙNG MỘT câu trả lời -> KHÔNG tách, gộp chung
   1 nhóm.
6. Bỏ qua (không đưa vào bất kỳ nhóm nào) các unit KHÔNG phải câu trả lời:
   quốc hiệu, "**Số:**", ngày tháng, "Kính gửi...", "V/v trả lời kiến nghị
   của cử tri", câu dẫn "...có ý kiến như sau:", TOÀN BỘ nội dung KIẾN
   NGHỊ/TRÍCH DẪN PAKN (khối in nghiêng *...* hoặc chữ thường đặt giữa
   "...cụ thể như sau:" và "...đã có ý kiến như sau:"), heading "## 1. Nội
   dung kiến nghị của cử tri", câu kết thư ("Xin trân trọng cảm ơn./.", "Bộ Y
   tế trân trọng kính gửi ... để biết, thông tin tới cử tri."), "Nơi nhận:",
   bullet "Như trên;", chữ ký.
7. Bỏ qua (không đưa vào nhóm nào) các unit thuộc phần "I. Kiến nghị chung",
   "Tình hình chung", tổng hợp chung.
8. Mỗi câu trả lời "N." tương ứng ĐÚNG MỘT kiến nghị "N."; kết thúc trước
   kiến nghị kế tiếp hoặc câu kết thư. KHÔNG gộp 2 câu trả lời của 2 kiến
   nghị khác nhau.

## ĐỊNH DẠNG OUTPUT (bắt buộc, không kèm gì khác)

Một JSON array of arrays, mỗi array con là 1+ ID unit (dạng "U12") thuộc cùng
một câu trả lời, liệt kê theo đúng thứ tự xuất hiện. Ví dụ:

[
  ["U5"],
  ["U6", "U7", "U8"],
  ["U11"]
]

Không bịa ID không có trong danh sách cung cấp. Không xuất unit KHÔNG PHẢI
câu trả lời vào bất kỳ nhóm nào. Số nhóm = số câu trả lời của văn bản.
"""


# ---------------------------------------------------------------------------
# LLM (giống semantic_chunker: temp 0, xử lý phần thinking " response")
# ---------------------------------------------------------------------------
def build_prompt(units: list[Unit]) -> str:
    return "\n\n".join(f"[{u.id}] {u.text}" for u in units)


def build_messages(units: list[Unit], system_prompt: str, hint: str | None = None) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": build_prompt(units)}]
    if hint:
        messages.append({"role": "user", "content": hint})
    return messages


async def _call_llm(units: list[Unit], system_prompt: str,
                    hint: str | None = None, max_retries: int = 3) -> str:
    count = 0
    while count < max_retries:
        try:
            response = await client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0.0,
                max_tokens=32768,
                messages=build_messages(units, system_prompt, hint),
                timeout=_LLM_TIMEOUT,
            )
            fully = response.choices[0].message.content
            if " response" in fully:
                _, final_answer = fully.rsplit(" response", 1)
            else:
                final_answer = fully
            final_answer = final_answer.strip()
            if final_answer:
                return final_answer
        except Exception as e:
            print(f"[LỖI API] {e}", file=sys.stderr)
        count += 1
        print(f"[RETRY {count}/{max_retries}]", file=sys.stderr)
    raise RuntimeError("LLM không trả về output sau max_retries lần.")


# ---------------------------------------------------------------------------
# Validate referential integrity (sao từ chunking_utils + seen chéo KN/TL)
# ---------------------------------------------------------------------------
def strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*)\n```$", text, flags=re.DOTALL)
    return m.group(1).strip() if m else text


def validate_and_resolve(llm_output_raw: str,
                         units_by_id: dict[str, Unit],
                         all_ids_ordered: list[str],
                         seen: set[str] | None = None) -> tuple[list[list[str]], dict]:
    """
    Parse output LLM (JSON array of arrays ID) + xác thực + sắp xếp theo thứ tự gốc.
    `seen`: set các ID đã thuộc nhóm khác (KN) — ID không được tham chiếu 2 lần
    chung cho cả KN + TL. Trả (validated_groups, report).
    """
    report = {"errors": [], "warnings": []}
    cleaned = strip_code_fence(llm_output_raw)
    try:
        raw_groups = json.loads(cleaned)
    except json.JSONDecodeError:
        # chịu thêm text dư quanh JSON
        try:
            cleaned = _extract_outer_json(llm_output_raw)
            raw_groups = json.loads(cleaned)
        except (ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"LLM output không phải JSON hợp lệ: {e}\nRaw: {llm_output_raw}") from e

    if not isinstance(raw_groups, list):
        raise ValueError("Output phải là JSON array of arrays.")

    seen = set() if seen is None else set(seen)
    validated: list[list[str]] = []
    id_position = {uid: i for i, uid in enumerate(all_ids_ordered)}

    for group in raw_groups:
        if not isinstance(group, list) or not group:
            report["errors"].append(f"Nhóm không hợp lệ: {group}")
            continue
        valid_group = []
        for ref_id in group:
            if ref_id not in units_by_id:
                report["errors"].append(f"ID không tồn tại, bị loại bỏ: {ref_id}")
                continue
            if ref_id in seen:
                report["errors"].append(f"ID {ref_id} bị tham chiếu >1 lần, chỉ giữ lần đầu.")
                continue
            seen.add(ref_id)
            valid_group.append(ref_id)
        if not valid_group:
            continue

        sorted_group = sorted(valid_group, key=lambda i: id_position[i])
        if sorted_group != valid_group:
            report["warnings"].append(
                f"Nhóm {valid_group} không đúng thứ tự gốc, đã sắp xếp lại: {sorted_group}."
            )
        positions = [id_position[i] for i in sorted_group]
        if positions and (max(positions) - min(positions) + 1) != len(positions):
            report["warnings"].append(
                f"Nhóm {sorted_group} không liên tục trong văn bản gốc."
            )
        validated.append(sorted_group)

    report["discarded_ids"] = [i for i in all_ids_ordered if i not in seen]
    return validated, report


def resolve_chunk_text(group: list[str], units_by_id: dict[str, Unit]) -> str:
    return "\n\n".join(units_by_id[i].text for i in group)


# ---------------------------------------------------------------------------
# Cân bằng + build output
# ---------------------------------------------------------------------------
def _merge_extra_groups(groups: list[list[str]], keep: int,
                        pos: dict[str, int],
                        busy: set[str]) -> tuple[list[list[str]], bool]:
    """Gộp các nhóm thừa (từ index keep trở đi) vào nhóm cuối giữ lại, NHƯNG
    chỉ chấp nhận khi phần gộp LIÊN TỤC trong văn bản gốc (giữa các nhóm gộp
    không được chứa unit thuộc nhóm khác). Trả về (nhóm mới, gộp được không)."""
    if keep <= 0 or not groups:
        return [], True
    head = groups[:keep - 1]
    tail = groups[keep - 1:]
    tail_pos = {pos[u] for g in tail for u in g}
    lo, hi = min(tail_pos), max(tail_pos)
    for p in range(lo, hi + 1):
        if p in busy and p not in tail_pos:
            return groups, False
    merged = [u for g in tail for u in g]
    return head + [merged], True


def build_output(file_name: str, units: list[Unit], kn_groups: list[list[str]],
                 tl_groups: list[list[str]], report_kn: dict,
                 report_tl: dict) -> dict:
    """Ghép text từng nhóm (clean), sắp blocks theo thứ tự KN_i, TL_i."""
    units_by_id = {u.id: u for u in units}
    kien_nghi, tra_loi, blocks = [], [], []
    warns: list[str] = []
    for label, groups, text_list in (("KIEN_NGHI", kn_groups, kien_nghi),
                                      ("TRA_LOI", tl_groups, tra_loi)):
        for group in groups:
            text = _clean_block(resolve_chunk_text(group, units_by_id))
            text = re.sub(r"[,;]+$", "", text.rstrip())
            blocks.append({
                "label": label,
                "source_unit_ids": group,
                "start_id": group[0],
                "end_id": group[-1],
                "text": text,
            })
            text_list.append(text)

    seen_order = []
    for i in range(max(len(kn_groups), len(tl_groups))):
        if i < len(kn_groups):
            seen_order.append(("KIEN_NGHI", kn_groups[i]))
        if i < len(tl_groups):
            seen_order.append(("TRA_LOI", tl_groups[i]))
    blocks.sort(key=lambda b: (next(j for j, (lab, g) in enumerate(seen_order)
                                    if lab == b["label"] and g == b["source_unit_ids"]),
                               units_by_id[b["start_id"]].index))

    for r in (report_kn, report_tl):
        for e in r.get("errors", []):
            warns.append(f"VALIDATE: {e}")
        for w in r.get("warnings", []):
            warns.append(f"VALIDATE: {w}")

    return {
        "file": file_name,
        "n_units": len(units),
        "kien_nghi": kien_nghi,
        "tra_loi": tra_loi,
        "blocks": blocks,
        "warnings": warns,
    }


async def extract_from_md(markdown: str, file_name: str = "document", max_tries: int = 3) -> dict:
    """Chạy pipeline v3: segment + 2 LLM call + validate + cân bằng KN==TL."""
    units = segment_units_simple(markdown)

    # Kiểm tra đơn giản để tránh gọi LLM với đầu vào không dùng được
    if not units:
        return {
            "file": file_name,
            "n_units": 0,
            "kien_nghi": [],
            "tra_loi": [],
            "blocks": [],
            "warnings": ["KHONG_UNIT: văn bản không tạo được atomic unit nào."],
        }

    units_by_id = {u.id: u for u in units}
    all_ids_ordered = [u.id for u in units]

    hint = None
    last_kn_groups = last_tl_groups = None
    last_reports = ({"errors": [], "warnings": []}, {"errors": [], "warnings": []})
    for attempt in range(max_tries):
        # Chạy tuần tự 2 prompt LLM để tiết kiệm VRAM GPU (tránh tràn KV Cache)
        logger.info(f"[{file_name}] Bắt đầu gọi prompt Kiến nghị (lần {attempt + 1}/{max_tries})...")
        raw_kn = await _call_llm(units, KIEN_NGHI_SYSTEM_PROMPT, hint)
        logger.info(f"[{file_name}] Bắt đầu gọi prompt Trả lời (lần {attempt + 1}/{max_tries})...")
        raw_tl = await _call_llm(units, TRA_LOI_SYSTEM_PROMPT, hint)
        try:
            seen = set()
            kn_groups, report_kn = validate_and_resolve(raw_kn, units_by_id, all_ids_ordered, None)
            tl_groups, report_tl = validate_and_resolve(raw_tl, units_by_id, all_ids_ordered, seen)
        except ValueError as e:
            logger.warning(f"[{file_name}] LLM output parse lỗi: {e}")
            hint = (f"Lần trước JSON của bạn không hợp lệ ({e}). "
                    f"Hãy chỉ xuất MỘT JSON array of arrays duy nhất với các ID unit "
                    f"có trong danh sách.")
            continue

        last_kn_groups, last_tl_groups = kn_groups, tl_groups
        last_reports = (report_kn, report_tl)
        n_kn, n_tl = len(kn_groups), len(tl_groups)
        if n_kn == n_tl:
            logger.info(f"[{file_name}] LLM extract thành công cân bằng: {n_kn} KN và {n_tl} TL.")
            return build_output(file_name, units, kn_groups, tl_groups, report_kn, report_tl)
        logger.warning(f"[{file_name}] Lệch số lượng: {n_kn} KN != {n_tl} TL. Đang thử lại...")
        hint = (f"Số KIẾN NGHỊ ({n_kn}) KHÁC số CÂU TRẢ LỜI ({n_tl}) bạn vừa trả. "
                f"YÊU CẦU: mỗi kiến nghị có ĐÚNG MỘT câu trả lời, số trả lời PHẢI bằng "
                f"số kiến nghị. Hãy kiểm tra lại danh sách unit và xuất lại TOÀN BỘ JSON "
                f"(gộp các phần bị tách của cùng 1 kiến nghị/câu trả lời thành 1 nhóm).")

    # Hết lượt retry vẫn lệch -> gộp nhóm thừa (đảm bảo 1:1), nhưng chỉ khi
    # phần gộp liên tục trong văn bản; không gộp được thì giữ nguyên + cảnh báo.
    kn_groups = list(last_kn_groups or [])
    tl_groups = list(last_tl_groups or [])
    warnings_add: list[str] = []
    pos = {u_id: i for i, u_id in enumerate(all_ids_ordered)}
    busy = {pos[u] for g in kn_groups for u in g} | {pos[u] for g in tl_groups for u in g}
    if len(kn_groups) > len(tl_groups):
        n = len(tl_groups)
        if n == 0:
            kn_groups = []
        else:
            kn_groups, ok = _merge_extra_groups(kn_groups, n, pos, busy)
        if n and len(kn_groups) == n:
            warnings_add.append(f"CAN_BANG: gộp nhóm kiến nghị -> {len(kn_groups)} (bằng TL).")
        elif n and len(kn_groups) > n:
            warnings_add.append(
                f"MISMATCH: {len(kn_groups)} KN != {n} TL; nhóm thừa không liên tục "
                f"nên chưa gộp, giữ nguyên để tránh ghép nhầm 2 kiến nghị độc lập.")
    elif len(tl_groups) > len(kn_groups):
        n = len(kn_groups)
        if n == 0:
            tl_groups = []
        else:
            tl_groups, ok = _merge_extra_groups(tl_groups, n, pos, busy)
        if n and len(tl_groups) == n:
            warnings_add.append(f"CAN_BANG: gộp nhóm trả lời -> {len(tl_groups)} (bằng KN).")
        elif n and len(tl_groups) > n:
            warnings_add.append(
                f"MISMATCH: {len(tl_groups)} TL != {n} KN; nhóm thừa không liên tục "
                f"nên chưa gộp, giữ nguyên để tránh gộp nhầm câu trả lời độc lập.")
    result = build_output(file_name, units, kn_groups, tl_groups,
                          last_reports[0], last_reports[1])
    result["warnings"].extend(warnings_add)
    return result


def extract_from_md_sync(markdown_text: str, file_name: str = "document", max_tries: int = 3) -> dict:
    """Wrapper đồng bộ cho CLI hoặc script test."""
    return asyncio.run(extract_from_md(markdown_text, file_name, max_tries))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        raise SystemExit(
            "Cách dùng: python llm_chunking.py \"path/to/file.md\" [output_dir]\n"
            "  output_dir (tùy chọn): thư mục lưu file .json, mặc định cùng chỗ file .md"
        )

    md_path = Path(sys.argv[1])
    if not md_path.is_file():
        raise SystemExit(f"File không tồn tại: '{md_path}'")

    out_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"[1/3] Đọc markdown: {md_path.name} ...")
    t0 = time.monotonic()
    markdown = md_path.read_text(encoding="utf-8")
    print(f"      done ({time.monotonic() - t0:.2f}s, {len(markdown)} ký tự)")

    print("[2/3] Gọi LLM 2 lần (gộp KIẾN NGHỊ + gộp CÂU TRẢ LỜI theo unit) ...")
    t0 = time.monotonic()
    result = extract_from_md_sync(markdown, md_path.name, max_tries=MAX_RETRIES)
    print(f"      done ({time.monotonic() - t0:.1f}s)")

    print("[3/3] Validate + gom khối ...")
    for w in result["warnings"]:
        print(f"      [CẢNH BÁO] {w}", file=sys.stderr)
    print(f"      Kiến nghị: {len(result['kien_nghi'])} | Trả lời: {len(result['tra_loi'])} "
          f"| Khối: {len(result['blocks'])}")

    out_path = md_path.with_suffix(".json")
    if out_dir is not None:
        out_path = out_dir / (md_path.stem + ".json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã lưu: {out_path}")

    print("\n--- KẾT QUẢ (llm_chunking) ---")
    for idx, text in enumerate(result["kien_nghi"], 1):
        print(f"\n[{idx}] KIẾN NGHỊ:\n{text}")
    for idx, text in enumerate(result["tra_loi"], 1):
        print(f"\n[{idx}] TRẢ LỜI:\n{text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())