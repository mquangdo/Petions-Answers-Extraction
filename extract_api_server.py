# -*- coding: utf-8 -*-
"""
FastAPI server cho Answer Matching API v2 (theo docs/Mo_ta_API_v2.md).

Contract bất đồng bộ qua polling:

    POST /api/v1/answer-matching   (multipart/form-data)
        form fields:
            data_list       : JSON string danh sách kiến nghị (KN_KIENNGHI.*)
            file_ids        : JSON array string định danh file của HTTT
            files           : PDF binary[] (mapping 1-1 theo index)
            force_reprocess : bool mặc định false
        -> HTTP 200 { request_id, status: "PROCESSING" }
           hoặc (cache hit) { request_id, status: "FINISHED", result }

    GET /api/v1/answer-matching/{request_id}
        -> { request_id, status: PROCESSING | FINISHED | FAILED, result?, error? }

    GET /health -> {"status": "ok"}

Cơ chế nội bộ:
    - File PDF binary được lưu vào FILE_STORE_DIR (volume dùng chung giữa
      API container và Celery worker), message queue chỉ chứa đường dẫn.
    - Cache key = SHA-256(file_ids + SHA-256 từng file bytes + data_list).
      Kết quả cache lưu trong bảng result_cache trên PostgreSQL.
"""

import copy
import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from kombu.exceptions import OperationalError

from config import FILE_STORE_DIR, MAX_FILE_SIZE, MAX_FILE_SIZE_MB
from db import (
    create_job as db_create_job,
    create_tables,
    get_cached_result,
    get_job,
    set_failed,
)
from logger import get_logger
from postprocess import normalize_markdown, strip_markdown
from schemas import JobStatus, OutputResponse, parse_data_list, parse_file_ids
from tasks import process_answer_matching

logger = get_logger("api", "api.log")


