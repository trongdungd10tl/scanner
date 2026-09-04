# Cách chạy

## Trên server — một lệnh

Chép thư mục này lên server, ví dụ vào `/root/apps/moderation`, rồi:

```bash
cd /root/apps/moderation
docker compose up -d --build
```

Xong. Dịch vụ nghe ở cổng **8000**, gọi API quét ảnh được ngay.

Không phải tải mô hình trước, không phải tạo thư mục, không phải đặt biến môi
trường, không phải sửa gì trong tệp nào.

### Chép lên bằng cách nào

```bash
git clone <repo> /root/apps && cd /root/apps/moderation
```

Hoặc chép thẳng từ máy của bạn — **nhớ loại hai thư mục này ra**:

```bash
rsync -av --exclude .venv --exclude models       ./moderation/ root@server:/root/apps/moderation/
```

`.venv/` là môi trường ảo của Windows, trên Linux vô dụng; `models/` sẽ được tải
lại trong lúc build. Hai thứ đó cộng lại hơn 3GB, chép lên chỉ tốn thời gian.

### Server cần gì

| | |
|---|---|
| Docker | có `docker compose` (bản v2) |
| Ổ đĩa | ~3 GB cho image |
| RAM | **tối thiểu 2 GB** — mô hình chiếm ~1 GB lúc chạy |
| Mạng | chỉ cần **lúc build**, tải ~400 MB |

Máy 1GB RAM sẽ bị hệ điều hành giết tiến trình lúc nạp mô hình, và biểu hiện là
container cứ khởi động lại liên tục.

Nếu server chỉ có `docker-compose` bản cũ thì thay bằng
`docker-compose up -d --build`.

### Lần đầu mất bao lâu

Build phải tải **~2 GB thư viện torch** và **354 MB trọng số**. Server mạng khoẻ
thì khoảng 5–10 phút. Những lần sau Docker dùng lại lớp đã có nên gần như tức thì.

Nếu đang tải mà đứt mạng, chạy lại đúng câu lệnh đó — phần tải trọng số nối tiếp
được từ chỗ đứt, không phải làm lại từ đầu.

### Mở cổng cho máy khác gọi

Mặc định cổng 8000 mở ra mọi giao diện mạng của server. Tường lửa thường chặn
sẵn, nên nếu gọi từ máy khác không được:

```bash
ufw allow 8000/tcp
```

Dịch vụ **không hỏi người gọi là ai**. Đừng mở cổng này ra Internet. Nếu ứng dụng
gọi nó chạy trên cùng server, đóng hẳn lại bằng cách sửa `docker-compose.yml`:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

## Kiểm xem đã sống chưa

```bash
curl http://localhost:8000/health
```

```json
{"status":"ok","model_loaded":true}
```

Mô hình mất vài giây để nạp. Trong lúc đó Docker báo `starting`, chưa phải lỗi:

```bash
docker compose ps
```

Chờ tới khi cột STATUS ghi **`(healthy)`**.

## Bốn endpoint

Cả bốn đều cần khoá ở header **`X-API-Key`**, và đều **trả kết quả ngay trong
response** — không có việc để chờ, không phải hỏi lại.

Ba endpoint đầu nhận **link**, `/scan/hls` cũng vậy:

```bash
curl -X POST http://localhost:8000/scan          -H "X-API-Key: KHOA" -F "url=https://.../anh.jpg"
curl -X POST http://localhost:8000/scan/video    -H "X-API-Key: KHOA" -F "url=https://.../video.mp4"
curl -X POST http://localhost:8000/scan/document -H "X-API-Key: KHOA" -F "url=https://.../tailieu.pdf"
curl -X POST http://localhost:8000/scan/hls      -H "X-API-Key: KHOA" -F "url=https://.../stream.m3u8"
```

| Endpoint | Nhận |
|---|---|
| `/scan` | **chỉ `url`** |
| `/scan/video` | **chỉ `url`** |
| `/scan/document` | `url` hoặc `file` |
| `/scan/hls` | chỉ `url` |

