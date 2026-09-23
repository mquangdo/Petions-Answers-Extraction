# -*- coding: utf-8 -*-
"""
Router: định tuyến trích xuất theo cơ quan ban hành (Bộ).

Luồng: detect tên Bộ từ markdown OCR -> tra bảng MINISTRY_TO_MODULE
(định nghĩa cứng trong file này) -> load file đơn modules/<mã>.py
(regex + functions đã gộp) -> trích xuất.
- KHÔNG import functions (self-contained): danh sách tên Bộ, chuẩn hóa dấu
  và vòng detect tự implement trong file này.
- KHÔNG fallback root: Bộ lạ / không detect được -> mặc định
  "Bộ Nông nghiệp và Môi trường" (module nnmt). Module lỗi/trả rỗng
  -> kết quả rỗng (không bao giờ nổ job).

Preload: common.py + toàn bộ 13 module được load 1 lần lúc import
(single-threaded, trước khi worker spawn thread) nên runtime chỉ đọc dict
cache — không lock, không race sys.modules. Module nào load lỗi thì bỏ qua
(coi như chưa load); common lỗi thì raise loud (single point of failure).
"""

import asyncio
import importlib.util
import re
import sys
import unicodedata
from pathlib import Path

from logger import get_logger

logger = get_logger("router", "pipeline.log")

_MODULES_DIR = Path(__file__).resolve().parent / "modules"

# Danh sách tên cơ quan chuẩn (copy từ common._BO_NAMES + 2 cơ quan ngoài
# Bộ có module riêng; đồng bộ tay khi common.py đổi, đã bổ sung từ CQTQ.txt).
_BO_NAMES = (
    "Bộ Nông nghiệp và Môi trường",
    "Bộ Tài nguyên và Môi trường",
    "Bộ Tài chính",
    "Bộ Công Thương",
    "Bộ Y tế",
    "Bộ Xây dựng",
    "Bộ Giao thông vận tải",
    "Bộ Giáo dục và Đào tạo",
    "Bộ Kế hoạch và Đầu tư",
    "Bộ Văn hóa, Thể thao và Du lịch",
    "Bộ Lao động - Thương binh và Xã hội",
    "Bộ Khoa học và Công nghệ",
    "Bộ Thông tin và Truyền thông",
    "Bộ Tư pháp",
    "Bộ Nội vụ",
    "Bộ Quốc phòng",
    "Bộ Công an",
    "Bộ Ngoại giao",
    "Ban tổ chức Trung ương",
    "Ủy ban Dân nguyện và Giám sát",
    "Văn phòng Quốc hội",
    "Ủy ban Trung ương Mặt trận Tổ quốc VN",
    "Tổng liên đoàn Lao động VN",
    "Ban Nội chính Trung ương",
    "Văn phòng Chính phủ",
    "Ngân hàng Nhà nước Việt Nam",
    "Ủy ban Kinh tế và Tài chính",
    "Ngân hàng Chính sách xã hội",
    "Bộ Văn hóa Thể thao du lịch",
    "Ủy ban Kiểm tra Trung ương",
    "Ủy ban Công tác đại biểu",
    "Ủy ban Khoa học Công nghệ và Môi trường",
    "Bộ Dân tộc và Tôn giáo",
    "Ủy ban Pháp luật và Tư pháp",
    "Văn phòng Trung ương Đảng",
    "Thanh tra Chính phủ",
    "Ban Tuyên giáo Trung ương",
    "Ủy ban Văn hóa và Xã hội",
)

# Cơ quan ngoài Bộ có module riêng, tên không nằm trong _BO_NAMES nên phải
# detect riêng (cả đường chính xác lẫn đường lột dấu):
#   - UBTWMTTQ: intro/body ghi tắt "Ủy ban Trung ương MTTQ ..."; letterhead
#     hay bị OCR sai dấu ("MĂT TRẬN TÔ QUÔC") nên thêm bản đầy đủ
#     "Ủy ban Trung ương Mặt trận Tổ quốc" để đường lột dấu bắt được.
#   - TANDTC: letterhead OCR sai ("TÔI/TỔI CAO") nhưng intro/body ghi chuẩn
#     "Tòa án nhân dân tối cao".
_EXTRA_NAMES = (
    "Ủy ban Trung ương MTTQ",
    "Ủy ban Trung ương Mặt trận Tổ quốc",
    "Tòa án nhân dân tối cao",
)

