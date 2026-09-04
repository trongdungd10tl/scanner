"""
Dich vu quet anh nhay cam cho Base CMS.

Repo goc (PRITHIVSAKTHIUR/nsfw-image-detection) chi co mot demo Gradio, khong
goi tu ung dung khac duoc. File nay boc cung mo hinh do thanh mot endpoint HTTP.

Chay:
    .venv/Scripts/python.exe -m uvicorn app:app --port 8000

Goi:
    curl -X POST http://localhost:8000/scan -F "file=@anh.jpg"
"""
import io
import logging
import os
import time

import torch
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from transformers import AutoImageProcessor, SiglipForImageClassification

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("nsfw")

# Uu tien thu muc da tai san; khong co thi keo tu HuggingFace.
DUONG_DAN_MO_HINH = os.getenv("MODEL_PATH", "models/nsfw-image-detection")
TEN_TREN_HUB = "strangerguardhf/nsfw_image_detection"

# Nhung nhan bi coi la khong an toan. "Anime Picture" KHONG nam trong day: no
# chi mo ta phong cach ve, khong noi gi ve muc do nhay cam.
NHAN_KHONG_AN_TOAN = {"Hentai", "Pornography", "Enticing or Sensual"}

# HAI TRAN KHAC NHAU, THIEU CAI NAO CUNG HO.
#
# Tran kich thuoc chan tep 2GB lam day dia va het RAM.
#
# Tran diem anh chan thu ma tran kich thuoc bo tay: mot PNG 252KB khai
# 9000x9000 do duoc la 231MB RAM khi giai nen - khuech dai ~940 lan. La chan san
# co cua Pillow KHONG bat duoc, vi 81 trieu diem anh van nam duoi tran mac dinh
# 89.478.485 cua no.
#
# 24 trieu diem anh la 6000x4000, thua cho moi may anh that. De cao hon vo ich:
# mo hinh co anh ve 256x256 roi moi cham, nen anh cang to cang chi ton RAM chu
# khong cho ket qua tot hon.
KICH_THUOC_TOI_DA = int(os.getenv("MAX_FILE_BYTES", 10 * 1024 * 1024))
DIEM_ANH_TOI_DA = int(os.getenv("MAX_PIXELS", 24_000_000))
KHOI_DOC = 1024 * 1024

app = FastAPI(title="NSFW scanner cho Base CMS")


@app.middleware("http")
async def chan_som_theo_content_length(request: Request, call_next):
    """
    Tu choi tep qua lon TRUOC khi Starlette doc than request.

    Can tang nay vi luc ham scan() chay thi da muon: Starlette doc xong than
    request va do phan vuot 1MB ra tep tam tren dia roi. Da do: middleware nhin
    thay content-length truoc khi dieu do xay ra.

    Khong thay the duoc viec dem trong scan(): content-length co the thieu (gui
    kieu chunked) hoac khai lao. Day chi la cua chan re tien, dem moi la that.
    """
    co = request.headers.get("content-length")
    if co and co.isdigit() and int(co) > KICH_THUOC_TOI_DA + KHOI_DOC:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Tep vuot qua {KICH_THUOC_TOI_DA // 1024 // 1024}MB"},
        )
    return await call_next(request)


async def _doc_co_tran(file: UploadFile) -> bytes:
    """Doc theo tung khoi, dung ngay khi vuot tran thay vi nuot het roi moi do."""
    cac_khoi = []
    tong = 0
    while True:
        mieng = await file.read(KHOI_DOC)
        if not mieng:
            break
        tong += len(mieng)
        if tong > KICH_THUOC_TOI_DA:
            raise HTTPException(
                status_code=413,
                detail=f"Tep vuot qua {KICH_THUOC_TOI_DA // 1024 // 1024}MB",
            )
        cac_khoi.append(mieng)
    return b"".join(cac_khoi)

_processor = None
_model = None


def _nap_mo_hinh():
    """Nap mot lan, giu trong bo nho. Nap moi lan goi se mat vai giay/anh."""
    global _processor, _model
    if _model is not None:
        return

    nguon = DUONG_DAN_MO_HINH if os.path.isdir(DUONG_DAN_MO_HINH) else TEN_TREN_HUB
    log.info("Nap mo hinh tu %s", nguon)
    _processor = AutoImageProcessor.from_pretrained(nguon)
    _model = SiglipForImageClassification.from_pretrained(nguon)
    _model.eval()
    log.info("San sang. Cac lop: %s", _model.config.id2label)


@app.on_event("startup")
def khoi_dong():
    _nap_mo_hinh()


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None}


@app.post("/scan")
async def scan(file: UploadFile = File(...)):
    """
    Cham diem mot anh.

    Tra ve diem tung lop, cong them mot con so gop `nsfw_score` de ben goi chi
    viec so voi nguong cua minh. Diem gop la TONG cua ba lop khong an toan chu
    khong phai lop cao nhat: mot anh 0.4 Pornography + 0.4 Hentai dang ngo hon
    nhieu so voi 0.4 Pornography mot minh.
    """
    _nap_mo_hinh()

    du_lieu = await _doc_co_tran(file)
    if not du_lieu:
        raise HTTPException(status_code=400, detail="Tep rong")

    try:
        anh = Image.open(io.BytesIO(du_lieu))
    except Exception:
        # Doc khong ra anh thi tu choi, khong doan theo Content-Type.
        raise HTTPException(status_code=400, detail="Khong phai anh")

    # Kiem kich thuoc GIUA open() va convert(). Image.open() chi doc phan dau
    # tep de biet kich thuoc, chua cap phat diem anh nao - do duoc 0.10ms so voi
    # 202.88ms cua convert(), tuc re hon gan 2000 lan. Sau convert() moi kiem
    # thi RAM da bi ngon mat roi, kiem cung vo nghia.
    rong, cao = anh.size
    if rong * cao > DIEM_ANH_TOI_DA:
        raise HTTPException(
            status_code=413,
            detail=f"Anh {rong}x{cao} vuot qua {DIEM_ANH_TOI_DA // 1_000_000} trieu diem anh",
        )

    try:
        anh = anh.convert("RGB")
    except Exception:
        # open() chi doc phan dau nen tep cut duoi thi den day moi lo ra.
        raise HTTPException(status_code=400, detail="Khong doc duoc noi dung anh")

    bat_dau = time.perf_counter()
    dau_vao = _processor(images=anh, return_tensors="pt")
    with torch.no_grad():
        logits = _model(**dau_vao).logits
        xac_suat = torch.nn.functional.softmax(logits, dim=1).squeeze().tolist()

    id2label = _model.config.id2label
    diem = {id2label[i]: round(float(xac_suat[i]), 4) for i in range(len(xac_suat))}
    nhan_cao_nhat = max(diem, key=diem.get)

    return {
        "label": nhan_cao_nhat,
        "scores": diem,
        "nsfw_score": round(sum(v for k, v in diem.items() if k in NHAN_KHONG_AN_TOAN), 4),
        "took_ms": round((time.perf_counter() - bat_dau) * 1000, 1),
    }