Gửi `file` vào `/scan` hay `/scan/video` trả **422** — FastAPI báo thiếu trường
`url` bắt buộc.

`/scan/document` vẫn nhận tệp tải lên, vì một PDF thường được giữ trước khi
công bố ở đâu đó nên chưa có link để gửi. Với nó, phải gửi **đúng một** trong
`url` và `file`; cả hai hoặc không có đều trả **400** — nhận cả hai rồi âm thầm
chọn một sẽ che mất lỗi của bên gọi.

### Link nội bộ — `ALLOW_HOSTS`

Chốt chặn SSRF **từ chối mọi địa chỉ nội bộ**, nên link trỏ về chính CMS của bạn
(`localhost:8089`, `192.168.x`) sẽ bị chặn:

```json
{"detail":"Host resolves to a non-public address (127.0.0.1). Add it to ALLOW_HOSTS if the scanner should read from it."}
```

Khai đúng host cần cho phép:

```bash
ALLOW_HOSTS=cms.internal,192.168.1.10 docker compose up -d
```

Chỉ những host được **nêu tên** mới qua; mọi địa chỉ nội bộ khác vẫn bị chặn.
Danh sách càng ngắn thì lỗ càng hẹp — đừng khai cả dải.

### Trần khi tải link

| | Mặc định | Biến |
|---|---|---|
| Kích thước tài nguyên | 512 MB | `MAX_DOWNLOAD_BYTES` |

Trần này cần thiết hơn ở đường tải tệp lên: tệp tải lên tốn băng thông của bên
gọi, còn link thì tốn của scanner — một request ngắn có thể bắt nó kéo về tệp
lớn tuỳ ý.

Dịch vụ cũng **đối chiếu `Content-Length`**. Kết nối đứt giữa chừng để lại tệp
ngắn hơn mà không ném lỗi nào, và với video thì điều đó tệ hơn cả báo lỗi: phần
tải được có thể quét ra sạch, trong khi phần không về mới là phần đáng lo.

Cả bốn trả về **cùng một hình dạng JSON**, nên bên gọi viết một hàm đọc là dùng
được cho mọi loại:

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
  "frames_scanned": 42,
  "worst_at": 128.5,
  "took_ms": 9310.4
}
```

| Trường | Ý nghĩa |
|---|---|
| `type` | `image`, `video`, `document` hay `hls` |
| `nsfw_score` | **con số duy nhất cần đọc** — so với ngưỡng của bạn |
| `scores` | điểm của **khung tệ nhất**, không phải trung bình |
| `frames_scanned` | số khung/trang đã chấm; ảnh luôn là 1 |
| `worst_at` | giây (video, hls) hoặc số trang (tài liệu); ảnh là `null` |

`nsfw_score` lấy theo **khung tệ nhất**, không lấy trung bình. Một giây phản cảm
giữa một tiếng phim vô hại chính là thứ cần bắt, mà lấy trung bình thì nó chìm
nghỉm. `worst_at` cho biết xem lại ở đâu.

Đọc `README.md` trước khi chọn ngưỡng — con số mặc định chưa được hiệu chỉnh
trên dữ liệu thật.

### Mỗi loại lấy bao nhiêu khung

| | Cách lấy | Trần |
|---|---|---|
| Video | rải đều theo độ dài video | `MAX_FRAMES` = 60 |
| HLS | 60 giây đầu của luồng | `MAX_FRAMES` = 60 |
| Tài liệu | mỗi trang một ảnh | `MAX_PAGES` = 40 |

Khoảng lấy mẫu tính từ chính độ dài video, nên **video 30 giây hay 3 tiếng đều
cho ra tối đa 60 khung** — thời gian trả lời vì thế đoán trước được. Chấm một
khung mất ~220ms, nên trường hợp chậm nhất khoảng 13 giây.

Luồng HLS trực tiếp không bao giờ kết thúc, nên bắt buộc phải cắt: `HLS_SECONDS`
mặc định 60.

Đổi được bằng biến môi trường, nhưng nhớ rằng nâng trần là kéo dài thời gian
người gọi phải chờ.

### `/scan/hls` chỉ nhận địa chỉ công cộng

URL trỏ vào `127.0.0.1`, dải nội bộ (`192.168.x`, `10.x`, `172.16-31.x`), địa
chỉ link-local hay scheme không phải `http`/`https` đều bị từ chối **400**.

Không có chốt này thì dịch vụ thành một cái proxy mở: ai gọi được API cũng có
thể bắt server của bạn gửi request tới cổng quản trị của chính nó, tới CSDL của
máy bên cạnh, hay tới endpoint metadata của nhà cung cấp đám mây — từ một địa
chỉ mà những nơi đó tin tưởng hơn hẳn người ngoài.

Thiếu khoá trả **401**, sai khoá trả **403**, vượt hạn mức trả **429**.

`/health` không cần khoá — healthcheck của Docker gọi nó, và nó không tiết lộ gì
ngoài việc mô hình đã nạp xong hay chưa.

Có sẵn giao diện thử trong trình duyệt: <http://localhost:8000/docs>

## Khoá API

Mỗi bên gọi **một khoá riêng**. Moderation chỉ giữ bản băm SHA-256 kèm tên:

```yaml
environment:
  API_KEYS: "cms:a160b94c...,partnerA:3f81e2d5...,partnerB:c07ab914..."
