"""
Kiem viec kiem tra dau vao cua dich vu.

    .venv/Scripts/python.exe kiem-dau-vao.py

Chay thang tren ung dung, khong can dung server rieng.

KHONG CON TRAN KICH THUOC VA TRAN DIEM ANH - da bo theo yeu cau, xem commit
"feat: bound what the scanner will accept" neu muon lay lai. Nhung gi con lai
chi la: tep rong bi tu choi, va thu khong doc ra anh bi tu choi.
"""
import io
import warnings

from fastapi.testclient import TestClient
from PIL import Image

warnings.filterwarnings("ignore")

import app as ung_dung

client = TestClient(ung_dung.app)


def anh_jpeg(rong, cao):
    dem = io.BytesIO()
    Image.new("RGB", (rong, cao), (120, 140, 160)).save(dem, format="JPEG")
    return dem.getvalue()


def goi(ten, noi_dung, kieu="image/jpeg"):
    return client.post("/scan", files={"file": (ten, noi_dung, kieu)})


truot = 0


def mong_doi(mo_ta, ket_qua, ma):
    global truot
    dat = ket_qua.status_code == ma
    truot += 0 if dat else 1
    chi_tiet = ""
    if ket_qua.status_code != 200:
        chi_tiet = "  -> " + str(ket_qua.json().get("detail", ""))[:60]
    print(f"  [{'OK ' if dat else 'TRUOT'}] {mo_ta:<44} {ket_qua.status_code} (mong {ma}){chi_tiet}")


print()
mong_doi("anh thuong 512x512", goi("ok.jpg", anh_jpeg(512, 512)), 200)
mong_doi("tep rong", goi("rong.jpg", b""), 400)
mong_doi("khong phai anh", goi("x.jpg", b"day khong phai anh" * 100), 400)
mong_doi("anh 4000x3000", goi("to.jpg", anh_jpeg(4000, 3000)), 200)

print("\n%s\n" % ("TAT CA DAT" if truot == 0 else "CO %d TRUONG HOP TRUOT" % truot))
raise SystemExit(1 if truot else 0)
