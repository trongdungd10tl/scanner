"""
Checks the rate limit, the SSRF guard and input handling across all four
endpoints. Runs against the application directly, so no server needs to be
started.

    python test_input.py

There is nothing here about authentication because the service has none: the
port itself is the boundary now.

Images, video and streams are named by URL, so the happy paths are served by a
small HTTP server this file starts on loopback, with ALLOW_HOSTS naming only
that address. The blocked cases therefore use other private addresses, never
loopback: an allowed host would still return 400 from failing to connect rather
than from the check under test, and green for the wrong reason is how a dead
endpoint stayed hidden here once already.

Cases needing ffmpeg or pymupdf are skipped when those are missing, which is
normal on a development machine; the Docker image has both, so nothing is
skipped there. Skips are reported, never counted as passes.
"""
import http.server
import io
import os
import shutil
import threading
import warnings

warnings.filterwarnings("ignore")

os.environ["CONTEXT_PATH"] = "/media-moderation"
os.environ["RATE_LIMIT_PER_MINUTE"] = "6"
os.environ["ALLOW_HOSTS"] = "127.0.0.1"

from fastapi.testclient import TestClient
from PIL import Image

import app as service

client = TestClient(service.app)
PREFIX = "/media-moderation"
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


SERVED = {"/a.jpg": jpeg(512, 512), "/junk.bin": b"not an image" * 100}
if HAS_PYMUPDF:
    SERVED["/a.pdf"] = pdf(3)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = SERVED.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % server.server_address[1]


def post_url(path, link):
    return client.post(PREFIX + path, data={"url": link})


def post_file(path, name, content, mime):
    return client.post(PREFIX + path, files={"file": (name, content, mime)})


def expect(description, response, status):
    global failures
    passed = response.status_code == status
    failures += 0 if passed else 1
    detail = ""
    if response.status_code != 200:
        detail = "  -> " + str(response.json().get("detail", ""))[:44]
    print("  [%s] %-32s %s (want %s)%s"
          % ("OK " if passed else "FAIL", description, response.status_code, status, detail))


def skip(description, reason):
    global skipped
    skipped += 1
    print("  [    ] %-32s skipped, no %s" % (description, reason))


print("\n  health")
expect("health answers", client.get(PREFIX + "/health"), 200)

print("\n  image by link")
service._recent.clear()
expect("real link", post_url("/scan", BASE + "/a.jpg"), 200)
expect("link is not an image", post_url("/scan", BASE + "/junk.bin"), 400)
expect("link that 404s", post_url("/scan", BASE + "/missing.jpg"), 400)
expect("no url at all", client.post(PREFIX + "/scan"), 422)
expect("upload no longer accepted",
       post_file("/scan", "a.jpg", jpeg(64, 64), "image/jpeg"), 422)

print("\n  blocked addresses")
service._recent.clear()
expect("private range", post_url("/scan", "http://10.0.0.1/a.jpg"), 400)
expect("private range, video", post_url("/scan/video", "http://192.168.1.1/a.mp4"), 400)
expect("file scheme", post_url("/scan/document", "file:///etc/passwd"), 400)
expect("hls private range", post_url("/scan/hls", "http://172.16.0.1/a.m3u8"), 400)

print("\n  document")
service._recent.clear()
if HAS_PYMUPDF:
    response = post_url("/scan/document", BASE + "/a.pdf")
    expect("three page pdf by link", response, 200)
    if response.status_code == 200:
        body = response.json()
        print("       type=%s frames=%s worst_at=%s"
              % (body["type"], body["frames_scanned"], body["worst_at"]))
    service._recent.clear()
    expect("upload still accepted",
           post_file("/scan/document", "d.pdf", pdf(2), "application/pdf"), 200)
    expect("not a pdf", post_file("/scan/document", "d.pdf", b"nope", "application/pdf"), 400)
else:
    skip("pdf pages", "pymupdf")

print("\n  video")
service._recent.clear()
if HAS_FFMPEG:
    expect("link is not a video", post_url("/scan/video", BASE + "/junk.bin"), 400)
else:
    skip("video frames", "ffmpeg")

print("\n  rate limit of 6 frames per minute, per client address")
service._recent.clear()
for number in range(1, 7):
    expect("frame %d" % number, post_url("/scan", BASE + "/a.jpg"), 200)
expect("frame 7 refused", post_url("/scan", BASE + "/a.jpg"), 429)

# Buckets are per address, but TestClient only ever has one, so the "another
# caller is unaffected" case cannot be driven through HTTP any more - it used to
# be driven with a second key. Charging a different address directly is the
# closest honest substitute: it proves the budget is keyed, not global.
try:
    service._charge("10.9.9.9", 6)
    passed = True
except Exception:
    passed = False
failures += 0 if passed else 1
print("  [%s] %-32s %s" % ("OK " if passed else "FAIL",
                           "another address has its own budget",
                           "allowed" if passed else "refused"))

server.shutdown()

summary = "ALL PASSED" if failures == 0 else "%d FAILED" % failures
if skipped:
    summary += "  (%d skipped)" % skipped
print("\n%s\n" % summary)
raise SystemExit(1 if failures else 0)
