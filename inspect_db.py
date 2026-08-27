import json
import os
from datetime import datetime

from sqlalchemy import create_engine, text


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:mysecretpassword@localhost:5432/postgres",
)

OUTPUT_FILE = "jobs_dump.txt"

engine = create_engine(DATABASE_URL)


def format_value(value):
    """Format dữ liệu để ghi ra txt dễ đọc."""

    if value is None:
        return "NULL"

    if isinstance(value, (dict, list)):
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        )

    if isinstance(value, datetime):
        return value.isoformat()

    return str(value)


def dump_jobs():
    """Đọc toàn bộ bảng jobs và ghi ra file txt."""

    query = text(
        """
        SELECT
            id,
            status,
            input,
            result,
            error,
            created_at,
            updated_at
        FROM jobs
        ORDER BY created_at DESC
        """
    )

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    lines = []

    lines.append("=" * 100)
    lines.append("POSTGRESQL - JOBS TABLE")
    lines.append("=" * 100)
    lines.append(f"DATABASE_URL: {DATABASE_URL}")
    lines.append(f"Tổng số job: {len(rows)}")
    lines.append("")

    if not rows:
        lines.append("Không có job nào trong bảng jobs.")
    else:
        for index, row in enumerate(rows, start=1):

            lines.append("=" * 100)
            lines.append(f"JOB #{index}")
            lines.append("=" * 100)

            lines.append(
                f"job_id     : {row['id']}"
            )

            lines.append(
                f"status     : {row['status']}"
            )

            lines.append(
                f"created_at : {format_value(row['created_at'])}"
            )

            lines.append(
                f"updated_at : {format_value(row['updated_at'])}"
            )

            lines.append("")
            lines.append("INPUT:")
            lines.append("-" * 100)
            lines.append(
                format_value(row["input"])
            )

            lines.append("")
            lines.append("RESULT:")
            lines.append("-" * 100)
            lines.append(
                format_value(row["result"])
            )

            lines.append("")
            lines.append("ERROR:")
            lines.append("-" * 100)
            lines.append(
                format_value(row["error"])
            )

            lines.append("")

    output = "\n".join(lines)

    # In ra terminal
    print(output)

    # Ghi ra file
    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(output)

    print("")
    print("=" * 100)
    print(f"Đã ghi kết quả vào: {OUTPUT_FILE}")
    print("=" * 100)


if __name__ == "__main__":
    try:
        dump_jobs()

    except Exception as e:
        print("Không thể đọc PostgreSQL.")
        print(f"Lỗi: {type(e).__name__}: {e}")
        raise

    