# Bỏ qua khi detect: "Ủy ban Dân nguyện và Giám sát" là đơn vị CHUYỂN/NHẬN
# kiến nghị (Kính gửi + "Phúc đáp Công văn số 498/UBDNGS16..."), xuất hiện
# SỚM trong mọi thư nhưng KHÔNG phải cơ quan ban hành. Không skip thì nó
# thắng first-match và hijack toàn bộ file có letterhead nát (đã đo: 36 file
# route nhầm, BTCTW mất 24→9 petition). Gặp tên này thì quét tiếp dòng sau.
_SKIP_MINISTRIES = ("Ủy ban Dân nguyện và Giám sát",)

# Bảng map cứng: tên cơ quan chuẩn (có dấu) -> mã module trong modules/.
# Bộ lạ (detect được tên nhưng chưa có module) và không detect được ->
# mặc định "Bộ Nông nghiệp và Môi trường" (module nnmt), KHÔNG fallback root.
MINISTRY_TO_MODULE = {
    "Bộ Nông nghiệp và Môi trường": "nnmt",
    "Bộ Nội vụ": "nv",
    "Bộ Công an": "ca",
    "Bộ Giáo dục và Đào tạo": "gddt",
    "Bộ Quốc phòng": "qp",
    "Bộ Văn hóa, Thể thao và Du lịch": "vh",
    "Bộ Khoa học và Công nghệ": "khcn",
    "Bộ Công Thương": "ct",
    "Bộ Y tế": "yt",
    "Ban Tổ chức Trung ương": "btctw",
    "Ủy ban Trung ương MTTQ": "ubtwmttq",
    "Ủy ban Trung ương Mặt trận Tổ quốc": "ubtwmttq",
    "Tòa án nhân dân tối cao": "tandtc",
    # Biến thể tên (không dấu phẩy / viết tắt): map về module của tên chuẩn,
    # nếu không detect trúng biến thể sẽ rớt nhầm default nnmt.
    "Bộ Văn hóa Thể thao du lịch": "vh",
    "Ủy ban Trung ương Mặt trận Tổ quốc VN": "ubtwmttq",
}

DEFAULT_MINISTRY = "Bộ Nông nghiệp và Môi trường"
DEFAULT_MODULE = "nnmt"

_EMPTY_METADATA = {"so_cong_van": None, "ngay_ban_hanh": None, "nguoi_ky": None}


def _strip_accents_lower(s: str) -> str:
    """Chữ thường + 'đ'->'d' + lột toàn bộ dấu tiếng Việt (copy functions)."""
    if not s:
        return ""
    s = s.lower().replace("đ", "d")
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", s).strip()


# Tên chuẩn hóa (lột dấu) để match tolerant khi OCR lỗi dấu tên Bộ.
# Gộp cả _EXTRA_NAMES để letterhead OCR nát (vd "MĂT TRẬN TÔ QUÔC",
# "TÒA ÁN NHÂN DÂN TÔI CAO") vẫn bắt được qua đường lột dấu.
_NORM_NAMES = {_strip_accents_lower(n): n for n in _BO_NAMES + _EXTRA_NAMES}
_NORM_MODULE = {_strip_accents_lower(k): v for k, v in MINISTRY_TO_MODULE.items()}
_SKIP_NORM = {_strip_accents_lower(s) for s in _SKIP_MINISTRIES}


def detect_ministry(md_text: str) -> str | None:
    """Detect tên cơ quan ban hành từ vùng letterhead (30 dòng đầu).

    Quét từng dòng từ trên xuống, trả tên ĐẦU TIÊN khớp dict (đường chính
    xác trước, đường lột dấu sau). Ngoại lệ duy nhất: tên trong skip-list
    (đơn vị chuyển/nhận, vd UBDNGS) thì bỏ qua, quét tiếp — vì nó luôn đứng
    trước tên cơ quan ban hành trong Kính gửi/Phúc đáp. Trả tên chuẩn có
    dấu, hoặc None khi không thấy.
    """
    if not md_text:
        return None
    for line in md_text.splitlines()[:30]:
        if not line.strip():
            continue
        low = line.lower()
        for name in _BO_NAMES:
            if name.lower() in low:
                if _strip_accents_lower(name) not in _SKIP_NORM:
                    return name
        for name in _EXTRA_NAMES:
            if name.lower() in low:
                if _strip_accents_lower(name) not in _SKIP_NORM:
                    return name
        norm = _strip_accents_lower(line)
        for norm_name, canonical in _NORM_NAMES.items():
            if norm_name and norm_name in norm:
                if _strip_accents_lower(canonical) not in _SKIP_NORM:
                    return canonical
    return None


