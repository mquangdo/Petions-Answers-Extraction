#!/bin/bash
# Tự động kích hoạt môi trường conda nếu celery/python3 chưa được nạp
if ! command -v celery >/dev/null 2>&1; then
    if [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then
        . "/opt/conda/etc/profile.d/conda.sh"
        conda activate surya 2>/dev/null || true
    fi
    if ! command -v celery >/dev/null 2>&1 && [ -d "/home/jovyan/.conda/envs/surya/bin" ]; then
        export PATH="/home/jovyan/.conda/envs/surya/bin:$PATH"
    fi
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH}"

echo "========================================="
echo " Stopping all running Celery Workers..."
echo "========================================="

# Bắt theo 'celeryd' hoặc 'tasks.celery_app' để khớp đúng tên tiến trình hiển thị
PATTERN="celeryd|tasks\.celery_app"

# 1. Thử SIGTERM (-15) trước
pkill -15 -f "$PATTERN"

sleep 2

# 2. Ép dừng bằng SIGKILL (-9) nếu vẫn còn
if pgrep -f "$PATTERN" > /dev/null; then
    echo "[INFO] Force stopping remaining workers..."
    pkill -9 -f "$PATTERN"
    sleep 1
fi

# 3. Kiểm tra lại
count=$(pgrep -f "$PATTERN" | wc -l)
if [ "$count" -eq 0 ]; then
    echo "[SUCCESS] All Celery workers have been stopped successfully."
else
    echo "[WARNING] Still found $count running worker process(es)."
fi

echo "[INFO] Purging all pending tasks in Celery queues..."
celery -A tasks.celery_app purge -f

echo "[INFO] Deleting old RabbitMQ queues to apply x-max-priority..."
python3 -c "
from kombu import Connection
import os
broker_url = os.environ.get('CELERY_BROKER_URL', 'amqp://admin:admin123@localhost:5672//')
try:
    with Connection(broker_url) as conn:
        channel = conn.channel()
        for q in ['worker1_queue', 'worker2_queue', 'worker3_queue', 'worker4_queue', 'celery']:
            try:
                channel.queue_delete(queue=q)
                print(f'Deleted queue: {q}')
            except Exception as e:
                pass
except Exception:
    pass
"