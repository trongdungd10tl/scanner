"""
NSFW scanning service for images.

Wraps the SigLIP2 model strangerguardhf/nsfw_image_detection behind HTTP. The
one scanning endpoint answers in its own response, with no job to poll for:
scoring an image measured about 220ms whatever its size.

An image arrives either as a link or as the file itself, exactly one of the two.
A link suits an image already published; the file suits an upload that should be
judged before it is stored anywhere, so a rejected image never reaches the CDN.
A link goes through the public-address check in media.py, and ALLOW_HOSTS and
FETCH_VIA are what let the scanner read from hosts inside the network.

A second model, NudeNet, looks for exposed body parts and reports the
strongest one as the score "Explicit". The SigLIP labels judge the mood of the
whole picture and get it wrong both ways - a close-up of a face filling the frame
with skin scored Pornography 0.97, while a sex scene scored 0.18 - so they only
decide what a person should look at. Explicit is what callers block on, because
it needs an actual exposed genital, anus or female breast to rise. NudeNet is
trained on photographs; drawn porn still rests on the SigLIP Hentai label.

There is no authentication. Anything that can reach the port can spend the
model's CPU on it, so the port must not be one the open internet can reach -
bind it to a private network, or put a gateway in front that does the
authenticating.

The image budget is still enforced, now per client address instead of per
caller. It is a safeguard against one client starving the rest, not a security
control: an address is trivially many, and behind a reverse proxy every caller
arrives wearing the proxy's address and shares a single bucket. X-Forwarded-For
is deliberately not read, because trusting a header any client can set would
hand out a fresh budget on request.
"""
import base64
import io
import logging
import os
import time
import uuid
from collections import defaultdict, deque

import numpy as np
import torch
from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
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

# NudeNet classes that count as explicit. Exposed buttocks, bellies and armpits
# are left out on purpose: swimwear has them.
EXPLICIT_CLASSES = {
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
}
EXPLICIT_LABEL = "Explicit"
# NudeNet often calls a bare male chest FEMALE_BREAST_EXPOSED at around 0.6
# (real female nudity scored 0.71 and up). Set to 0.75 after a second shirtless
# man scored 0.639; real nudity from 0.6 to 0.75 now goes to review only.
BREAST_MIN_SCORE = float(os.getenv("BREAST_MIN_SCORE", "0.75"))
# NudeNet is ignored when SigLIP rates the picture Normal at least this high.
# Real nudity scored Normal 0.17 or less; NudeNet's false hits on a close-up
# face and a shirtless man scored 0.93 and 0.99.
NORMAL_VETO = float(os.getenv("NORMAL_VETO", "0.5"))

# The verdict the callers act on ("action": block / review / pass):
#   block  - genitals or anus >= PART_SURE, whatever SigLIP says;
#          - genitals or anus >= PART_BLOCK, unless vetoed by Normal (above);
#          - a female breast >= BREAST_MIN_SCORE with Enticing or Sensual >=
#            BREAST_SENSUAL, unless vetoed; without that Sensual it is only
#            reviewed (NudeNet takes a bare male chest for a breast);
#          - or a grey part (genitals, anus or breast >= PART_GREY) and
#            Pornography > PORN_BLOCK: two models agreeing;
#   review - Pornography >= PORN_REVIEW, whatever NudeNet saw, or Hentai >
#            HENTAI_REVIEW. SigLIP alone never blocks: it called two
#            ordinary pictures pornography;
#   pass   - the rest.
GENITAL_CLASSES = {"FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED"}
BREAST_CLASS = "FEMALE_BREAST_EXPOSED"
PART_SURE = float(os.getenv("PART_SURE", "0.8"))
PART_BLOCK = float(os.getenv("PART_BLOCK", "0.6"))
PART_GREY = float(os.getenv("PART_GREY", "0.5"))
PORN_BLOCK = float(os.getenv("PORN_BLOCK", "0.95"))
PORN_REVIEW = float(os.getenv("PORN_REVIEW", "0.7"))
HENTAI_REVIEW = float(os.getenv("HENTAI_REVIEW", "0.8"))
BREAST_SENSUAL = float(os.getenv("BREAST_SENSUAL", "0.4"))