```

Khoá thật không nằm trong repo, không nằm trong image. Người đọc được toàn bộ mã
nguồn cũng chỉ thấy bản băm, mà gửi bản băm lên thì bị từ chối.

### Cấp khoá cho một bên

```bash
python issue_key.py partnerA
```

```
Give this to the caller. It is stored nowhere and cannot be recovered:
  scanner_partnerA_9hRmI1lTuEfYDn9-DI2z8CjTGO_6QJKL

Add this to API_KEYS in docker-compose.yml, comma separated:
  partnerA:fe42f870754758db48db53dd23b42dda3d5087491434294d808a52d08800174d
```

Gửi dòng khoá cho đối tác **qua kênh riêng**, dán dòng thứ hai vào `API_KEYS`,
rồi `docker compose up -d`. Không phải build lại image.

Khoá chỉ hiện đúng một lần. Mất thì cấp khoá mới, không lấy lại được.

### Thu hồi một bên

Xoá đúng mục của bên đó khỏi `API_KEYS` rồi `docker compose up -d`. **Các bên
còn lại không bị ảnh hưởng** — đó là lý do mỗi bên một khoá thay vì dùng chung.

### Hạn mức

```yaml
RATE_LIMIT_PER_MINUTE: 300
```

Áp dụng **riêng cho từng khoá**, không phải tổng. Vượt trả **429**.

Đếm theo **khung, không theo lời gọi**. Một video tốn CPU bằng số khung nó sinh
ra, nên nếu tính mỗi video là một lượt thì gói 60 ảnh vào một tệp video là tiêu
60 lần ngân sách mà chỉ mất một lượt. Các endpoint media trừ một đơn vị để vào
cửa, rồi trừ tiếp đúng số khung thật — **trước khi** chấm khung nào.

Đây không phải thủ tục hình thức: mỗi khung tốn ~220ms CPU, nên một bên gửi đều
đặn là làm nghẽn tất cả các bên khác mà không cần cố ý phá hoại. Khoá đúng cũng
không cứu được chuyện đó — chỉ hạn mức mới cứu được.

Mặc định `300` khung/phút, tức khoảng năm video đầy khung mỗi phút cho mỗi bên.

Hạn mức đếm trong bộ nhớ của tiến trình. Chạy nhiều worker uvicorn thì mỗi worker
đếm riêng, tức hạn mức thật bằng số worker nhân với con số này.

Dịch vụ **không khởi động** nếu `API_KEYS` rỗng hoặc sai định dạng. Chạy hở không
khoá là trạng thái nguy hiểm mà lại trông y hệt lúc chạy đúng, nên thà nó chết
ngay lúc bật.

## Chạy sau HTTPS

Khoá đi qua mạng ở dạng rõ. Trong mạng nội bộ thì chấp nhận được; ra Internet thì
**bắt buộc phải có TLS**, nếu không ai bắt được gói tin là có khoá.

Cách gọn nhất là đặt Caddy phía trước — nó tự xin và tự gia hạn chứng chỉ:

```yaml
services:
  scanner:
    build: .
    restart: unless-stopped
    expose:
      - "8000"
    environment:
      API_KEYS: ${API_KEYS}

  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy-data:/data

