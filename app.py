"""
NSFW scanning service for images, video, PDFs and HLS streams.

Wraps the SigLIP2 model strangerguardhf/nsfw_image_detection behind HTTP. Video,
documents and streams are reduced to still frames by media.py and scored frame
by frame; every endpoint answers in its own response, with no job to poll for.

Images, video and streams are named by URL rather than uploaded: the caller has
already stored the file somewhere, so sending the bytes again only moves them
twice. Documents still accept an upload as well, since a PDF is often held
before it is published anywhere. Anything named by URL goes through the
public-address check in media.py, and ALLOW_HOSTS is what lets the scanner read
from the application that sent the link.

Staying synchronous is only honest because the work is bounded. Scoring one
frame measured about 220ms, and media.py never yields more than MAX_FRAMES, so
the slowest case is a predictable handful of seconds rather than an open-ended
wait. Batching frames through the model measured only 1.2x faster, because torch
already spreads a single image across every core, so frames are scored one at a
time and the code stays simple.

There is no authentication. Anything that can reach the port can spend the
model's CPU on it, so the port must not be one the open internet can reach -
bind it to a private network, or put a gateway in front that does the
authenticating.

The frame budget is still enforced, now per client address instead of per
caller. It is a safeguard against one client starving the rest, not a security
control: an address is trivially many, and behind a reverse proxy every caller
arrives wearing the proxy's address and shares a single bucket. X-Forwarded-For
is deliberately not read, because trusting a header any client can set would
hand out a fresh budget on request.
"""
import io
import logging
import os
import shutil
import tempfile
import time
import hashlib
import hmac
import uuid
from collections import defaultdict, deque

import torch
from fastapi import (APIRouter, Depends, FastAPI, File, Form, Header, HTTPException,
                     Request, UploadFile)
from PIL import Image
from transformers import AutoImageProcessor, SiglipForImageClassification

import logs
import media

logs.setup()
log = logging.getLogger("nsfw")

MODEL_PATH = os.getenv("MODEL_PATH", "models/nsfw-image-detection")
HUB_NAME = "strangerguardhf/nsfw_image_detection"
UNSAFE_LABELS = {"Hentai", "Pornography", "Enticing or Sensual"}
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))

CONTEXT_PATH = os.getenv("CONTEXT_PATH", "/media-moderation").rstrip("/")


def _read_keys():
    """
    Doc API_KEYS: "ten:khoa,ten2:sha256:<hex>".

    Khoa duoc luu duoi dang bam, khong phai chuoi goc: ai doc duoc bien moi
    truong hay tep .env cung khong goi duoc. Viet thang chuoi goc van chay -
    no se duoc bam luc nap - nhung co canh bao, vi luc do chuoi goc nam tren
    dia may chu.

    De trong thi khong xac thuc, giu nguyen cach cu.
    """
    keys = {}
    for muc in os.getenv("API_KEYS", "").split(","):
        muc = muc.strip()
        if not muc or ":" not in muc:
            continue
        ten, gia_tri = muc.split(":", 1)
        ten, gia_tri = ten.strip(), gia_tri.strip()
        if gia_tri.lower().startswith("sha256:"):
            keys[gia_tri.split(":", 1)[1].strip().lower()] = ten
        else:
            keys[hashlib.sha256(gia_tri.encode()).hexdigest()] = ten
            log.warning("API_KEYS chua khoa dang chu cho '%s'. Nen thay bang "
                        "sha256:<hex> de chuoi goc khong nam tren may chu.", ten)
    return keys


API_KEYS = _read_keys()


def _require_key(request: Request, x_api_key: str = Header(default="")):
    """
    Doi chieu X-API-Key voi API_KEYS.

    So sanh bang compare_digest chu khong phai ==: phep so sanh thong thuong
    dung som o byte dau tien khac nhau, va thoi gian tra loi do lo dan tung
    byte cua khoa dung.
    """
    if not API_KEYS:
        return "unknown"
    bam = hashlib.sha256(x_api_key.encode()).hexdigest()
    for luu, ten in API_KEYS.items():
        if hmac.compare_digest(bam, luu):
            request.state.caller = ten
            return ten
    raise HTTPException(status_code=401, detail="Missing or unknown X-API-Key")

