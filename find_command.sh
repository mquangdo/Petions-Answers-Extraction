#!/usr/bin/env bash
# find_vllm_command.sh
# Mục đích: Từ một PID (có thể chỉ hiển thị "VLLM::EngineCore" do setproctitle),
# truy ngược lên tiến trình cha thực sự để lấy full command gốc đã launch vllm serve.
#
# Usage: ./find_vllm_command.sh <PID>

set -euo pipefail

PID="${1:?Usage: $0 <PID>}"

if [ ! -d "/proc/$PID" ]; then
    echo "Lỗi: PID $PID không tồn tại." >&2
    exit 1
fi

echo "=========================================="
echo "1. Cmdline trực tiếp của PID $PID"
echo "=========================================="
tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null; echo
echo

echo "=========================================="
echo "2. Cây tiến trình tổ tiên (ancestors)"
echo "=========================================="
pstree -p -a -s "$PID" 2>/dev/null || echo "(pstree không khả dụng)"
echo

echo "=========================================="
echo "3. Duyệt ngược PPID để tìm command gốc"
echo "=========================================="
current_pid="$PID"
while true; do
    ppid=$(awk '/^PPid:/ {print $2}' "/proc/$current_pid/status" 2>/dev/null)
    if [ -z "$ppid" ] || [ "$ppid" = "0" ]; then
        break
    fi

    cmd=$(tr '\0' ' ' < "/proc/$ppid/cmdline" 2>/dev/null)
    echo "PID $ppid -> $cmd"

    # Nếu tìm thấy dòng lệnh chứa "vllm serve", đây là ứng viên command gốc
    if echo "$cmd" | grep -q "vllm serve\|vllm\.entrypoints"; then
        echo
        echo ">>> Tìm thấy command gốc tại PID $ppid <<<"
        echo "$cmd"
        FOUND_PID="$ppid"
        FOUND_CMD="$cmd"
    fi

    # Dừng khi chạm PID 1 (init/orphan) hoặc lặp vô hạn
    if [ "$ppid" = "1" ]; then
        echo "(Đã chạm PID 1 - tiến trình bị orphan/reparent)"
        break
    fi
    current_pid="$ppid"
done
echo

echo "=========================================="
echo "4. Tất cả tiến trình hiện tại liên quan đến vllm"
echo "=========================================="
pgrep -af "vllm" 2>/dev/null || echo "(không tìm thấy)"
echo

echo "=========================================="
echo "5. Kiểm tra port đang được vllm sử dụng (nếu biết)"
echo "=========================================="
ss -tlnp 2>/dev/null | grep -i "$PID" || echo "(không map được qua ss, thử: lsof -i -a -p $PID)"
echo

if [ -n "${FOUND_CMD:-}" ]; then
    echo "=========================================="
    echo "KẾT QUẢ: Command gốc đã launch vllm serve"
    echo "=========================================="
    echo "PID: $FOUND_PID"
    echo "CMD: $FOUND_CMD"
else
    echo "Không tìm thấy trực tiếp 'vllm serve' trong chuỗi tổ tiên."
    echo "Khả năng: tiến trình đã bị reparent vào PID 1 (orphan)."
    echo "Bước tiếp theo: kiểm tra Jupyter kernel/console sessions bằng:"
    echo "  curl -s http://localhost:8888/api/sessions | python3 -m json.tool"
fi