# Dịch vụ quét ảnh nhạy cảm

Bọc mô hình [nsfw-image-detection](https://github.com/PRITHIVSAKTHIUR/nsfw-image-detection)
(SigLIP2, Apache-2.0) thành một endpoint HTTP để ứng dụng khác gọi được.

Repo gốc chỉ có demo Gradio — dựng ra một trang cho người ngồi kéo thả ảnh, không
phải API. `app.py` ở đây là phần bọc còn thiếu.

**Cách chạy: xem [RUN.md](RUN.md).** Tệp này nói về những gì mô hình làm được,
làm chưa được, và những chỗ đã vấp.

## Các tệp

| Tệp | Vai trò |
|---|---|
| `app.py` | dịch vụ: bốn endpoint, xác thực, hạn mức |
| `media.py` | biến video, luồng HLS và PDF thành các khung ảnh |
| `Dockerfile`, `docker-compose.yml` | đóng gói |
| `requirements.txt` | phụ thuộc lõi |
| `requirements-media.txt` | phụ thuộc cho video/PDF, tách riêng vì lý do đệm Docker |
| `download_model.sh` | tải trọng số, 8 kết nối song song |
| `download_file.sh` | hàm tải dùng chung: 8 kết nối song song, nối tiếp được |
| `download_model_hf.py` | cách tải thay thế, một kết nối |
| `scan.py` | quét một ảnh từ dòng lệnh |
| `smoke_test.py` | xác nhận dịch vụ còn sống |
| `test_input.py` | kiểm cách xử lý đầu vào |

## API

Bốn endpoint, tất cả nhận `multipart/form-data`:

| | Trường | Nhận |
|---|---|---|
| `POST /scan` | `url` | ảnh |
| `POST /scan/video` | `url` | video |
| `POST /scan/document` | `url` hoặc `file` | PDF |
| `POST /scan/hls` | `url` | luồng `.m3u8` |

Ảnh, video và luồng **chỉ nhận link** — bên gọi đã lưu tệp ở đâu đó rồi, gửi lại
toàn bộ byte chỉ là chuyển chúng đi hai lần. Tài liệu vẫn nhận cả tệp tải lên,
vì PDF thường được giữ trước khi công bố nên chưa có link; với nó phải gửi đúng
một trong hai, cả hai hoặc không có đều trả 400.
Link trỏ vào địa chỉ nội bộ bị chặn trừ khi khai trong `ALLOW_HOSTS` — xem
`RUN.md`.

Cả bốn trả **cùng một hình dạng**, nên bên gọi chỉ cần một hàm đọc:

```json
{
  "type": "video",
  "label": "Normal",
  "scores": {
    "Anime Picture": 0.0121,
    "Hentai": 0.0043,
    "Normal": 0.9612,
    "Pornography": 0.0089,
    "Enticing or Sensual": 0.0135
  },
  "nsfw_score": 0.0267,
  "frames_scanned": 60,
  "worst_at": 128.5,
  "took_ms": 10549.3
}
```

`nsfw_score` và `scores` lấy theo **khung tệ nhất**, không phải trung bình. Một
giây phản cảm giữa một tiếng phim vô hại chính là thứ cần bắt, mà trung bình thì
nó chìm nghỉm. `worst_at` là giây (video, hls) hoặc số trang (tài liệu), và
`null` với ảnh.

Chi tiết cách gọi, trần lấy mẫu và chốt chặn SSRF: xem `RUN.md`.

`GET /health` trả trạng thái nạp mô hình, không cần khoá.

### Hạn mức

**Dịch vụ không có xác thực.** Bất cứ thứ gì chạm được tới cổng 8000 đều dùng
được nó, nên **cổng đó không được để Internet chạm tới**: gắn vào mạng nội bộ,
hoặc đặt một cổng vào phía trước lo phần xác thực.

Hạn mức vẫn còn, tính **theo địa chỉ máy gọi** và **đếm theo khung chứ không
theo lời gọi**. Một khung tốn ~220ms CPU, nên một bên gửi đều đặn là làm nghẽn
mọi bên khác dù không cố ý; còn nếu đếm theo lời gọi thì gói 60 ảnh vào một tệp
video là tiêu 60 lần ngân sách mà chỉ mất một lượt. Vượt hạn mức trả **429**.

Đây là biện pháp giữ cho một bên không chiếm hết máy, **không phải biện pháp bảo
mật**: địa chỉ thì đổi bao nhiêu cũng được, và khi đứng sau reverse proxy thì mọi
bên gọi đều mang địa chỉ của proxy nên dùng chung một ngân sách. Header
`X-Forwarded-For` cố tình không được đọc, vì tin vào một header mà bên gọi tự đặt
được thì chỉ là phát ngân sách mới theo yêu cầu.

## Tốc độ đo được

CPU 16 nhân, không GPU. Hâm nóng trước rồi đo 4 lần mỗi cỡ:

| Cỡ ảnh | Thời gian |
|---|---|
| 512×512 | 228 ms |
| 640×360 | 218 ms |
| 1920×1080 | 214 ms |
| 4000×3000 | 227 ms |

**~220 ms/ảnh, và không phụ thuộc kích thước ảnh** — bộ tiền xử lý co mọi ảnh về
256×256 trước khi chấm, nên ảnh 12 triệu điểm ảnh cũng tốn đúng bằng ảnh nhỏ.

Trong container đo được 146 ms, tức đóng gói không làm chậm đi.

> Bản trước của tệp này ghi ~950–1100 ms. Con số đó **sai** — nó đến từ những
> phép đo đơn lẻ không hâm nóng trước, nên gánh cả chi phí khởi tạo lần đầu.
> Sai lệch 4–5 lần, và nó từng dẫn tới kết luận sai là phải quét nền.

Gộp nhiều khung vào một lượt gọi chỉ nhanh hơn **1.2 lần** (8 khung: 1120ms so
với 957ms), vì torch đã trải một ảnh ra 10 luồng nên CPU gần như đã bão hoà.
Không đáng đánh đổi lấy phức tạp, nên các khung được chấm tuần tự.

Đo qua HTTP trong container, video 2 phút:

```
frames_scanned  60
took_ms         10549.3      -> ~176 ms/khung
```

Mốc thời gian đã được đối chiếu với nội dung thật: một video 120 giây gồm 6 khối
màu mỗi khối 20 giây cho ra đúng mốc đổi màu tại **0, 20, 40, 60, 80, 100** giây.
`media.py` tính mốc bằng số học (`khung thứ i × khoảng lấy mẫu`) thay vì đọc
metadata của ffmpeg, và phép đo này xác nhận cách đó đúng.

Để so sánh, NudeNet công bố ~142 ms/ảnh — vẫn nhanh hơn, nhưng khoảng cách là
1.5 lần chứ không phải 7 lần như tôi từng viết.

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

## Giới hạn

Đo được, không phải phỏng đoán:

- **Nó luôn trả lời, kể cả khi không có gì để trả lời.** Nhiễu ngẫu nhiên được
  chấm `Normal` 97.9%; ảnh 1×1 điểm được chấm `Normal` 93.6%. Softmax luôn cộng
  lại bằng 1 nên mô hình buộc phải dồn hết vào một nhãn. Điểm cao **không** có
  nghĩa là mô hình chắc chắn.
- **Chỉ nội dung khiêu dâm/gợi dục.** Bạo lực, vũ khí, ma tuý, tự hại — không có
  nhãn nào. Ảnh vũ khí sẽ được chấm `Normal` vì không có ô nào khác để bỏ vào.
- **Không phủ CSAM.** Đó là hạng mục riêng, xem `docs/KIEM-DUYET-NOI-DUNG.md`.
- **GIF và WebP động chỉ được quét khung đầu tiên.** Đã kiểm: gửi GIF có khung 0
  màu đen và các khung sau màu đỏ, dịch vụ chỉ thấy khung đen. Nội dung ở khung
  sau lọt hoàn toàn.
- **Không áp dụng EXIF Orientation.** Điện thoại chụp ảnh dọc thì lưu ảnh ngang
  kèm cờ xoay; dịch vụ bỏ qua cờ đó nên **quét ảnh ở tư thế nằm nghiêng**.
- **Video và HLS chỉ được lấy mẫu, không xem hết.** Tối đa 60 khung cho mọi độ
  dài, và luồng trực tiếp bị cắt sau 60 giây. Nội dung phản cảm lọt giữa hai
  khung lấy mẫu sẽ **không bị phát hiện** — video càng dài, khe càng rộng.
- **Tài liệu chỉ đọc phần nhìn thấy được.** Trang PDF được vẽ ra thành ảnh rồi
  chấm, nên chữ nghĩa hoàn toàn bị bỏ qua. Chỉ nhận PDF; Word, Excel, PowerPoint
  không đọc được.
- **Chỉ ảnh raster.** SVG bị từ chối.
- **Định dạng không ảnh hưởng kết quả.** PNG, GIF, BMP, TIFF, WebP cho điểm giống
  hệt nhau; JPEG lệch 0.0001 do nén mất. Chuyển đổi định dạng trước khi quét là
  vô ích — `.convert("RGB")` đã chuẩn hoá tất cả về điểm ảnh thô rồi.

## Chưa kiểm được

Mới xác nhận **đường ống chạy thông**: nạp mô hình, nhận tệp, trả JSON đúng nhãn.
**Chưa đánh giá được độ chính xác** — mới chỉ đưa vào ảnh sinh tại chỗ, thứ không
nói lên điều gì về khả năng phán đoán.

Trước khi tin vào ngưỡng nào, hãy chấm 200–300 ảnh thật của chính nền tảng bạn,
tự dán nhãn, rồi xem điểm cắt ở đâu là hợp.

## ffmpeg

`media.py` gọi `ffprobe` để lấy độ dài rồi `ffmpeg` để trích khung. Cả hai nằm
**trong image** — không phải cài gì thêm trên server.

Dùng gói của `apt`. Đã thử bản static 39MB để image gọn hơn 375MB, và **bỏ**:
nó sập ở hai chỗ độc lập.

```
URL có tên miền   ->  Segmentation fault
tệp .ts cục bộ    ->  exit 139 (SIGSEGV)
```

Cái thứ hai mới chí mạng: `.ts` chính là định dạng các đoạn HLS. Đổi một đường
ống video chạy được lấy một image nhỏ hơn là đánh đổi ngược.

### Nếu build chậm, đổi mirror

Đây là bài học đắt nhất của phần này. `apt` bò ở **17 KB/s** từ máy build, khiến
bước cài ffmpeg mất **2 tiếng**, và tôi đã đổ tại "apt nặng" rồi đi đường vòng
mấy tiếng qua bản static. Đo thật thì nguyên nhân khác hẳn:

| Mirror | Tốc độ |
|---|---|
| `deb.debian.org` (mặc định) | **không phản hồi** |
| `opensource.xtdv.net` | **12.040 KB/s** |
| `mirror.bizflycloud.vn` | 8.556 KB/s |

Cùng một tệp, nhanh hơn ~700 lần. Sau khi đổi mirror, bước đó xong trong **90
giây**.

```bash
APT_MIRROR=opensource.xtdv.net docker compose build
```

**Đo trước khi chọn**, đừng đoán như tôi:

```bash
curl -o /dev/null -w '%{speed_download}
'      https://MIRROR/debian/dists/trixie/main/binary-amd64/Packages.gz
```

Mặc định vẫn là `deb.debian.org` vì nó đúng ở mọi nơi. Các dòng có chữ
`security` **không** bị đổi — mirror có `/debian` thường không có
`/debian-security`, đổi cả hai là build hỏng với lỗi 404.

## Ghi chú khi cài

Tên mô hình trên HuggingFace là **`strangerguardhf/nsfw_image_detection`**.
README của repo GitHub ghi là `prithivMLmods/...` — định danh đó **không tồn tại**,
tải sẽ báo lỗi. Tên đúng nằm trong `app.py` của chính repo đó.

Cài `torch` phải chỉ rõ `--index-url .../whl/cpu`, nếu không pip kéo bản CUDA
khoảng 2.5GB mà máy không có GPU cũng không dùng được.

`transformers` cần thêm `torchvision` cho `AutoImageProcessor`, dù không gọi
trực tiếp gói đó.

HuggingFace bóp tốc độ **từng kết nối** (~110 KB/s), không phải băng thông tổng.
Vì vậy `download_model.sh` chia làm 8 phần — đo được ~1.1 MB/s. Và `curl --retry`
**không** nối lại từ chỗ đứt, nên mỗi phần phải tự chạy vòng lặp xin phần còn
thiếu; lần đầu không làm vậy đã ghép ra một tệp thiếu 134MB.

## Nối vào CMS

Xem `docs/KIEM-DUYET-NOI-DUNG.md` mục 6. Tóm tắt: viết một lớp hiện thực
`ContentScanner` gọi `POST /scan`, và **thay resource handler tĩnh `/files/**`
bằng controller kiểm trạng thái** — không có bước sau thì việc kiểm duyệt không
có tác dụng gì.
