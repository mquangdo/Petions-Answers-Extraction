# -*- coding: utf-8 -*-
"""
Cấu hình tập trung cho toàn bộ service Answer Matching v2.
Đọc từ biến môi trường (environment variables) hoặc gán giá trị mặc định hợp lý.
"""

import os
from pathlib import Path

# Thư mục gốc dự án
BASE_DIR = Path(__file__).resolve().parent

# Thư mục chứa logs
LOG_DIR = Path(os.environ.get("LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Thư mục lưu file upload tạm thời
FILE_STORE_DIR = os.environ.get("FILE_STORE_DIR", str(BASE_DIR / "uploaded_files"))
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024

# Database (PostgreSQL)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:mysecretpassword@localhost:5432/postgres",
)
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
DB_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "3600"))

# Message Queue (Celery / RabbitMQ)
RABBITMQ_URL = os.environ.get(
    "RABBITMQ_URL",
    "amqp://admin:admin123@localhost:5672//",
)
CELERY_QUEUE = os.environ.get("CELERY_QUEUE", "answer_matching")

# OCR Service
OCR_URL = os.environ.get("OCR_URL", "http://127.0.0.1:8078/step1/ocr")
OCR_TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "600.0"))
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "2"))
OCR_MAX_RETRIES = int(os.environ.get("OCR_MAX_RETRIES", "5"))
RETRY_BACKOFF_BASE = float(os.environ.get("RETRY_BACKOFF_BASE", "1.0"))  # giây, nhân đôi sau mỗi lần thử lại
JITTER = float(os.environ.get("JITTER", "1.0"))                         # jitter ngẫu nhiên (giây) thêm vào delay retry

# LLM Service (vLLM / Ollama)
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8076/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "EMPTY")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "google/gemma-4-26B-A4B-it")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "300.0"))
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "1"))

# Business Logic Thresholds
JACCARD_THRESHOLD = float(os.environ.get("JACCARD_THRESHOLD", "0.5"))