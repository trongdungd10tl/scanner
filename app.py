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
from fastapi import FastAPI, File, HTTPException, UploadFile
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

app = FastAPI(title="NSFW scanner cho Base CMS")

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

    du_lieu = await file.read()
    if not du_lieu:
        raise HTTPException(status_code=400, detail="Tep rong")

    try:
        anh = Image.open(io.BytesIO(du_lieu)).convert("RGB")
    except Exception:
        # Doc khong ra anh thi tu choi, khong doan theo Content-Type.
        raise HTTPException(status_code=400, detail="Khong phai anh")

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
