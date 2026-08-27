# -*- coding: utf-8 -*-
"""Test nhanh normalize/strip + _convert_result_format (chạy tay)."""
import extract_api_server as srv
from postprocess import normalize_markdown, strip_markdown

# --- Unit test 2 hàm postprocess ---
cases_norm = [
    ("Bộ trưởng ### Trịnh Việt Hùng", "Bộ trưởng Trịnh Việt Hùng"),
    ("(I)** Về ban hành Nghị định", "(I) Về ban hành Nghị định"),
    ("1.** Về kiến nghị", "1. Về kiến nghị"),
    ("thì **Chủ tịch** Ủy ban nhân dân", "thì **Chủ tịch** Ủy ban nhân dân"),
]
cases_strip = [
    ("thì **Chủ tịch** Ủy ban nhân dân", "thì Chủ tịch Ủy ban nhân dân"),
    ("Dòng 1\n\nDòng 2", "Dòng 1\n\nDòng 2"),
    ("Bộ trưởng ### Trịnh Việt Hùng", "Bộ trưởng Trịnh Việt Hùng"),
    ("11. Trường hợp khu công nghiệp", "11. Trường hợp khu công nghiệp"),
]
for src, want in cases_norm:
    got = normalize_markdown(src)
    assert got == want, f"normalize {src!r} -> {got!r}, want {want!r}"
for src, want in cases_strip:
    got = strip_markdown(src)
    assert got == want, f"strip {src!r} -> {got!r}, want {want!r}"

# --- Test _convert_result_format với dữ liệu mô phỏng kết quả thật ---
result_md = {
    "data_list": [
        {
            "KN_KIENNGHI.ID": 101,
            "answers": [
                {
                    "content": "(I)** Về ban hành Nghị định:\n\nthì **Chủ tịch** Ủy ban nhân dân quyết định.",
                    "hashFile": "a" * 64,
                    "metadata": {
                        "nguoi_ky": "Bộ trưởng ### Trịnh Việt Hùng",
                        "so_cong_van": "/BNNMT-PC",
                        "ngay_ban_hanh": None,
                    },
                }
            ],
        }
    ],
    "metadata_all": {"matched_count": 1, "unmatched_count": 0},
}

plain = srv._convert_result_format(result_md, plain_text=True)
ans = plain["data_list"][0]["answers"][0]
assert ans["content"] == "(I) Về ban hành Nghị định:\n\nthì Chủ tịch Ủy ban nhân dân quyết định.", repr(ans["content"])
assert ans["metadata"]["nguoi_ky"] == "Bộ trưởng Trịnh Việt Hùng"

md = srv._convert_result_format(result_md, plain_text=False)
ans2 = md["data_list"][0]["answers"][0]
assert "**Chủ tịch**" in ans2["content"], "markdown mode phải giữ bold"
assert "(I)**" not in ans2["content"], "artifact phải được dọn"
assert ans2["metadata"]["nguoi_ky"] == "Bộ trưởng Trịnh Việt Hùng"

# DB result không bị mutate
assert "**Chủ tịch**" in result_md["data_list"][0]["answers"][0]["content"]
assert "###" in result_md["data_list"][0]["answers"][0]["metadata"]["nguoi_ky"]

# --- Test OutputResponse khớp output_schema_v2.json ---
out = srv.OutputResponse.model_validate(
    srv._convert_result_format(result_md, plain_text=True)
)
dumped = out.model_dump(by_alias=True)
assert set(dumped.keys()) == {"data_list", "metadata_all"}
item = dumped["data_list"][0]
assert set(item.keys()) == {"KN_KIENNGHI.ID", "answers"}
ans_d = item["answers"][0]
assert set(ans_d.keys()) == {"content", "hashFile", "metadata"}
assert set(ans_d["metadata"].keys()) == {"so_cong_van", "ngay_ban_hanh", "nguoi_ky"}

print("ALL TESTS OK (unit + convert + no-mutation + schema v2 shape)")