CONTEXT_PATH = os.getenv("CONTEXT_PATH", "/media-moderation").rstrip("/")


app = FastAPI(title="NSFW scanner", docs_url=CONTEXT_PATH + "/docs",
              openapi_url=CONTEXT_PATH + "/openapi.json")
router = APIRouter(prefix=CONTEXT_PATH)


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
_detector = None
_recent = defaultdict(deque)


def _caller(request):
    """
    Who to bill, and what to write in the log.

    The client address is all that is left to go on. Behind a reverse proxy that
    is the proxy, so every caller shares one budget - see the note at the top of
    this file about why X-Forwarded-For is not read instead.
    """
    return request.client.host if request.client else "unknown"


def _charge(name):
    """Counts one image against a caller's budget for the last minute."""
    now = time.monotonic()
    window = _recent[name]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Rate limit of " + str(RATE_LIMIT) + " images per minute exceeded",
        )
    window.append(now)


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
    _load_detector()


def _load_detector():
    """
    Loads NudeNet. Its weights ship inside the pip package, so this needs no
    network. If the package is missing - an older image running this file - the
    service keeps answering without the Explicit score instead of failing to
    start: callers then block nothing and only queue images for review, which
    is the safe way to be wrong.
    """
    global _detector
    if _detector is not None:
        return
    try:
        from nudenet import NudeDetector
        _detector = NudeDetector()
        log.info("NudeNet ready. Explicit classes: %s", sorted(EXPLICIT_CLASSES))
    except Exception as e:
        log.error("NudeNet NOT loaded, Explicit score disabled: %s", e)


@app.on_event("startup")
def startup():
    log.warning("No authentication. Anything that can reach this port may "
                "use it; keep it off the public internet. %d images per "
                "minute per client address.", RATE_LIMIT)
    _load_model()


@router.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None,
            "explicit_loaded": _detector is not None}


def _score(image):
    inputs = _processor(images=image, return_tensors="pt")
    with torch.no_grad():
        logits = _model(**inputs).logits
        probs = torch.nn.functional.softmax(logits, dim=1).squeeze().tolist()
    id2label = _model.config.id2label
    scores = {id2label[i]: round(float(probs[i]), 4) for i in range(len(probs))}
    return scores, round(sum(v for k, v in scores.items() if k in UNSAFE_LABELS), 4)


def _explicit(image):
    """
    Highest NudeNet score among the explicit classes, and every detection for
    the log. NudeNet expects OpenCV's BGR channel order.
    """
    if _detector is None:
        return None, [], 0.0, 0.0
    found = _detector.detect(np.ascontiguousarray(np.asarray(image)[:, :, ::-1]))
    hits = [(d["class"], round(float(d["score"]), 4)) for d in found]
    top = max([s for c, s in hits if c in EXPLICIT_CLASSES
               and (c != "FEMALE_BREAST_EXPOSED" or s >= BREAST_MIN_SCORE)] or [0.0])
    genital = max([s for c, s in hits if c in GENITAL_CLASSES] or [0.0])
    breast = max([s for c, s in hits if c == BREAST_CLASS] or [0.0])
    return round(top, 4), hits, genital, breast


