# -*- coding: utf-8 -*-
"""
Tầng PostgreSQL cho Answer Matching v2.

1. Bảng `jobs`: vòng đời 1 request
       PROCESSING (API tạo) -> FINISHED (có result) | FAILED (có error)
   (Trong DB lưu nội bộ là processing/succeeded/failed — mapping sang
   PROCESSING/FINISHED/FAILED diễn ra ở API layer.)

2. Bảng `result_cache`: cache kết quả theo cache_key
       cache_key = SHA-256(file_ids + hash từng file bytes + data_list)
   Cache hit cho phép trả FINISHED ngay mà không chạy lại AI Pipeline.
   force_reprocess=true bỏ qua đọc cache (nhưng vẫn ghi đè).

Env var:
    DATABASE_URL  ví dụ:
    postgresql+psycopg2://postgres:mysecretpassword@localhost:5432/postgres
"""

import os
from datetime import datetime

from sqlalchemy import String, Text, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:mysecretpassword@localhost:5432/postgres",
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    input: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


class ResultCache(Base):
    __tablename__ = "result_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    data_list: Mapped[list] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


def create_tables() -> None:
    """Tạo bảng nếu chưa có (idempotent, gọi nhiều lần an toàn)."""
    Base.metadata.create_all(engine, checkfirst=True)


def create_job(job_id: str, job_input: dict) -> None:
    """API gọi: ghi bản ghi job mới với status='processing'."""
    with SessionLocal() as session:
        session.add(Job(id=job_id, status="processing", input=job_input))
        session.commit()


def set_succeeded(job_id: str, result: dict) -> None:
    """Worker gọi khi pipeline chạy xong không lỗi."""
    _update(job_id, status="succeeded", result=result, error=None)


def set_failed(job_id: str, error: str) -> None:
    """Worker gọi khi pipeline lỗi."""
    _update(job_id, status="failed", error=error)


def get_job(job_id: str) -> dict | None:
    """API GET /api/v1/answer-matching/{id}: trả dict hoặc None nếu không tồn tại."""
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        return {
            "job_id": str(job.id),
            "status": job.status,
            "input": job.input,
            "result": job.result,
            "error": job.error,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Result cache
# ---------------------------------------------------------------------------

def get_cached_result(cache_key: str) -> dict | None:
    """
    Trả result đã cache theo cache_key, hoặc None nếu cache miss.
    Lỗi truy vấn (DB tạm thời không nối được) -> coi như cache miss,
    job vẫn chạy pipeline bình thường thay vì chết 500.
    """
    try:
        with SessionLocal() as session:
            row = session.get(ResultCache, cache_key)
            if row is None:
                return None
            return dict(row.result)
    except Exception as e:
        print(f"[cache] Không thể đọc cache {cache_key[:12]}...: {e}")
        return None


def put_cached_result(cache_key: str, data_list: list[dict], result: dict) -> None:
    """
    Lưu/ghi đè kết quả vào cache (upsert theo cache_key).
    data_list gốc được lưu kèm để phục vụ debug/re-map file_id.
    Ghi đè giúp force_reprocess=true luôn cập nhật bản mới nhất.
    Lỗi cache (vd DB đầy) không được làm hỏng job -> nuốt exception.
    """
    try:
        with SessionLocal() as session:
            row = session.get(ResultCache, cache_key)
            if row is None:
                session.add(
                    ResultCache(cache_key=cache_key, data_list=data_list, result=result)
                )
            else:
                row.data_list = data_list
                row.result = result
            session.commit()
    except Exception as e:
        print(f"[cache] Không thể ghi cache cho {cache_key[:12]}...: {e}")


def find_cache_keys_by_data(data_list_json: str) -> list[str]:
    """
    (Debug/helper) Tìm các cache_key có data_list khớp chuỗi JSON chỉ định.
    Ít khi dùng; chủ yếu để inspect DB.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(ResultCache.cache_key).where(ResultCache.data_list == data_list_json)
        ).scalars().all()
        return list(rows)


def _update(job_id: str, **fields) -> None:
    """UPDATE 1 dòng job; job không tồn tại thì bỏ qua (không lỗi)."""
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        session.commit()
