# Modules trích xuất kiến nghị theo Bộ

Tài liệu mô tả chi tiết từng module trong `modules/`: nhiệm vụ, mục đích,
và tại sao lại cần chia theo Bộ thay vì dùng chung một bộ regex.

## 1. Vị trí trong pipeline

```
Văn bản scan (PDF/ảnh)
  → OCR (utils/batch_ocr_sync.py) → data_markdown/<Bộ>/*.md
    → modules/<mã bộ>/batch_extract.py → extract_answer_result/<Bộ>/*.json
```

Mỗi file `.json` tuân thủ `schema/output_schema_v2.json`, gồm:

```json
{
  "file": "<tên file>",
  "petitions": [
    { "noi_dung": "<nội dung kiến nghị của cử tri>",
      "tra_loi": "<nội dung cơ quan trả lời>" }
  ],
  "metadata": {
    "so_cong_van": "...",
    "ngay_ban_hanh": "DD/MM/YYYY",
    "nguoi_ky": "..."
  }
}
```

## 2. Tại sao phải chia module theo Bộ?

1. **Mỗi Bộ có văn phong công văn riêng.** Cùng là thư trả lời kiến nghị cử tri
   nhưng mốc cấu trúc khác nhau hoàn toàn:
   - NNMT/NV/KHCN: mục `1. Nội dung kiến nghị` + `2. Kết quả nghiên cứu...`;
   - VH/CT: intro `... nội dung như sau:` + mốc `... xin trả lời như sau:`;
   - YT: `N. Cử tri/Đề nghị...` + đáp án inline, hoặc section `N. Về...`;
   - BTCTW: cặp heading `## N. ... kiến nghị ...` + `### Trả lời/Trao đổi ...`.
   Một regex chung không thể bao hết mà không sinh false positive.
2. **Lỗi OCR đặc thù từng đợt scan.** Số công văn `6174` thành `6H4` (KHCN),
   chữ số bị tách rời `6 0 29` và `1` thành `|` (Công Thương), `Số:` thành
   `S6.6.019` (Lâm Đồng-CT), `Kết` thành `KÊt/KÉt`, `nghị` thành `nghi`
   (NNMT test v2), `Đào` thành `Đảo` (Y tế). Mỗi module chứa luật tolerant
   đúng với lỗi của Bộ mình, không làm lỏng regex của Bộ khác.
3. **Regex nhanh, xác định, miễn phí.** ~300 file chạy xong trong ~2 giây,
   kết quả tái lập 100%. Chỉ file nào regex bó tay mới gắn nhãn `llm` để
   tuyến LLM xử lý tiếp — thay vì tốn chi phí LLM cho toàn bộ.

## 3. Khung format chung f1 / f2 / f3 / llm

Mọi module dùng chung một khung phân loại (`classify_format`), chỉ khác
marker bên trong:

| Nhãn | Ý nghĩa |
|---|---|
| `f1` | 1 kiến nghị / thư (1 cặp mốc, hoặc 1 đoạn văn + đáp án văn xuôi) |
| `f2` | N kiến nghị, mỗi kiến nghị 1 mục riêng (GROUP/marker lặp lại) |
| `f3` | N kiến nghị liệt kê gộp trong 1 mục + đáp án chia theo số/heading (map theo thứ tự) |
| `llm` | Không khớp format chuẩn (thiếu mốc, số item/block lệch nhau...) → regex trả rỗng `[]`, nhường cho LLM |

Không phải Bộ nào cũng có đủ 3 format (vd VH/CT không có f2, KHCN/BTCTW
không có f3) — nhãn thừa được giữ dự phòng, `classify` không bao giờ trả về.

## 4. Cấu trúc 4 file trong mỗi module

| File | Nhiệm vụ |
|---|---|
| `regexes.py` | Toàn bộ regex của Bộ: mốc S1/S2/GROUP/item/heading đáp án, câu kết, `Nơi nhận`/chữ ký, metadata, footnote. Tách riêng để dễ test và tái sử dụng |
| `functions.py` | `classify_format` (định dạng f1/f2/f3/llm), `_extract_f*` (cắt `noi_dung`/`tra_loi`), `extract_petitions` (router), `extract_metadata` (số/ngày/người ký) |
| `postprocess.py` | Hàm dùng chung: xóa footnote OCR, làm sạch markdown, chuẩn hóa dấu câu (copy giống nhau ở mọi module) |
| `batch_extract.py` | Runner dòng lệnh: `python batch_extract.py -i <input_dir> -o <output_dir> [--overwrite]` — 1 file `.md` → 1 file `.json` (giống nhau ở mọi module) |

Nguyên tắc chung khi viết regex cho một Bộ (áp dụng cho mọi module mới):

- Khảo sát **toàn bộ** file `.md` của Bộ, tìm pattern chung nhất của phần
  kiến nghị (S1) và phần trả lời (S2) rồi viết regex theo hướng đó.