def _decide(scores, genital, breast, vetoed):
    """block / review / pass and the reason, by the rule at the top of this file."""
    porn = scores.get("Pornography", 0.0)
    grey = genital >= PART_GREY or breast >= PART_GREY
    if genital >= PART_SURE:
        # SigLIP rated close-ups of a penis Normal 0.89-0.98: a veto there let
        # them through. NudeNet's false hits on faces stayed under 0.7.
        return "block", "explicit"
    if genital >= PART_BLOCK and not vetoed:
        return "block", "explicit"
    breast_only = breast >= BREAST_MIN_SCORE and not vetoed
    if breast_only and scores.get("Enticing or Sensual", 0.0) >= BREAST_SENSUAL:
        # A bare male chest still reaches 0.78 as a female breast; SigLIP rated
        # it Sensual 0.01, real topless women 0.50-0.86.
        return "block", "breast"
    if grey and porn > PORN_BLOCK:
        return "block", "grey+porn"
    if breast_only:
        return "review", "breast"
    if porn >= PORN_REVIEW:
        return "review", "porn"
    if scores.get("Hentai", 0.0) > HENTAI_REVIEW:
        # NudeNet barely sees drawings, so a nude drawing gets no other check
        return "review", "hentai"
    return "pass", ""


def _thumbnail(image, side=512):
    """A JPEG of at most side x side pixels, base64-encoded."""
    copy = image.copy()
    copy.thumbnail((side, side))
    buffer = io.BytesIO()
    copy.save(buffer, format="JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _open(data):
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Not an image")


def _reply(kind, scores, nsfw, count, worst, started):
    return {
        "type": kind,
        "label": max((k for k in scores if k != EXPLICIT_LABEL), key=scores.get),
        "scores": scores,
        "nsfw_score": nsfw,
        "frames_scanned": count,
        "worst_at": worst,
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
    }


@router.post("/scan")
async def scan_image(request: Request, url: str = Form(default=""),
                     file: UploadFile = File(default=None),
                     evidence: bool = Form(default=False)):
    """
    Scores one image, sent as url or as file.

    Exactly one is required. Accepting both and quietly preferring one would
    hide a caller's mistake behind a confident answer about the wrong image.
    """
    has_file = file is not None and bool(file.filename)
    if bool(url) == has_file:
        raise HTTPException(status_code=400, detail="Send exactly one of url or file")
    caller = _caller(request)
    _charge(caller)
    _load_model()

    if url:
        try:
            data = media.fetch_bytes(url)
        except media.MediaError as e:
            raise HTTPException(status_code=400, detail=str(e))
        label = url
    else:
        data = await file.read(media.MAX_DOWNLOAD_BYTES + 1)
        if not data:
            raise HTTPException(status_code=400, detail="Empty file")
        if len(data) > media.MAX_DOWNLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Image is larger than allowed")
        label = "upload:" + file.filename

    started = time.perf_counter()
    image = _open(data)
    scores, nsfw = _score(image)
    explicit, hits, genital, breast = _explicit(image)
    vetoed = scores.get("Normal", 0.0) >= NORMAL_VETO
    if explicit and vetoed:
        # SigLIP is sure the picture is ordinary: a NudeNet hit here has been a
        # face, lips or a hand mistaken for a body part. Kept in the log only.
        log.info("%s explicit %.4f vetoed, Normal=%.4f", caller, explicit, scores["Normal"])
        explicit = 0.0
    if explicit is not None:
        # same meaning as before "action" existed, for callers not yet reading it
        scores[EXPLICIT_LABEL] = explicit
    action, reason = _decide(scores, genital, breast, vetoed)
    reply = _reply("image", scores, nsfw, 1, None, started)
    reply["action"] = action
    reply["reason"] = reason
    reply["parts"] = {"genital": round(genital, 4), "breast": round(breast, 4)}
    if evidence:
        # A caller that sent only a link never held the image, so it has
        # nothing to keep as proof of what was blocked. Hand back the copy
        # already fetched, shrunk to a thumbnail.
        reply["evidence_jpeg"] = _thumbnail(image)
    log.info("%s image %s -> %s nsfw=%.4f explicit=%s action=%s %s parts=%s",
             caller, label, reply["label"], nsfw, explicit, action, reason, hits)
    return reply


app.include_router(router)
