"""
Kiem cac tran dau vao cua dich vu.

    .venv/Scripts/python.exe kiem-tran.py

Chay thang tren ung dung, khong can dung server rieng.
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


def png_bom(canh):
    """Nho tren duong truyen, khong lo khi giai nen."""
    dem = io.BytesIO()
    Image.new("RGB", (canh, canh), (30, 90, 200)).save(dem, format="PNG")
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
    print(f"  [{'OK ' if dat else 'TRUOT'}] {mo_ta:<46} {ket_qua.status_code} (mong {ma}){chi_tiet}")


print("\nTran kich thuoc : %d MB" % (ung_dung.KICH_THUOC_TOI_DA // 1024 // 1024))
print("Tran diem anh   : %d trieu\n" % (ung_dung.DIEM_ANH_TOI_DA // 1_000_000))

mong_doi("anh thuong 512x512", goi("ok.jpg", anh_jpeg(512, 512)), 200)
mong_doi("tep rong", goi("rong.jpg", b""), 400)
mong_doi("khong phai anh", goi("x.jpg", b"day khong phai anh" * 100), 400)
mong_doi("tep 12MB (vuot tran 10MB)", goi("to.bin", b"\0" * (12 * 1024 * 1024)), 413)

bom = png_bom(9000)
mong_doi(
    "bom giai nen 9000x9000 (%d KB)" % (len(bom) / 1024),
    goi("bom.png", bom, "image/png"),
    413,
)

# Sat tran nhung con duoi: phai VAO duoc, khong duoc chan nham.
sat = anh_jpeg(4000, 3000)   # 12 trieu diem anh
mong_doi("anh 4000x3000 (12 trieu diem anh) van vao", goi("to-that.jpg", sat), 200)

print("\n%s\n" % ("TAT CA DAT" if truot == 0 else "CO %d TRUONG HOP TRUOT" % truot))
raise SystemExit(1 if truot else 0)
