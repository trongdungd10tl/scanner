# Cách chạy

## Trên server — mang sẵn image, không build

`scanner-image.tar.gz` trong thư mục này là image **đã build sẵn**, gồm cả trọng
số mô hình. Chép cả thư mục lên rồi:

```bash
cd /root/apps/moderation
gunzip -c scanner-image.tar.gz | docker load
docker compose up -d
```

Không có `--build`. Server **không cần mạng**, không tải lại gì cả.

Image mang tên cố định `moderation-scanner:latest` (ghim bằng khoá `image:` trong
`docker-compose.yml`), nên đổi tên thư mục cũng không sao.

### Hoặc để server tự build

```bash
cd /root/apps/moderation
docker compose up -d --build
```

Cách này server phải có mạng và tải lại ~400 MB: base image, pip, và
trọng số mô hình. Chậm hơn nhiều, chỉ dùng khi không tiện chép tệp image lên.

### Chép lên bằng cách nào

```bash
rsync -av --exclude .venv --exclude models       ./moderation/ root@server:/root/apps/moderation/
```

**Loại hai thư mục đó ra.** `.venv/` là môi trường ảo của Windows, trên Linux vô
dụng. `models/` không cần: trọng số đã nằm sẵn trong `scanner-image.tar.gz`, và
lúc build thì `.dockerignore` cũng bỏ qua nó.

Dịch vụ nghe ở cổng **8508** dưới đường dẫn `/media-moderation`.

### Server cần gì

| | |
|---|---|
| Docker | có `docker compose` (bản v2) |
| Ổ đĩa | ~3 GB cho image |
| RAM | **tối thiểu 2 GB** — mô hình chiếm ~1 GB lúc chạy |
| Mạng | **không cần** nếu nạp từ `scanner-image.tar.gz`; chỉ cần khi tự build (~400 MB) |

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

Mặc định cổng 8508 mở ra mọi giao diện mạng của server. Tường lửa thường chặn
sẵn, nên nếu gọi từ máy khác không được:

```bash
ufw allow 8508/tcp
```

Dịch vụ **không hỏi người gọi là ai**. Đừng mở cổng này ra Internet. Nếu ứng dụng
gọi nó chạy trên cùng server, đóng hẳn lại bằng cách sửa `docker-compose.yml`:

```yaml
ports:
  - "127.0.0.1:8508:8508"
```

## Kiểm xem đã sống chưa

```bash
curl http://localhost:8508/health
```

```json
{"status":"ok","model_loaded":true}
```

Mô hình mất vài giây để nạp. Trong lúc đó Docker báo `starting`, chưa phải lỗi:

```bash
docker compose ps
```

Chờ tới khi cột STATUS ghi **`(healthy)`**.

## Endpoint quét ảnh

**Trả kết quả ngay trong response** — không có việc để chờ, không phải hỏi lại.
Chỉ nhận **link**:

```bash
curl -X POST http://localhost:8508/media-moderation/scan -F "url=https://.../anh.jpg"
```

Gửi `file` thay cho `url` trả **422** — FastAPI báo thiếu trường `url` bắt buộc.

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

Dịch vụ cũng **đối chiếu `Content-Length`**: kết nối đứt giữa chừng để lại ảnh
thiếu mà không ném lỗi nào, nên tải thiếu byte là trả **400** thay vì chấm một
ảnh cụt.

Kết quả trả về:

```json
{
  "type": "image",
  "label": "Normal",
  "scores": {
    "Anime Picture": 0.0121,
    "Hentai": 0.0043,
    "Normal": 0.9612,
    "Pornography": 0.0089,
    "Enticing or Sensual": 0.0135
  },
  "nsfw_score": 0.0267,
  "frames_scanned": 1,
  "worst_at": null,
  "took_ms": 214.3
}
```

| Trường | Ý nghĩa |
|---|---|
| `nsfw_score` | **con số duy nhất cần đọc** — so với ngưỡng của bạn |
| `scores` | điểm của từng nhãn |
| `frames_scanned`, `worst_at` | luôn là `1` và `null`; giữ lại để bên gọi cũ không vỡ |

