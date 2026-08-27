
# Image dùng chung cho API server và Celery worker.
#
# API:    docker run ... -v file_store:/data/files -e FILE_STORE_DIR=/data/files ...
# Worker: docker run ... -v file_store:/data/files -e FILE_STORE_DIR=/data/files ...
#         command: celery -A tasks worker -Q answer_matching --pool=threads -c 4
#
# API và worker BẮT BUỘC mount cùng 1 volume vì message queue chỉ chứa
# đường dẫn file, binary PDF nằm trên volume dùng chung.

FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY extract_api_server.py functions.py postprocess.py regexes.py schemas.py tasks.py pipeline.py db.py ./

EXPOSE 8005

CMD ["uvicorn", "extract_api_server:app", "--host", "0.0.0.0", "--port", "8005"]