# ============================================================
# FastAPI lifespan
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Khởi tạo database + thư mục lưu file khi API startup.

    create_tables() dùng checkfirst=True nên có thể gọi nhiều lần
    mà không gây lỗi nếu bảng đã tồn tại.

    Nếu PostgreSQL chưa sẵn sàng thì KHÔNG làm chết app — chỉ log cảnh báo
    (Swagger/docs vẫn mở được để debug; POST sẽ trả 503 cho tới khi DB lên).
    Worker cũng tự create_tables() khi nhận job đầu tiên.
    """
    os.makedirs(FILE_STORE_DIR, exist_ok=True)
    try:
        create_tables()
        logger.info("[startup] Đã kết nối DB và sẵn sàng bảng dữ liệu.")
    except Exception as e:
        logger.warning(f"[startup] CẢNH BÁO: chưa tạo được bảng trong PostgreSQL ({e})")
        logger.warning("[startup] API vẫn khởi động. Kiểm tra DATABASE_URL/docker compose up -d db.")
    yield


app = FastAPI(
    title="Answer Matching API v2",
    description=(
        "Nhận danh sách kiến nghị cử tri và các file văn bản trả lời PDF "
        "(multipart/form-data) -> OCR, trích xuất metadata + nội dung trả "
        "lời, semantic matching. Xử lý bất đồng bộ qua polling trạng thái."
    ),
    version="2.2.0",
    lifespan=lifespan,
)


# ============================================================
# OpenAPI post-process (fix Swagger UI không hiện nút chọn file)
# ============================================================
#
# FastAPI mới sinh OpenAPI 3.1, biểu diễn UploadFile bằng
# "contentMediaType": "application/octet-stream" mà KHÔNG có
# "format": "binary". Swagger UI cũ không hiểu contentMediaType ->
# render thành ô TEXT thường; khi chọn file nó đọc nguyên binary PDF
# đổ vào textbox (hiện chuỗi ký tự rác).
#
# Fix: thêm "format": "binary" vào mọi string schema có contentMediaType
# octet-stream -> Swagger UI luôn render đúng ô chọn file.

_default_openapi = app.openapi


def _patch_binary_formats(node) -> None:
    """Đệ quy gắn format='binary' cho các file field trong schema."""
    if isinstance(node, dict):
        if (
            node.get("type") == "string"
            and node.get("contentMediaType") == "application/octet-stream"
        ):
            node.setdefault("format", "binary")
        for value in node.values():
            _patch_binary_formats(value)
    elif isinstance(node, list):
        for item in node:
            _patch_binary_formats(item)


def custom_openapi() -> dict:
    if app.openapi_schema is None:
        app.openapi_schema = _default_openapi()
        _patch_binary_formats(app.openapi_schema)
    return app.openapi_schema


app.openapi = custom_openapi


# ============================================================
# Exception handlers
# ============================================================

@app.exception_handler(RequestValidationError)
async def _validation_error_handler(
    request: Request,
    exc: RequestValidationError,
):
    """
    Schema sai -> HTTP 400 thay vì 422 mặc định.
    """
    return JSONResponse(
        status_code=400,
        content={"detail": exc.errors()},
    )


# ============================================================
# Utility functions
# ============================================================

def _convert_result_format(result: dict, plain_text: bool) -> dict:
    """
    Chuyển đổi định dạng result trước khi trả FE.

    - plain_text=True  -> content dạng plain text (strip_markdown)
    - plain_text=False -> content dạng markdown ĐÃ normalize (giữ **bold**)

    Field metadata (nguoi_ky, so_cong_van, ngay_ban_hanh) LUÔN được dọn
    sạch markdown ở cả 2 chế độ (field ngắn, không bao giờ nên chứa "###").

    DB luôn lưu markdown gốc — hàm này chỉ convert bản copy trả về,
    không mutate dữ liệu trong DB.
    """
    out = copy.deepcopy(result)

    for item in out.get("data_list", []):
        for ans in item.get("answers", []):
            content = ans.get("content") or ""
            ans["content"] = (
                strip_markdown(content) if plain_text else normalize_markdown(content)
            )

            metadata = ans.get("metadata") or {}
            for key in ("nguoi_ky", "so_cong_van", "ngay_ban_hanh"):
                if metadata.get(key):
                    metadata[key] = normalize_markdown(metadata[key])

    return out


def _sha256_bytes(data: bytes) -> str:
    """Tính SHA-256 hex lowercase trên bytes gốc của file."""
    return hashlib.sha256(data).hexdigest()


def _compute_cache_key(
    file_ids: list[str],
    file_hashes: list[str],
    data_list: list[dict],
) -> str:
    """
    Cache key = SHA-256( canonical(file_ids + hash từng file + data_list) ).

    Bao gồm cả file_ids để kết quả trả ra luôn mang đúng file_id của request
    hiện tại (không cần re-map khi HTTT đổi định danh file).
    """
    canonical = json.dumps(
        {
            "file_ids": file_ids,
            "file_hashes": file_hashes,
            "data_list": data_list,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _read_and_validate_files(files: list[UploadFile]) -> list[bytes]:
    """
    Validate + đọc toàn bộ file upload:

        - Không phải PDF (theo extension/content-type) -> 415
        - Dung lượng > MAX_FILE_SIZE                  -> 413
    Trả về list bytes theo đúng thứ tự upload.
    """
    contents: list[bytes] = []

    for f in files:
        filename = f.filename or ""
        content_type = f.content_type or ""
        is_pdf = (
            filename.lower().endswith(".pdf")
            or content_type.lower() == "application/pdf"
        )
        if not is_pdf:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"File '{filename}' không đúng định dạng application/pdf. "
                    "Chỉ cho phép upload file .pdf."
                ),
            )

        data = await f.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{filename}' vượt quá giới hạn "
                    f"{MAX_FILE_SIZE_MB}MB."
                ),
            )
        if not data:
            raise HTTPException(
                status_code=400,
                detail=f"File '{filename}' rỗng.",
            )

        contents.append(data)

    return contents


def _save_files(request_id: str, contents: list[bytes]) -> list[str]:
    """
    Lưu các file PDF vào volume dùng chung, trả về list đường dẫn
    mapping 1-1 theo thứ tự upload.
    """
    job_dir = os.path.join(FILE_STORE_DIR, request_id)
    os.makedirs(job_dir, exist_ok=True)

    paths = []
    for i, data in enumerate(contents):
        path = os.path.join(job_dir, f"{i}.pdf")
        with open(path, "wb") as fh:
            fh.write(data)
        paths.append(path)

    return paths


def _db_status_to_api_status(db_status: str) -> str:
    """Map status nội bộ trong DB sang status của contract API."""
    if db_status == "succeeded":
        return "FINISHED"
    if db_status == "failed":
        return "FAILED"
    # queued / processing / khác -> đang xử lý
    return "PROCESSING"


# ============================================================
# Health check
# ============================================================

@app.get("/health")
def health():
    """Kiểm tra tình trạng hệ thống."""
    return {"status": "ok"}


# ============================================================
# POST /api/v1/answer-matching
# ============================================================

@app.post("/api/v1/answer-matching")
async def create_answer_matching(
    data_list: str = Form(...),
    file_ids: str = Form(...),
    files: list[UploadFile] = File(...),
    force_reprocess: bool = Form(False),
):
    """
    Khởi tạo xử lý & ghép nối trả lời.

    Flow:

        validate multipart fields (400/413/415)
            |
            v
        tính cache_key = SHA-256(file_ids + hash files + data_list)
            |
            +-- force_reprocess=false và cache hit
            |       -> trả ngay {request_id, status: FINISHED, result}
            |
            v
        lưu PDF vào volume dùng chung
            |
            v
        INSERT PostgreSQL (status=processing)
            |
            v
        Celery apply_async() -> RabbitMQ
            |
            v
        return {request_id, status: PROCESSING}
    """

    logger.info(
        f"[API] Nhận request answer-matching: {len(files)} files, "
        f"force_reprocess={force_reprocess}"
    )

    # --------------------------------------------------------
    # 1. Parse + validate form fields
    # --------------------------------------------------------

    try:
        parsed_data_list = parse_data_list(data_list)
        parsed_file_ids = parse_file_ids(file_ids)
    except ValueError as e:
        logger.warning(f"[API] Validate form thất bại: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    if len(parsed_file_ids) != len(files):
        err_msg = (
            f"Số lượng file_ids ({len(parsed_file_ids)}) phải bằng chính xác "
            f"số lượng files ({len(files)})."
        )
        logger.warning(f"[API] {err_msg}")
        raise HTTPException(status_code=400, detail=err_msg)

    contents = await _read_and_validate_files(files)

    # --------------------------------------------------------
    # 2. Tính cache key + sinh request_id
    # --------------------------------------------------------

    request_id = str(uuid.uuid4())
    file_hashes = [_sha256_bytes(b) for b in contents]
    cache_key = _compute_cache_key(parsed_file_ids, file_hashes, parsed_data_list)
    logger.info(f"[{request_id}] Đã sinh cache_key: {cache_key[:12]}...")

    # --------------------------------------------------------
    # 3. Cache hit -> trả FINISHED ngay (trừ khi force_reprocess)
    # --------------------------------------------------------

    if not force_reprocess:
        cached = get_cached_result(cache_key)
        if cached is not None:
            logger.info(f"[{request_id}] CACHE HIT! Trả ngay kết quả FINISHED.")
            result = _convert_result_format(cached, plain_text=True)
            return {
                "request_id": request_id,
                "status": "FINISHED",
                "result": OutputResponse.model_validate(result).model_dump(
                    by_alias=True
                ),
            }

    # --------------------------------------------------------
    # 4. Lưu file PDF vào volume dùng chung
    # --------------------------------------------------------

    try:
        file_paths = _save_files(request_id, contents)
        logger.info(f"[{request_id}] Đã lưu {len(file_paths)} files vào volume.")
    except OSError as e:
        logger.error(f"[{request_id}] Không thể lưu file upload: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Không thể lưu file upload: {e}",
        )

    # --------------------------------------------------------
    # 5. Ghi job vào PostgreSQL
    # --------------------------------------------------------

    job_input = {
        "data_list": parsed_data_list,
        "file_ids": parsed_file_ids,
        "file_paths": file_paths,
        "cache_key": cache_key,
    }

    try:
        db_create_job(job_id=request_id, job_input=job_input)
        logger.info(f"[{request_id}] Đã tạo job trong DB trạng thái processing.")
    except Exception as e:
        # Dọn dẹp file đã lưu để không rác tích tụ
        shutil.rmtree(os.path.join(FILE_STORE_DIR, request_id), ignore_errors=True)
        logger.error(f"[{request_id}] Lỗi DB create_job: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Không thể lưu job vào PostgreSQL: {e}",
        )

    # --------------------------------------------------------
    # 6. Đẩy task vào RabbitMQ thông qua Celery
    # --------------------------------------------------------

    try:
        process_answer_matching.apply_async(
            kwargs={
                "data_list": parsed_data_list,
                "file_ids": parsed_file_ids,
                "file_paths": file_paths,
                "cache_key": cache_key,
                "force_reprocess": force_reprocess,
            },
            task_id=request_id,
        )
        logger.info(f"[{request_id}] Đã enqueue Celery task vào RabbitMQ thành công.")

    except OperationalError as e:
        logger.error(f"[{request_id}] Lỗi kết nối RabbitMQ: {e}")
        try:
            set_failed(request_id, f"Không kết nối được RabbitMQ: {e}")
        except Exception:
            pass

        raise HTTPException(
            status_code=503,
            detail=(
                "Không kết nối được RabbitMQ "
                "(RABBITMQ_URL sai hoặc broker không khả dụng): "
                f"{e}"
            ),
        )

    except Exception as e:
        logger.error(f"[{request_id}] Lỗi submit Celery task: {e}")
        try:
            set_failed(request_id, f"Lỗi khi submit Celery task: {e}")
        except Exception:
            pass

        raise HTTPException(
            status_code=503,
            detail=f"Không thể submit job vào RabbitMQ: {e}",
        )

    # --------------------------------------------------------
    # 7. Trả response ngay
    # --------------------------------------------------------

    return {
        "request_id": request_id,
        "status": "PROCESSING",
    }


# ============================================================
# GET /api/v1/answer-matching/{request_id}
# ============================================================

@app.get(
    "/api/v1/answer-matching/{request_id}",
    response_model=JobStatus,
)
async def get_answer_matching_status(
    request_id: str,
    plain_text: bool = True,
):
    """
    Poll trạng thái xử lý.

        PROCESSING -> chưa xong, client poll tiếp sau 2-3s
        FINISHED   -> có kết quả đầy đủ trong trường `result`
        FAILED     -> lỗi trong trường `error`

    Query param:

        plain_text=true  (mặc định) -> content dạng plain text
        plain_text=false            -> content dạng markdown đã normalize
    """

    # --------------------------------------------------------
    # Validate UUID
    # --------------------------------------------------------

    try:
        uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Request ID không tồn tại",
        )

    # --------------------------------------------------------
    # Query PostgreSQL
    # --------------------------------------------------------

    try:
        job = get_job(request_id)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể truy vấn PostgreSQL: {e}",
        )

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Request ID không tồn tại",
        )

    api_status = _db_status_to_api_status(job["status"])

    # --------------------------------------------------------
    # FAILED -> kèm error
    # --------------------------------------------------------

    if api_status == "FAILED":
        return {
            "request_id": request_id,
            "status": "FAILED",
            "error": job["error"],
        }

    # --------------------------------------------------------
    # PROCESSING
    # --------------------------------------------------------

    if api_status == "PROCESSING":
        return {
            "request_id": request_id,
            "status": "PROCESSING",
        }

    # --------------------------------------------------------
    # FINISHED -> kèm result (validate theo output schema)
    # --------------------------------------------------------

    result = _convert_result_format(job["result"], plain_text)

    return {
        "request_id": request_id,
        "status": "FINISHED",
        "result": OutputResponse.model_validate(result).model_dump(by_alias=True),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8005,
    )
