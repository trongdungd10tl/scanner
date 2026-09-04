"""
Checks authentication, rate limiting, the SSRF guard and input handling across
all four endpoints. Runs against the application directly, so no server needs
to be started.

    python test_input.py

Uses throwaway keys of its own, so it neither needs nor reveals a real one.

Cases needing ffmpeg or pymupdf are skipped when those are missing, which is
normal on a development machine; the Docker image has both, so nothing is
skipped there. Skips are reported, never counted as passes.
"""
import hashlib
import io
import os
import shutil
import warnings

warnings.filterwarnings("ignore")

CMS_KEY = "test-key-for-cms"
PARTNER_KEY = "test-key-for-partner"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


os.environ["API_KEYS"] = "cms:%s,partnerA:%s" % (digest(CMS_KEY), digest(PARTNER_KEY))
os.environ["RATE_LIMIT_PER_MINUTE"] = "6"

from fastapi.testclient import TestClient
from PIL import Image

import app as service

client = TestClient(service.app)
failures = 0
skipped = 0

HAS_FFMPEG = shutil.which("ffmpeg") is not None
try:
    import fitz  # noqa: F401
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


def jpeg(width, height):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, format="JPEG")
    return buffer.getvalue()


def pdf(pages=3):
    document = fitz.open()
    for _ in range(pages):
        document.new_page()
    return document.tobytes()


def post(path, name, content, mime="image/jpeg", key=CMS_KEY):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post(path, files={"file": (name, content, mime)}, headers=headers)


def post_form(path, data, key=CMS_KEY):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.post(path, data=data, headers=headers)


def expect(description, response, status):
    global failures
    passed = response.status_code == status
    failures += 0 if passed else 1
    detail = ""
    if response.status_code != 200:
        detail = "  -> " + str(response.json().get("detail", ""))[:46]
    print("  [%s] %-32s %s (want %s)%s"
          % ("OK " if passed else "FAIL", description, response.status_code, status, detail))


def skip(description, reason):
    global skipped
    skipped += 1
    print("  [    ] %-32s skipped, no %s" % (description, reason))


print("\n  authentication")
expect("no key", post("/scan", "a.jpg", jpeg(64, 64), key=None), 401)
expect("wrong key", post("/scan", "a.jpg", jpeg(64, 64), key="wrong"), 403)
expect("health needs no key", client.get("/health"), 200)

print("\n  image")
expect("ordinary image", post("/scan", "ok.jpg", jpeg(512, 512)), 200)
expect("empty file", post("/scan", "empty.jpg", b""), 400)
expect("not an image", post("/scan", "x.jpg", b"not an image" * 100), 400)

print("\n  document")
service._recent.clear()
if HAS_PYMUPDF:
    response = post("/scan/document", "d.pdf", pdf(3), "application/pdf")
    expect("three page pdf", response, 200)
    if response.status_code == 200:
        body = response.json()
        print("       type=%s frames=%s worst_at=%s"
              % (body["type"], body["frames_scanned"], body["worst_at"]))
    expect("not a pdf", post("/scan/document", "d.pdf", b"nope", "application/pdf"), 400)
else:
    skip("pdf pages", "pymupdf")

print("\n  video and stream")
service._recent.clear()
if HAS_FFMPEG:
    expect("not a video", post("/scan/video", "v.mp4", b"nope", "video/mp4"), 400)
else:
    skip("video frames", "ffmpeg")
expect("hls rejects loopback", post_form("/scan/hls", {"url": "http://127.0.0.1/a.m3u8"}), 400)
expect("hls rejects private range", post_form("/scan/hls", {"url": "http://192.168.1.1/a.m3u8"}), 400)
expect("hls rejects file scheme", post_form("/scan/hls", {"url": "file:///etc/passwd"}), 400)

print("\n  rate limit of 6 frames per minute, per caller")
service._recent.clear()
for number in range(1, 7):
    expect("cms frame %d" % number, post("/scan", "a.jpg", jpeg(64, 64)), 200)
expect("cms frame 7 refused", post("/scan", "a.jpg", jpeg(64, 64)), 429)
expect("partnerA unaffected", post("/scan", "a.jpg", jpeg(64, 64), key=PARTNER_KEY), 200)

summary = "ALL PASSED" if failures == 0 else "%d FAILED" % failures
if skipped:
    summary += "  (%d skipped)" % skipped
print("\n%s\n" % summary)
raise SystemExit(1 if failures else 0)
