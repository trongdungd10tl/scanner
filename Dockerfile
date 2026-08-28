# Dich vu quet anh nhay cam.
#
# Cong thuc nay lam MOT LAN nhung buoc ma cai bang tay se vap: chon dung ban
# torch CPU, keo theo torchvision, va nho python-multipart cho viec nhan tep.
#
# Trong so mo hinh (354MB) KHONG nam trong image. Gan tu ngoai vao bang volume
# - giong cach MySQL cua du an giu du lieu ngoai image. Nho vay image gon, va
# doi mo hinh khac chi can thay thu muc chu khong phai build lai.
FROM python:3.11-slim

# Khi lam video: bo dau # o dong duoi la ca image co ffmpeg, khong may nao phai
# tu cai. Gio chua dung nen khong keo vao cho nang.
# RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
#     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Torch phai lay tu chi muc CPU. Khong chi ro thi pip keo ban CUDA ~2.5GB, vo
# ich voi may khong co GPU va lam image phinh gap doi.
RUN pip install --no-cache-dir \
        torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu

# Chep requirements truoc code: Docker giu lai lop nay khi chi co code doi, nen
# sua app.py khong phai cai lai toan bo thu vien.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Khong chay bang root. Container bi chiem thi ke tan cong cung chi la mot tai
# khoan thuong.
RUN useradd --create-home --uid 10001 quet && chown -R quet:quet /app
USER quet

ENV MODEL_PATH=/app/models/nsfw-image-detection
EXPOSE 8000

# Nap mo hinh mat vai giay, nen start-period rong tay de Docker khong bao
# unhealthy trong luc no dang khoi dong.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