app = FastAPI(title="NSFW scanner", docs_url=CONTEXT_PATH + "/docs",
              openapi_url=CONTEXT_PATH + "/openapi.json")
# Hai router: moi duong dan quet deu di qua _require_key, con /health thi
# khong - healthcheck trong Dockerfile goi no va khong mang theo khoa nao.
# Tach lam hai de mot endpoint them sau nay khong lo bi quen bao ve.
api = APIRouter(prefix=CONTEXT_PATH, dependencies=[Depends(_require_key)])
public = APIRouter(prefix=CONTEXT_PATH)


@app.middleware("http")
async def _log_request(request: Request, call_next):
    """Mot dong trong request.log cho moi lan goi, kem ma contextId."""
    token = logs.context_id.set(uuid.uuid4().hex[:8])
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        logs.request_log.info(
            "%s %s %s %s %dms",
            request.client.host if request.client else "-",
            request.method, request.url.path, status,
            round((time.perf_counter() - started) * 1000))
        logs.context_id.reset(token)

_processor = None
_model = None
_recent = defaultdict(deque)


def _caller(request):
    """
    Who to bill, and what to write in the log.

    The client address is all that is left to go on. Behind a reverse proxy that
    is the proxy, so every caller shares one budget - see the note at the top of
    this file about why X-Forwarded-For is not read instead.
    """
    ten = getattr(request.state, "caller", None)
    if ten:
        return ten
    return request.client.host if request.client else "unknown"


def _charge(name, units=1):
    """
    Bills a caller in frames, not requests.

    A video costs as much CPU as the frames it yields, so charging it as one
    request would let a caller spend the whole budget many times over by
    wrapping images in a video container. The media endpoints charge once to be
    let in, then again for the frames they actually produced, before any of
    those frames is scored.
    """
    now = time.monotonic()
    window = _recent[name]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) + units > RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Rate limit of " + str(RATE_LIMIT) + " frames per minute exceeded",
        )
    window.extend([now] * units)


def _load_model():
    global _processor, _model
    if _model is not None:
        return
    source = MODEL_PATH if os.path.isdir(MODEL_PATH) else HUB_NAME
    log.info("Loading model from %s", source)
    _processor = AutoImageProcessor.from_pretrained(source)
    _model = SiglipForImageClassification.from_pretrained(source)
    _model.eval()
    log.info("Ready. Labels: %s", _model.config.id2label)


@app.on_event("startup")
def startup():
    if API_KEYS:
        log.info("Xac thuc bang X-API-Key. %d khoa duoc cau hinh. %d khung "
                 "hinh moi phut cho moi ben goi.", len(API_KEYS), RATE_LIMIT)
    else:
        log.warning("No authentication. Anything that can reach this port may "
                    "use it; keep it off the public internet. %d frames per "
                    "minute per client address.", RATE_LIMIT)
    _load_model()


@public.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None}


def _score(image):
    inputs = _processor(images=image, return_tensors="pt")
    with torch.no_grad():
        logits = _model(**inputs).logits
        probs = torch.nn.functional.softmax(logits, dim=1).squeeze().tolist()
    id2label = _model.config.id2label
    scores = {id2label[i]: round(float(probs[i]), 4) for i in range(len(probs))}
    return scores, round(sum(v for k, v in scores.items() if k in UNSAFE_LABELS), 4)


def _open(data):
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Not an image")