- Số ít file lệch format chung → `llm`, **không** nới regex để ép bao phủ 100%.
- Không đoán nội dung OCR hỏng (ngày rác → `None`, số sai giữ nguyên văn).
- Sub-item trong đáp án (`(1)`, `a)–e)`, `(i)–(vii)`) không bao giờ làm boundary.

## 5. Chi tiết từng module

### `nnmt` — Bộ Nông nghiệp và Môi trường (module gốc, tham chiếu)
- Marker S1 `Nội dung kiến nghị ...`, S2 `Kết quả nghiên cứu, giải quyết và
  trả lời kiến nghị`, GROUP `I. Kiến nghị số ...`, f3 `Kiến nghị số X:` +
  heading `2.x.`.
- Kết quả: 55 file → f1=40, f2=13, f3=2, llm=0 (78 petition).

### `nv` — Bộ Nội vụ
- Như `nnmt` nhưng tinh chỉnh: `_ANS_HEAD_RE` khớp mọi heading `2.x.` (trừ
  `2.x.y.`), map f3 theo thứ tự khi heading không ghi số, `_s1_inline_tail`
  lấy nội dung S1 ghi cùng dòng tiêu đề, đáp án inline sau dấu `:`.
- Kết quả: 95 file → f1=85, f2=5, f3=3, llm=2 (`Cà Mau 8350`, `Hà Nội 8349`).

### `ca` — Bộ Công an
- Khung f1/f2/f3/llm chuẩn (template nnmt).
- Kết quả: 20 file → f1=13, f2=4, f3=1, llm=2.

### `gddt` — Bộ Giáo dục và Đào tạo
- Khung f1/f2/f3/llm chuẩn (template nnmt).
- Kết quả: 19 file → f1=7, f2=12 (49 petition).

### `qp` — Bộ Quốc phòng
- Khung f1/f2/f3/llm chuẩn (template nnmt).
- Kết quả: 29 file → f1=21, f2=4, llm=4.

### `vh` — Bộ Văn hóa, Thể thao và Du lịch
- Không có mục S1/S2 kiểu NNMT. Biên trên S1 là intro `... với nội dung như
  sau:`, biên trên S2 là `... xin trả lời như sau:` (Tây Ninh thiếu `:`).
  f1 = 1 đoạn văn + đáp án văn xuôi (kể cả đáp án có section `1)`/`2)` nội bộ
  như Hà Nội → gộp thành 1 `tra_loi`); f3 = item `(N)`/`N)` + section đáp án
  `N.`/`N)` mở đầu `Liên quan/Về/Đối với`; f2 dự phòng (không xuất hiện).
- Đặc thù: sub-item `(1)(2)` lồng giữa dòng (An Giang) bị loại bằng neo `^`;
  heading đáp án số 3 của Hải Phòng bị OCR tách 2 dòng (vị trí cắt vẫn đúng).
- Metadata: chịu được `**Số:** 4974 /**BVHTTDL-VP**` và `ngày07` (Đồng Tháp).
- Kết quả: 10 file → f1=6, f3=4, llm=0 (17 petition).

### `khcn` — Bộ Khoa học và Công nghệ
- S1 ghi cụt `## 1. Nội dung kiến nghị` (không hậu tố "của cử tri") + bẫy trong
  đáp án (`### Nội dung kiến nghị được/đang/đã ...`) → S1 cần 2 lớp bảo vệ
  (hết dòng + resolver). f1/f2 theo cặp S1+S2 (GROUP `# I. Kiến nghị số N`);
  f3 dự phòng. File gộp nhiều thư (`ThanhHoa2`, 623 dòng, 8 thư) vẫn xử lý
  được vì extractor cặp S1+S2 gần nhất, không cần cắt đoạn trước.
- Metadata: số công văn OCR nát (`6H4`, `6 HFB`, mất `/`, `G182`) → neo `Số`
  đầu dòng + `BKHCN-VP` cùng dòng, lấy nguyên văn không đoán; ngày bọc bold
  cả tháng (`ngày **14** tháng **8**`).
- Kết quả: 10 file → f1=6, f2=4 (gồm ThanhHoa2 tách thành 13), llm=0
  (27 petition).

### `ct` — Bộ Công Thương (cùng họ cấu trúc với VH)
- Biên/mốc như VH nhưng tên Bộ và mốc đáp án riêng:
  `Sau khi nghiên cứu, Bộ Công Thương có ý kiến như sau:`. f1 + f3 (section
  đáp án `#### Nội dung N` trơ hết dòng); f2 dự phòng.
- Metadata: số tách rời chữ số (`6 0 29`, `6 | 0 1 8`, `6 017`) → xóa space/`|`
  (`6 | 0 1 8` → `6018`, khớp tên file); nhãn hỏng `S6.6.019` vẫn bắt được
  (lấy nguyên văn `6.6.019/BCT-KHTC`); người ký là **Thứ trưởng** ký thay
  (`KT. BỘ TRƯỞNG THỨ TRƯỞNG`, chịu OCR `THỬ TRƯỞNG`) → giữ đúng chức danh.
