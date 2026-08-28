"""
Quet mot anh bang dich vu dang chay.

    .venv/Scripts/python.exe quet.py duong/dan/anh.jpg
"""
import json
import mimetypes
import os
import sys
import urllib.request

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

duong_dan = sys.argv[1]
dich_vu = os.getenv("SCANNER_URL", "http://127.0.0.1:8000")

if not os.path.isfile(duong_dan):
    print("Khong thay tep:", duong_dan)
    sys.exit(1)

noi_dung = open(duong_dan, "rb").read()
kieu = mimetypes.guess_type(duong_dan)[0] or "application/octet-stream"
ten = os.path.basename(duong_dan)

ranh = "----quet"
than = (
    f"--{ranh}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{ten}"\r\n'
    f"Content-Type: {kieu}\r\n\r\n"
).encode() + noi_dung + f"\r\n--{ranh}--\r\n".encode()

yeu_cau = urllib.request.Request(
    f"{dich_vu}/scan",
    data=than,
    headers={"Content-Type": f"multipart/form-data; boundary={ranh}"},
)

try:
    ket_qua = json.loads(urllib.request.urlopen(yeu_cau, timeout=300).read())
except urllib.error.HTTPError as e:
    print("Bi tu choi:", json.loads(e.read()).get("detail"))
    sys.exit(1)

print(f"\n{ten}  ({len(noi_dung)/1024:.0f} KB, {kieu})\n")
for nhan, diem in sorted(ket_qua["scores"].items(), key=lambda x: -x[1]):
    thanh = "#" * round(diem * 40)
    print(f"  {nhan:<22} {diem:6.4f}  {thanh}")
print(f"\n  ket luan   : {ket_qua['label']}")
print(f"  nsfw_score : {ket_qua['nsfw_score']}")
print(f"  thoi gian  : {ket_qua['took_ms']} ms\n")
