# -*- coding: utf-8 -*-
"""
Pydantic schemas của Answer Matching API v2.

Tách khỏi extract_api_server.py để tái sử dụng ở nơi khác (vd: worker Celery
giai đoạn 2 cần parse lại payload từ queue) và giữ file API chỉ chứa logic
endpoint.

Contract ngoài (theo docs/Mo_ta_API_v2.md):
    POST /api/v1/answer-matching  -> multipart/form-data
        data_list       : JSON string (danh sách kiến nghị KN_KIENNGHI.*)
        file_ids        : JSON array string (định danh file do HTTT quản lý);
                          nới lỏng chấp nhận thêm 1 ID trần hoặc danh sách
                          phân tách phẩy (Swagger UI có bug cắt ngoặc vuông)
        files           : PDF binary[] (mapping 1-1 theo index với file_ids)
        force_reprocess : bool, mặc định false

Contract nội bộ API <-> Celery worker:
    InternalJobPayload — message trong queue chứa đường dẫn file trên
    volume dùng chung thay vì binary/URL.
"""

import json
import re

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RequestParseError(ValueError):
    """Lỗi parse/validate form-data đầu vào -> HTTP 400."""


# ---------------------------------------------------------------------------
# Làm sạch input form-data
# ---------------------------------------------------------------------------

# Smart quotes sinh ra khi copy text qua browser/chat/email — phá JSON
_SMART_QUOTE_MAP = {
    "\u201c": '"',  # " left double
    "\u201d": '"',  # " right double
    "\u2018": "'",  # ' left single
    "\u2019": "'",  # ' right single
}

# Code fence dính khi copy từ khung markdown: ```json ... ``` hoặc ``` ... ```
_FENCE_RE = r"^\s*(`{3,}\s*(?:json)?\s*)(.*?)(`{3,}\s*)$"