- Kết quả: 20 file → f1=14, f3=6, llm=0 (31 petition).

### `btctw` — Ban Tổ chức Trung ương
- Mỗi kiến nghị 1 cặp heading, không GROUP riêng: S1 `## N. Nội dung kiến
  nghị ...` / `## N. Kiến nghị số N ...`; S2 `## N. Trả lời kiến nghị`
  (chịu typo `kiên nghị`), `### Trả lời kiến nghị:`, `### Trao đổi, giải đáp:`,
  `### Trả lời cử tri:`. f1/f2 theo cặp S1+S2; f3 dự phòng. OCR sai dấu ngay
  ở marker (`CHẨP/CHÁP`, `TÔ`, `VIẾT`, `Nơi nhân`, `PHÚ/NHIỆU TRƯỞNG BAN`)
  → regex tolerant từng chỗ.
- Metadata: số không dấu `:` (`Số 1534 - CV/BTCTW`); người ký `Phó Trưởng ban
  Hà Minh Hải` (2 đường: sau dòng chức danh, hoặc tên trơ cuối file).
- Kết quả: 16 file → f1=10, f2=6, llm=0 (24 petition).

### `yt` — Bộ Y tế
- f1 (single): 1 kiến nghị không đánh số (mở đầu `Cử tri/Đề nghị/Kiến nghị`,
  có thể bọc italic) + đáp án văn xuôi; luật tách: **dòng trống đầu tiên sau
  đoạn kiến nghị là vách ngăn** (trước = `noi_dung`, sau = `tra_loi`).
- f2 (multi): item `N. ...` với allowlist
  `Cử tri|Đề nghị|Kiến nghị|Hiện nay|Theo|Việc|Thực hiện` (module cũ chỉ nhận
  2 từ đầu nên bỏ sót Lạng Sơn/Thái Nguyên/Phú Thọ/Tây Ninh/Hải Phòng; nới
  hơn nữa sẽ dính bẫy trích dẫn Quảng Trị và liệt kê `(i)` Cà Mau).
  Quy tắc trả lời gộp: item rỗng đáp án (Tây Ninh, Đồng Tháp, Đồng Nai) thì
  đáp án block sau dùng cho **cả 2** kiến nghị.
- f3 (heading): petition + section `## N. Về/Đối với` (`1. Về...` không `##`
  ở Cần Thơ, `## (2)...` có ngoặc) → gộp thành 1 `tra_loi`; preamble Cao Bằng
  giữ lại đầu đáp án.
- Metadata: fix `/**BYT-VPB**`, `ngày07`; tên ký giữ nguyên văn OCR
  (`Đảo Hồng Lan` ở HCM/Hưng Yên không tự sửa); rác ảnh ký (`*Joe Kalan*`)
  bị loại (chỉ nhận dòng `##`).
- Kết quả: 34 file → f1=4, f2=27, f3=3, llm=0 (130 petition; bản cũ 110 vì
  sót item). Lưu ý: số trong tên file không đáng tin (HCM "1 đến 11" chứa
  13 kiến nghị) — module tách theo số thứ tự thực tế trong văn bản.

### `kncn` — chưa gắn Bộ
- Template khung f1/f2/f3/llm chuẩn, chưa khảo sát dữ liệu, chưa chạy batch.
  Khi có folder dữ liệu tương ứng thì làm theo quy trình mục 4.

## 6. Cách chạy

```powershell
python -X utf8 .\modules\<mã_bộ>\batch_extract.py -i ".\data_markdown\<Tên Bộ>" -o ".\extract_answer_result\<Tên Bộ>" --overwrite
```

Ví dụ mapping mã bộ ↔ folder dữ liệu:

| Mã | Folder dữ liệu |
|---|---|
| `btctw` | `BTCTW` |
| `ca` | `Bộ Công an` |
| `ct` | `Bộ Công thương` |
| `gddt` | `Bộ Giáo dục và Đào tạo` |
| `khcn` | `Bộ Khoa học và Công nghệ` |
| `nnmt` | `Bộ Nông nghiệp và Môi trường` |
| `nv` | `Bộ Nội vụ` |
| `qp` | `Bộ Quốc phòng` |
| `vh` | `Bộ Văn hóa` |
| `yt` | `Bộ Y tế` |

Kết quả verified gần nhất (308 file, 523 petition, 0 thất bại): btctw 24,
ca 30, ct 31, gddt 49, khcn 27, nnmt 78, nv 105, qp 32, vh 17, yt 130.
File `llm` (8 file ở ca/nv/qp) ra `"petitions": []` để tuyến LLM xử lý tiếp.
