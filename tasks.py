# -*- coding: utf-8 -*-
"""
Khai báo Celery app + task cho pipeline Answer Matching.

Worker consume queue "answer_matching" và chạy toàn bộ pipeline:
đọc PDF từ volume dùng chung -> OCR -> extract petitions/metadata -> Jaccard
mapping -> ghi cache. Logic nghiệp vụ nằm ở pipeline.py.

Chạy worker (Windows bắt buộc --pool=threads, prefork không hỗ trợ):
    celery -A tasks worker -Q answer_matching --pool=threads -c 4 --loglevel=info
"""

import os

from celery import Celery

from config import CELERY_QUEUE, RABBITMQ_URL
from db import create_tables, get_cached_result, put_cached_result, set_failed, set_succeeded
from logger import get_logger
from pipeline import run_pipeline
from schemas import InternalJobPayload

logger = get_logger("worker", "worker.log")

celery_app = Celery("answer_matching", broker=RABBITMQ_URL)

celery_app.conf.task_default_queue = CELERY_QUEUE
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.task_acks_late = True


@celery_app.task(bind=True, name="process_answer_matching")
def process_answer_matching(self, data_list, file_ids, file_paths, cache_key=None):
    """
    Xử lý 1 job:

        1. Validate payload (phòng khi message đến từ nguồn khác ngoài API).
        2. Check cache lần nữa (chống race với request force_reprocess=false
           gửi cùng lúc từ client khác).
        3. Chạy pipeline đầy đủ.
        4. Ghi result vào cache + cập nhật job succeeded/failed trên DB.
    """
    job_id = self.request.id
    create_tables()

    try:
        payload = InternalJobPayload.model_validate(
            {
                "data_list": data_list,
                "file_ids": file_ids,
                "file_paths": file_paths,
            }
        )
    except Exception as e:
        set_failed(job_id, f"Payload trong queue sai schema: {e}")
        logger.error(f"[worker] Job {job_id} THẤT BẠI schema: {e}")
        raise

    logger.info(
        f"[worker] Bắt đầu job {job_id}: "
        f"{len(payload.data_list)} kiến nghị, {len(payload.file_ids)} file"
    )

    # Double-check cache (cache_key do API tính và truyền theo message)
    if cache_key:
        cached = get_cached_result(cache_key)
        if cached is not None:
            set_succeeded(job_id, cached)
            logger.info(
                f"[worker] Job {job_id} CACHE HIT — bỏ qua pipeline."
            )
            return cached

    try:
        result = run_pipeline(
            payload.data_list,
            payload.file_paths,
            payload.file_ids,
        )
    except Exception as e:
        set_failed(job_id, str(e))
        logger.error(f"[worker] Job {job_id} THẤT BẠI: {e}")
        raise

    if cache_key:
        put_cached_result(cache_key, payload.data_list, result)

    set_succeeded(job_id, result)
    logger.info(
        f"[worker] Hoàn tất job {job_id}: "
        f"matched={result['metadata_all']['matched_count']}, "
        f"unmatched={result['metadata_all']['unmatched_count']}"
    )
    return result
