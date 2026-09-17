# -*- coding: utf-8 -*-
"""
Thiết lập logging tập trung ghi ra console và các file log trong thư mục logs/.
Hỗ trợ xoay vòng file (RotatingFileHandler).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import LOG_DIR

LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] - %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured_loggers = {}


def get_logger(name: str, log_filename: str | None = None) -> logging.Logger:
    """
    Lấy logger đã được cấu hình.
    Nếu có log_filename (vd: 'api.log', 'worker.log', 'pipeline.log'),
    sẽ gắn thêm RotatingFileHandler ghi vào logs/<log_filename>.
    """
    logger = logging.getLogger(name)
    if name in _configured_loggers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # Console Handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler
    if log_filename:
        file_path = LOG_DIR / log_filename
        file_handler = RotatingFileHandler(
            filename=str(file_path),
            maxBytes=20 * 1024 * 1024,  # 20 MB mỗi file
            backupCount=5,              # Giữ tối đa 5 file backup
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _configured_loggers[name] = logger
    return logger
