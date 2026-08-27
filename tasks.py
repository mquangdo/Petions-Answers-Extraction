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

from db import create_tables, get_cached_result, put_cached_result, set_failed, set_succeeded
from pipeline import run_pipeline
from schemas import InternalJobPayload

# "answer_matching" ở đây chỉ là TÊN APP (nhãn), không phải queue.
# Broker URL đọc từ env var, ví dụ:
#   amqp://admin:admin123@localhost:5672/
celery_app = Celery("answer_matching", broker=os.environ["RABBITMQ_URL"])

# Đây mới là chỗ khai báo QUEUE riêng trên RabbitMQ (durable mặc định).
celery_app.conf.task_default_queue = "answer_matching"


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
        print(f"[process_answer_matching] Job {job_id} THẤT BẠI: {e}")
        raise

    print(
        f"[process_answer_matching] Bắt đầu job {job_id}: "
        f"{len(payload.data_list)} kiến nghị, {len(payload.file_ids)} file"
    )

    # Double-check cache (cache_key do API tính và truyền theo message)
    if cache_key:
        cached = get_cached_result(cache_key)
        if cached is not None:
            set_succeeded(job_id, cached)
            print(
                f"[process_answer_matching] Job {job_id} CACHE HIT — "
                "bỏ qua pipeline."
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
        print(f"[process_answer_matching] Job {job_id} THẤT BẠI: {e}")
        raise

    if cache_key:
        put_cached_result(cache_key, payload.data_list, result)

    set_succeeded(job_id, result)
    print(
        f"[process_answer_matching] Hoàn tất job {job_id}: "
        f"matched={result['metadata_all']['matched_count']}, "
        f"unmatched={result['metadata_all']['unmatched_count']}"
    )
    return result
