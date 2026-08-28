"""Goi thu dich vu bang mot anh sinh tai cho, de xac nhan no chay."""
import io
import sys
import urllib.request

from PIL import Image

DICH_VU = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

anh = Image.new("RGB", (256, 256), (200, 180, 160))
dem = io.BytesIO()
anh.save(dem, format="JPEG")
noi_dung = dem.getvalue()

ranh = "----kiemthu"
than = (
    f"--{ranh}\r\n"
    'Content-Disposition: form-data; name="file"; filename="test.jpg"\r\n'
    "Content-Type: image/jpeg\r\n\r\n"
).encode() + noi_dung + f"\r\n--{ranh}--\r\n".encode()

yeu_cau = urllib.request.Request(
    f"{DICH_VU}/scan",
    data=than,
    headers={"Content-Type": f"multipart/form-data; boundary={ranh}"},
)
print(urllib.request.urlopen(yeu_cau, timeout=120).read().decode())