Đọc `README.md` trước khi chọn ngưỡng — con số mặc định chưa được hiệu chỉnh
trên dữ liệu thật.

### Chỉ nhận địa chỉ công cộng

URL trỏ vào `127.0.0.1`, dải nội bộ (`192.168.x`, `10.x`, `172.16-31.x`), địa
chỉ link-local hay scheme không phải `http`/`https` đều bị từ chối **400**, trừ
host đã khai trong `ALLOW_HOSTS`.

Không có chốt này thì dịch vụ thành một cái proxy mở: ai gọi được API cũng có
thể bắt server của bạn gửi request tới cổng quản trị của chính nó, tới CSDL của
máy bên cạnh, hay tới endpoint metadata của nhà cung cấp đám mây — từ một địa
chỉ mà những nơi đó tin tưởng hơn hẳn người ngoài.

Vượt hạn mức trả **429**.

`/media-moderation/health` cho biết mô hình đã nạp xong hay chưa — healthcheck của Docker gọi nó.

Có sẵn giao diện thử trong trình duyệt: <http://localhost:8508/media-moderation/docs>

## Không có xác thực

Dịch vụ **không kiểm tra gì cả**: thứ gì gọi được tới cổng 8508 thì dùng được nó.
Cổng đó do đó **không được để Internet chạm tới**. Hai cách thường dùng:

- **Chỉ mở trong mạng nội bộ.** Trong `docker-compose.yml`, đổi `"8508:8508"`
  thành `"127.0.0.1:8508:8508"` để chỉ chính máy đó gọi được, hoặc gắn container
  vào mạng Docker chung với bên gọi và **bỏ hẳn `ports`** — lúc ấy chỉ các
  container cùng mạng thấy nó.
- **Đặt một cổng vào phía trước** (Caddy, nginx, API gateway) và cho nó lo phần
  xác thực. Xem mục "Chạy sau HTTPS" bên dưới.

Nếu sau này cần khoá trở lại thì đó là việc của cổng vào, đừng dựng lại trong
dịch vụ: một chỗ kiểm tra dễ rà soát hơn hai chỗ.

### Hạn mức

```yaml
RATE_LIMIT_PER_MINUTE: 300
```

Áp dụng **riêng cho từng địa chỉ máy gọi**, không phải tổng. Vượt trả **429**.

Đếm theo **số ảnh**. Đây không phải thủ tục hình thức: mỗi ảnh tốn ~220ms CPU, nên một bên gửi đều
đặn là làm nghẽn tất cả các bên khác mà không cần cố ý phá hoại.

Mặc định `300` ảnh/phút cho mỗi địa chỉ.

**Đây không phải biện pháp bảo mật.** Địa chỉ thì đổi bao nhiêu cũng được, và khi
dịch vụ đứng sau reverse proxy thì mọi bên gọi đều mang địa chỉ của proxy nên
dùng chung một ngân sách. Header `X-Forwarded-For` cố tình không được đọc: tin
vào một header mà bên gọi tự đặt được thì chẳng khác gì phát ngân sách mới theo
yêu cầu.

Hạn mức đếm trong bộ nhớ của tiến trình. Chạy nhiều worker uvicorn thì mỗi worker
đếm riêng, tức hạn mức thật bằng số worker nhân với con số này.

## Chạy sau HTTPS

Link ảnh và kết quả quét đi qua mạng ở dạng rõ. Trong mạng nội bộ thì chấp nhận
được; ra Internet thì **bắt buộc phải có TLS** — và cũng chính chỗ này là nơi đặt
phần xác thực, vì bản thân dịch vụ không có.

Cách gọn nhất là đặt Caddy phía trước — nó tự xin và tự gia hạn chứng chỉ:

```yaml
services:
  scanner:
    build: .
    restart: unless-stopped
    expose:
      - "8508"
    environment:

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
    reverse_proxy scanner:8508
}
```