def _replace_smart_quotes_outside_strings(s: str) -> str:
    """
    Thay smart quotes thành dấu thẳng CHỈ khi chúng đứng NGOÀI chuỗi JSON
    (đóng vai trò delimiter). Smart quotes bên trong giá trị string là ký tự
    hợp lệ của JSON và phải giữ nguyên.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for ch in s:
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
                out.append(ch)
            else:
                out.append(_SMART_QUOTE_MAP.get(ch, ch))
    return "".join(out)


def _clean_json_text(raw: str) -> str:
    """
    Làm sạch chuỗi nhận được từ form-data trước khi parse JSON:

        - bỏ BOM (\ufeff) ở bất kỳ đâu trong chuỗi;
        - thay smart quotes (" " ' ') bằng dấu thẳng (chỉ ngoài string);
        - bóc code fence (```json ... ```) nếu client dán nhầm.
    """
    s = (raw or "").replace("\ufeff", "")

    s = _replace_smart_quotes_outside_strings(s)

    s = s.strip()

    m = re.match(_FENCE_RE, s, re.DOTALL | re.IGNORECASE)
    if m:
        s = m.group(2).strip()

    return s


def _preview(raw: str, limit: int = 80) -> str:
    """Rút gọn chuỗi nhận được để nhúng vào thông báo lỗi (dễ debug)."""
    s = repr((raw or "")[:limit])
    return f"{s}..." if len(raw or "") > limit else s


# ---------------------------------------------------------------------------
# Kiến nghị cử tri (giữ nguyên giữa các version)
# ---------------------------------------------------------------------------

class InputPetition(BaseModel):
    """Một kiến nghị cử tri từ HTTT."""

    model_config = ConfigDict(populate_by_name=True)

    KN_KIENNGHI_ID: int = Field(alias="KN_KIENNGHI.ID")
    KN_KIENNGHI_DONVI_TIEPNHAN: int = Field(alias="KN_KIENNGHI.DONVI_TIEPNHAN")
    KN_KIENNGHI_NOI_DUNG: str = Field(alias="KN_KIENNGHI.NOI_DUNG")


def parse_data_list(raw: str) -> list[dict]:
    """
    Parse form field `data_list` (JSON string) thành list dict theo alias.

    Chấp nhận cả 2 dạng mà client có thể gửi:
        '["{...}", "{...}"]'  (mảng object hoặc mảng JSON-string)
        '{"data_list": [...]}' (object bọc ngoài)
        '[{...}, {...}]'
    Raise RequestParseError nếu JSON sai / schema sai.
    """
    cleaned = _clean_json_text(raw)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RequestParseError(
            f"data_list không phải JSON hợp lệ: {e} (nhận được: {_preview(raw)})"
        ) from e

    if isinstance(parsed, dict) and "data_list" in parsed:
        parsed = parsed["data_list"]

    # Client đôi khi gửi mảng các JSON-string lồng nhau -> unwrap từng phần tử
    unwrapped = []
    for item in parsed if isinstance(parsed, list) else []:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except json.JSONDecodeError as e:
                raise RequestParseError(
                    f"Phần tử data_list là JSON-string không hợp lệ: {e}"
                ) from e
        unwrapped.append(item)

    petitions: list[InputPetition] = []
    try:
        for item in unwrapped:
            petitions.append(InputPetition.model_validate(item))
    except ValidationError as e:
        raise RequestParseError(
            f"data_list sai schema: {e.errors(include_url=False)}"
        ) from e

    if not petitions:
        raise RequestParseError("data_list không được rỗng")

    return [p.model_dump(by_alias=True) for p in petitions]


def parse_file_ids(raw: str) -> list[str]:
    """
    Parse form field `file_ids` thành list ID.

    Định dạng chuẩn theo spec: JSON array string — '["FILE_001","FILE_002"]'.

    Nới lỏng thêm để chịu các lỗi của client (đặc biệt Swagger UI có bug
    cắt mất ngoặc vuông với giá trị nhìn giống mảng trong multipart —
    swagger-api/swagger-ui#8614, #10539):
        'FILE_001'                -> ['FILE_001']            (1 ID trần)
        'FILE_001, FILE_002'      -> ['FILE_001', 'FILE_002']
        '["FILE_001"], ["FILE_002"]' -> ['FILE_001', 'FILE_002']
                                     (Swagger UI nén mảng thành chuỗi phẩy)
    Ngoài ra tự làm sạch BOM / smart quotes / code fence trước khi parse.

    Raise RequestParseError nếu rỗng / sai kiểu.
    """
    cleaned = _clean_json_text(raw)

    if not cleaned:
        raise RequestParseError("file_ids không được rỗng")

    # Bắt đầu bằng [ hoặc { -> phải là JSON hợp lệ
    if cleaned[0] in "[{":
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise RequestParseError(
                f"file_ids không phải JSON hợp lệ: {e} (nhận được: {_preview(raw)})"
            ) from e

        if (
            not isinstance(parsed, list)
            or not parsed
            or not all(isinstance(x, str) and x.strip() for x in parsed)
        ):
            raise RequestParseError(
                "file_ids phải là JSON array chứa các chuỗi không rỗng "
                f"(nhận được: {_preview(raw)})"
            )
        return parsed

    # Fallback: 1 hoặc nhiều ID phân tách phẩy. Swagger UI có bug nén mảng
    # thành chuỗi phẩy và bóc mất ngoặc (swagger-api/swagger-ui#8614, #10539):
    #     'FILE_001'                  -> ['FILE_001']
    #     'FILE_001, FILE_002'        -> ['FILE_001', 'FILE_002']
    #     '["FILE_001"], ["FILE_002"]'-> ['FILE_001', 'FILE_002']
    parts = [
        re.sub(r"[\[\]{}\"']", "", part).strip()
        for part in cleaned.split(",")
    ]
    ids = [p for p in parts if p]

    if not ids:
        raise RequestParseError(
            f"file_ids không hợp lệ (nhận được: {_preview(raw)})"
        )

    return ids


# ---------------------------------------------------------------------------
# Message nội bộ API <-> Celery worker (queue)
# ---------------------------------------------------------------------------

class InternalJobPayload(BaseModel):
    """Message đẩy qua RabbitMQ cho worker.

    file_paths trỏ tới PDF đã lưu trên volume dùng chung giữa API container
    và worker container; file_ids giữ nguyên định danh của HTTT để trả kết quả.
    """

    data_list: list[dict]
    file_ids: list[str]
    file_paths: list[str]


# ---------------------------------------------------------------------------
# Polling status (GET /api/v1/answer-matching/{request_id})
# ---------------------------------------------------------------------------

class JobStatus(BaseModel):
    """Response polling trạng thái job.

    status: PROCESSING | FINISHED | FAILED
    result chỉ xuất hiện khi FINISHED; error chỉ xuất hiện khi FAILED.
    """

    request_id: str
    status: str
    result: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Response (kết quả matching)
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    """Metadata trích xuất từ văn bản trả lời (có thể null nếu chưa lấy được)."""

    so_cong_van: str | None = None
    ngay_ban_hanh: str | None = None
    nguoi_ky: str | None = None


class AnswerItem(BaseModel):
    """Một câu trả lời (tối đa 1 câu trả lời / kiến nghị)."""

    content: str
    file_id: str = Field(
        description="ID của file nguồn do HTTT cung cấp ban đầu.",
    )
    metadata: Metadata


class OutputPetition(BaseModel):
    """Một kiến nghị kèm câu trả lời đã map."""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    KN_KIENNGHI_ID: int = Field(alias="KN_KIENNGHI.ID")
    answers: list[AnswerItem]


class MetadataAll(BaseModel):
    """Thống kê tổng hợp kết quả khớp kiến nghị."""

    matched_count: int
    unmatched_count: int


class OutputResponse(BaseModel):
    """Body kết quả trả về client."""

    data_list: list[OutputPetition]
    metadata_all: MetadataAll