def _worst_of(frames):
    """
    Reports the single most explicit frame rather than an average.

    One explicit second inside an hour of harmless footage is exactly what this
    is meant to catch, and an average would bury it.
    """
    worst_scores, worst_nsfw, worst_marker = None, -1.0, None
    for marker, path in frames:
        with Image.open(path) as frame:
            scores, nsfw = _score(frame.convert("RGB"))
        if nsfw > worst_nsfw:
            worst_scores, worst_nsfw, worst_marker = scores, nsfw, marker
    return worst_scores, worst_nsfw, worst_marker


def _reply(kind, scores, nsfw, count, worst, started):
    return {
        "type": kind,
        "label": max(scores, key=scores.get),
        "scores": scores,
        "nsfw_score": nsfw,
        "frames_scanned": count,
        "worst_at": worst,
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _discard(*paths):
    for path in paths:
        if not path:
            continue
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


def _scan_frames(caller, kind, frames, label, started):
    _charge(caller, len(frames))
    scores, nsfw, worst = _worst_of(frames)
    reply = _reply(kind, scores, nsfw, len(frames), worst, started)
    log.info("%s %s %s -> %s nsfw=%.4f over %d frames",
             caller, kind, label, reply["label"], nsfw, len(frames))
    return reply


def _one_source(file, url):
    """
    Requires exactly one of file or url.

    Accepting both and silently preferring one hides a caller's mistake: a
    request meaning to scan a link but carrying a stale upload would come back
    with a confident answer about the wrong thing.
    """
    if bool(url) == bool(file is not None and file.filename):
        raise HTTPException(
            status_code=400, detail="Send exactly one of file or url"
        )


async def _bytes_from(file, url):
    if url:
        try:
            return media.fetch_bytes(url), url
        except media.MediaError as e:
            raise HTTPException(status_code=400, detail=str(e))
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return data, file.filename


@api.post("/scan")
async def scan_image(request: Request, url: str = Form(...)):
    caller = _caller(request)
    _charge(caller)
    _load_model()

    try:
        data = media.fetch_bytes(url)
    except media.MediaError as e:
        raise HTTPException(status_code=400, detail=str(e))

    started = time.perf_counter()
    scores, nsfw = _score(_open(data))
    reply = _reply("image", scores, nsfw, 1, None, started)
    log.info("%s image %s -> %s nsfw=%.4f", caller, url, reply["label"], nsfw)
    return reply


@api.post("/scan/video")
async def scan_video(request: Request, url: str = Form(...)):
    caller = _caller(request)
    _charge(caller)
    _load_model()

    handle, source = tempfile.mkstemp(suffix=".video")
    os.close(handle)
    folder = None
    try:
        started = time.perf_counter()
        try:
            media.fetch_to_file(url, source)
            frames = media.video_frames(source)
        except media.MediaError as e:
            raise HTTPException(status_code=400, detail=str(e))
        folder = os.path.dirname(frames[0][1])
        return _scan_frames(caller, "video", frames, url, started)
    finally:
        _discard(source, folder)


@api.post("/scan/document")
async def scan_document(request: Request, file: UploadFile = File(default=None),
                        url: str = Form(default="")):
    caller = _caller(request)
    _one_source(file, url)
    _charge(caller)
    _load_model()

    data, label = await _bytes_from(file, url)
    folder = None
    try:
        started = time.perf_counter()
        try:
            pages = media.pdf_pages(data)
        except media.MediaError as e:
            raise HTTPException(status_code=400, detail=str(e))
        folder = os.path.dirname(pages[0][1])
        return _scan_frames(caller, "document", pages, label, started)
    finally:
        _discard(folder)


@api.post("/scan/hls")
async def scan_hls(request: Request, url: str = Form(...)):
    caller = _caller(request)
    _charge(caller)
    _load_model()

    folder = None
    try:
        started = time.perf_counter()
        try:
            frames = media.hls_frames(url)
        except media.MediaError as e:
            raise HTTPException(status_code=400, detail=str(e))
        folder = os.path.dirname(frames[0][1])
        return _scan_frames(caller, "hls", frames, url, started)
    finally:
        _discard(folder)


app.include_router(public)
app.include_router(api)