Chú ý `expose` thay cho `ports` ở dịch vụ scanner: cổng 8508 chỉ mở trong mạng
nội bộ của Docker, không ra ngoài. Mọi lời gọi buộc phải đi qua Caddy, tức đi
qua HTTPS.

## Log

Ghi ra thư mục `logs/` cạnh `docker-compose.yml`, cùng khuôn với `log4j2.xml` của
gamefi:

| Tệp | Chứa gì | Xoay khi | Giữ |
|---|---|---|---|
| `main.log` | INFO trở lên | 1024 MB hoặc sang ngày | 7 ngày |
| `error.log` | chỉ ERROR | 300 MB hoặc sang ngày | 3 ngày |
| `request.log` | một dòng mỗi lần gọi API | 1024 MB hoặc sang ngày | 3 ngày |

Bản xoay được nén gz và đặt tên `main.2026-09-15-1.log.gz`.

```bash
tail -f logs/main.log
tail -f logs/request.log
zcat logs/main.2026-09-15-1.log.gz | less
```

Mỗi dòng mang một `contextId` — mã của request sinh ra nó, tương đương MDC bên
Java. Nhiều request chạy song song thì log của chúng vẫn tách ra được:

```
2026-09-15 13:51:36 [MainThread] [cd95b636] INFO  request._log_request:77 - 10.0.0.9 POST /media-moderation/scan 200 412ms
```

**Trên máy chủ phải chown trước.** Container chạy bằng uid 10001, còn thư mục
bind mount thì thuộc về root:

```bash
mkdir -p logs && chown 10001:10001 logs
```

Không làm bước này thì container không ghi được và thoát ngay lúc khởi động.

Đổi chỗ ghi bằng biến `LOG_DIR`.

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
  - "9000:8508"
```

Chỉ đổi số bên trái. Số bên phải là cổng bên trong container, giữ nguyên.

## Chỉ cho máy này gọi

Mặc định cổng 8508 mở ra mọi giao diện mạng, tức ai chung mạng cũng gọi được, và
dịch vụ **không hỏi người gọi là ai**. Muốn chỉ máy chủ gọi được:

```yaml
ports:
  - "127.0.0.1:8508:8508"
```

## Khi đang sửa code

Chạy bằng môi trường ảo thì không phải build lại image sau mỗi lần sửa.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/Scripts/python.exe -m pip install -r requirements.txt
bash download_model.sh
.venv/Scripts/python.exe -m uvicorn app:app --port 8508
```

Trên Linux/macOS thay `.venv/Scripts/python.exe` bằng `.venv/bin/python`.

**Đừng chạy cả hai cùng lúc.** Cả venv và container đều giữ cổng 8508 được mà
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

```bash
.venv/Scripts/python.exe scan.py duong/dan/anh.jpg
SCANNER_URL=http://may-khac:8508 .venv/Scripts/python.exe scan.py anh.jpg
```

## Khi có trục trặc

**Build báo lỗi tải trọng số.** Chạy lại `docker compose up -d --build` — script
tải nối tiếp được từ chỗ đứt, không phải tải lại từ đầu.

**Container cứ khởi động lại.** `docker compose logs` để xem lý do. Thường là
thiếu RAM: mô hình cần khoảng 1GB lúc chạy.

**Cổng 8508 đã bị chiếm.** Đổi cổng như mục trên, hoặc dừng thứ đang giữ nó.

**`unhealthy` sau khi vừa bật.** Chờ thêm. Healthcheck cho 90 giây để nạp mô
hình; máy chậm có thể cần lâu hơn.

**Gọi API trả 429.** Vượt hạn mức ảnh/phút của địa chỉ đang gọi. Chờ một phút,
hoặc nâng `RATE_LIMIT_PER_MINUTE`.

**Gọi API trả 400 kèm "non-public address".** Link trỏ tới một địa chỉ nội bộ.
Đó là chốt chặn proxy mở; nếu link đúng là của hệ thống nhà thì thêm host đó vào
`ALLOW_HOSTS`.
