# Dịch vụ quét ảnh nhạy cảm

Bọc mô hình [nsfw-image-detection](https://github.com/PRITHIVSAKTHIUR/nsfw-image-detection)
(SigLIP2, Apache-2.0) thành một endpoint HTTP để Base CMS gọi được.

Repo gốc chỉ có demo Gradio, không gọi từ ứng dụng khác được — `app.py` ở đây là
phần bọc.

## Chạy

```bash
# Lần đầu
python -m venv .venv
.venv/Scripts/python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe tai-mo-hinh.py      # tải trọng số về ./models

# Mỗi lần
.venv/Scripts/python.exe -m uvicorn app:app --port 8000
```

Kiểm nhanh: `.venv/Scripts/python.exe kiem-thu.py`

## API

```bash
curl -X POST http://localhost:8000/scan -F "file=@anh.jpg"
```

```json
{
  "label": "Normal",
  "scores": {
    "Anime Picture": 0.0121,
    "Hentai": 0.0043,
    "Normal": 0.9612,
    "Pornography": 0.0089,
    "Enticing or Sensual": 0.0135
  },
  "nsfw_score": 0.0267,
  "took_ms": 184.2
}
```

`GET /health` trả trạng thái nạp mô hình.

### `nsfw_score` được tính thế nào

Là **tổng** ba lớp không an toàn (`Hentai`, `Pornography`, `Enticing or Sensual`),
không phải lớp cao nhất. Lý do: ảnh 0.4 `Pornography` + 0.4 `Hentai` đáng ngờ hơn
nhiều so với 0.4 `Pornography` một mình — lấy giá trị lớn nhất sẽ coi hai trường
hợp đó như nhau.

`Anime Picture` **không** tính vào: nó chỉ mô tả phong cách vẽ, không nói gì về
mức độ nhạy cảm.

## Tốc độ đo được

Đo trên máy này (CPU, không GPU), ảnh 512×512:

| | |
|---|---|
| Lần gọi đầu | ~1200 ms (gồm khởi động) |
| Ổn định | **~950–1100 ms/ảnh** |
| Nạp mô hình lúc khởi động | vài giây, một lần |

Con số này quyết định kiến trúc: **~1 giây/ảnh là quá chậm để quét đồng bộ lúc
tải lên**. Người dùng sẽ thấy nút "Tải lên" treo cả giây. Phải quét nền.

Để so sánh, NudeNet công bố ~142 ms/ảnh — nhanh hơn khoảng 7 lần, vì mô hình
của nó ~7MB còn mô hình này ~354MB.

## Ngưỡng

**Đừng dùng ngưỡng mặc định của ai cả** — phải hiệu chỉnh trên dữ liệu thật của
chính nền tảng bạn. Điểm khởi đầu để đo:

| `nsfw_score` | Xử lý |
|---|---|
| ≥ 0.90 | Chặn thẳng |
| 0.30 – 0.90 | Đưa lên đầu hàng đợi duyệt tay |
| < 0.30 | Vẫn vào hàng đợi, xếp sau |

Mô hình công bố độ chính xác 91,37% — nghĩa là **cứ 11 ảnh thì sai 1**. Điểm số
dùng để **sắp xếp** hàng đợi, không phải để tự động công bố hay xoá thay người.

## Dừng dịch vụ

```bash
# Windows
powershell -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
```

## Giới hạn

- **Chỉ nội dung khiêu dâm/gợi dục.** Bạo lực, vũ khí, ma tuý, tự hại — không có.
- **Không phủ CSAM.** Đó là hạng mục riêng, xem `docs/KIEM-DUYET-NOI-DUNG.md`.
- **Chỉ ảnh.** Video phải tách khung bằng `ffmpeg` rồi gửi từng khung — và ở
  ~1 giây/khung thì một video 3 phút lấy 1 khung/giây mất khoảng 3 phút để quét.
- **Chỉ nhận ảnh raster.** SVG bị từ chối (Pillow không đọc được) — trùng với
  giới hạn của chính luồng tải lên trong CMS.

## Chưa kiểm được

Tôi mới xác nhận **đường ống chạy thông**: nạp mô hình, nhận tệp, trả JSON đúng
nhãn. **Chưa đánh giá được độ chính xác** — chỉ mới đưa vào ảnh màu trơn sinh
tại chỗ, thứ không nói lên điều gì về khả năng phán đoán của mô hình.

Trước khi tin vào ngưỡng nào, hãy chạy thử trên một tập ảnh thật của chính nền
tảng bạn.

## Ghi chú khi cài

Tên mô hình trên HuggingFace là **`strangerguardhf/nsfw_image_detection`**.
README của repo GitHub ghi là `prithivMLmods/...` — định danh đó **không tồn
tại**, tải sẽ báo lỗi. Tên đúng nằm trong `app.py` của chính repo đó.

Cài `torch` phải chỉ rõ `--index-url .../whl/cpu`, nếu không pip kéo bản CUDA
khoảng 2.5GB mà máy không có GPU thì cũng không dùng được.

`transformers` cần thêm `torchvision` cho `AutoImageProcessor`, dù mình không
gọi trực tiếp gói đó.

## Nối vào CMS

Xem `docs/KIEM-DUYET-NOI-DUNG.md` mục 6. Tóm tắt: viết một lớp hiện thực
`ContentScanner` gọi `POST /scan` của dịch vụ này, và **thay resource handler
tĩnh `/files/**` bằng controller kiểm trạng thái** — không có bước sau thì việc
kiểm duyệt không có tác dụng gì.