def _lookup_module(ministry: str | None) -> str:
    """Tra mã module theo tên Bộ; lạ/không có -> mặc định module nnmt."""
    if ministry:
        code = MINISTRY_TO_MODULE.get(ministry)
        if code is None:
            code = _NORM_MODULE.get(_strip_accents_lower(ministry))
        if code is not None:
            return code
        logger.info(f"Bộ '{ministry}' lạ/chưa có module -> mặc định {DEFAULT_MINISTRY}")
    else:
        logger.info(f"không detect được Bộ -> mặc định {DEFAULT_MINISTRY}")
    return DEFAULT_MODULE


def _load_common_once() -> None:
    """Load common.py (root) MỘT lần, GIỮ alias sys.modules["common"].

    Mọi functions.py đều `from common import ...` nên alias phải tồn tại
    trong SUỐT preload (không restore giữa chừng như regexes từng module).
    Giữ nguyên sau preload để `import common` ở đâu cũng resolve đúng.
    Common lỗi -> raise LOUD (single point of failure: im lặng sẽ thành 13
    warning khó hiểu + toàn bộ file trả rỗng).
    """
    path = _MODULES_DIR.parent / "common.py"
    spec = importlib.util.spec_from_file_location("common", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["common"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        sys.modules.pop("common", None)
        raise RuntimeError(f"common.py load thất bại, dừng preload: {e}") from e


def _load_module_functions(code: str):
    """Load modules/<code>.py (file đơn đã gộp regexes + functions).

    "common" đã swap sẵn ngoài vòng lặp nên `from common import ...` trong
    file module luôn ăn đúng. Không còn alias "regexes" để swap/restore.
    Chỉ gọi lúc preload single-threaded.
    """
    path = _MODULES_DIR / f"{code}.py"
    spec = importlib.util.spec_from_file_location(f"modules_{code}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _preload_all() -> dict:
    """Load common + toàn bộ module trong map lúc import (single-threaded)."""
    _load_common_once()
    loaded = {}
    seen = set()
    for code in MINISTRY_TO_MODULE.values():
        if code in seen:
            continue
        seen.add(code)
        try:
            loaded[code] = _load_module_functions(code)
            logger.info(f"[router] preload module '{code}' OK")
        except Exception as e:
            logger.warning(
                f"[router] preload module '{code}' thất bại ({e}) "
                "-> file của Bộ này trả rỗng"
            )
    return loaded


_MODULE_FUNCS = _preload_all()


def _empty_result(ministry: str, module: str) -> dict:
    """Kết quả rỗng (module lỗi/trả rỗng): giữ contract, không nổ job."""
    return {
        "petitions": [],
        "metadata": dict(_EMPTY_METADATA),
        "ministry": ministry,
        "module": module,
    }


async def route_extract(
    md_text: str,
    llm_semaphore=None,
    filename: str = "",
) -> dict:
    """Định tuyến trích xuất 1 văn bản theo Bộ. Trả dict:
        {"petitions": [...], "metadata": {...},
         "ministry": str, "module": str}
    ministry không bao giờ None (mặc định Bộ Nông nghiệp và Môi trường); module là mã module đã
    thử (không có "root" — đã bỏ fallback root).
    llm_semaphore giữ lại cho tương thích caller (hiện không dùng: module
    sync chạy to_thread, không gọi LLM).
    """
    tag = f"[{filename}] " if filename else ""

    ministry = detect_ministry(md_text)
    if ministry is None:
        logger.info(f"{tag}không detect được Bộ -> mặc định {DEFAULT_MINISTRY}")
        ministry = DEFAULT_MINISTRY
    code = _lookup_module(ministry)
    fn = _MODULE_FUNCS.get(code)

    if fn is None:
        logger.warning(f"{tag}module {code} chưa load được -> trả rỗng")
        return _empty_result(ministry, code)

    try:
        # Module là sync -> chạy trong thread để không block event loop.
        petitions = await asyncio.to_thread(fn.extract_petitions, md_text)
    except Exception as e:
        logger.warning(f"{tag}module {code} lỗi extract ({e}) -> trả rỗng")
        return _empty_result(ministry, code)

    # Lọc petition rỗng hoàn toàn (cả noi_dung + tra_loi trống): không mang
    # thông tin, chỉ gây nhiễu cặp Jaccard downstream.
    petitions = [
        p for p in petitions
        if (p.get("noi_dung") or "").strip()
        or (p.get("tra_loi") or "").strip()
    ]

    try:
        metadata = fn.extract_metadata(md_text)
    except Exception as e:
        logger.warning(f"{tag}module {code} lỗi metadata ({e})")
        metadata = dict(_EMPTY_METADATA)

    logger.info(
        f"{tag}route {ministry} -> module {code}: {len(petitions)} petition(s)"
    )
    return {
        "petitions": petitions,
        "metadata": metadata,
        "ministry": ministry,
        "module": code,
    }