volumes:
  caddy-data:
```

`Caddyfile`:

```
scan.tenmien-cua-ban.com {
    reverse_proxy scanner:8000
}
```

Chú ý `expose` thay cho `ports` ở dịch vụ scanner: cổng 8000 chỉ mở trong mạng
nội bộ của Docker, không ra ngoài. Mọi lời gọi buộc phải đi qua Caddy, tức đi
qua HTTPS.

## Các lệnh hay dùng

| | |
|---|---|
| `docker compose logs -f` | xem log |
| `docker compose ps` | trạng thái, có healthy chưa |
| `docker compose down` | tắt |
| `docker compose up -d --build` | build lại sau khi sửa `app.py` |

## Đổi cổng

```yaml
ports:
  - "9000:8000"
```

Chỉ đổi số bên trái. Số bên phải là cổng bên trong container, giữ nguyên.

## Chỉ cho máy này gọi

Mặc định cổng 8000 mở ra mọi giao diện mạng, tức ai chung mạng cũng gọi được, và
dịch vụ **không hỏi người gọi là ai**. Muốn chỉ máy chủ gọi được:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

## Khi đang sửa code

Chạy bằng môi trường ảo thì không phải build lại image sau mỗi lần sửa.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-media.txt
bash download_model.sh
.venv/Scripts/python.exe -m uvicorn app:app --port 8000
```

Trên Linux/macOS thay `.venv/Scripts/python.exe` bằng `.venv/bin/python`.

**Đừng chạy cả hai cùng lúc.** Cả venv và container đều giữ cổng 8000 được mà
không báo lỗi — request tới `localhost` sẽ vào bản venv vì nó khớp cụ thể hơn.
Sửa code rồi tưởng đang thử container mà thật ra đang thử bản cũ.

## Công cụ kèm theo

| Tệp | Dùng để |
|---|---|
| `scan.py` | quét một ảnh, in biểu đồ điểm |
| `smoke_test.py` | xác nhận dịch vụ còn sống |
| `test_input.py` | kiểm cách xử lý đầu vào, chạy thẳng không cần server |
| `download_model.sh` | tải trọng số về `./models` |
| `download_model_hf.py` | cách tải thay thế, chậm hơn |

| `issue_key.py` | cấp khoá cho một bên gọi |

Các công cụ đọc khoá từ biến môi trường `API_KEY`:

```bash
API_KEY=khoa_cua_ban .venv/Scripts/python.exe scan.py duong/dan/anh.jpg
API_KEY=khoa_cua_ban SCANNER_URL=http://may-khac:8000 .venv/Scripts/python.exe scan.py anh.jpg
```

## Khi có trục trặc

**Build báo lỗi tải trọng số.** Chạy lại `docker compose up -d --build` — script
tải nối tiếp được từ chỗ đứt, không phải tải lại từ đầu.

**Container cứ khởi động lại.** `docker compose logs` để xem lý do. Thường là
thiếu RAM: mô hình cần khoảng 1GB lúc chạy.

**Cổng 8000 đã bị chiếm.** Đổi cổng như mục trên, hoặc dừng thứ đang giữ nó.

**`unhealthy` sau khi vừa bật.** Chờ thêm. Healthcheck cho 90 giây để nạp mô
hình; máy chậm có thể cần lâu hơn.

**Container thoát ngay, log ghi `API_KEYS is empty`.** Chưa cấu hình khoá nào.
Xem mục "Khoá API".

**Gọi API trả 401, 403 hay 429.** 401 là quên header `X-API-Key`, 403 là khoá
sai, 429 là vượt hạn mức của khoá đó.
