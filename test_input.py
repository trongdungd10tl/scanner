"""
Checks the rate limit, the SSRF guard and input handling of the image
endpoint. Runs against the application directly, so no server needs to be
started.

    python test_input.py

There is nothing here about authentication because the service has none: the
port itself is the boundary now.

Images are named by URL, so the happy paths are served by a
small HTTP server this file starts on loopback, with ALLOW_HOSTS naming only
that address. The blocked cases therefore use other private addresses, never
loopback: an allowed host would still return 400 from failing to connect rather
than from the check under test, and green for the wrong reason is how a dead
endpoint stayed hidden here once already.

"""
import http.server
import io
import os
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

def jpeg(width, height):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (120, 140, 160)).save(buffer, format="JPEG")
    return buffer.getvalue()


SERVED = {"/a.jpg": jpeg(512, 512), "/junk.bin": b"not an image" * 100}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = SERVED.get(self.path)
        # /cdn.jpg answers only to its own site name, like a virtual host.
        if self.path == "/cdn.jpg":
            body = SERVED["/a.jpg"] if self.headers.get("Host") == "cdn.test" else None
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


print("\n  health")
expect("health answers", client.get(PREFIX + "/health"), 200)

print("\n  image by link")
service._recent.clear()
expect("real link", post_url("/scan", BASE + "/a.jpg"), 200)
expect("link is not an image", post_url("/scan", BASE + "/junk.bin"), 400)
expect("link that 404s", post_url("/scan", BASE + "/missing.jpg"), 400)
expect("neither url nor file", client.post(PREFIX + "/scan"), 400)

print("\n  image as file")
service._recent.clear()
expect("upload accepted", post_file("/scan", "a.jpg", jpeg(64, 64), "image/jpeg"), 200)
expect("upload not an image", post_file("/scan", "a.jpg", b"nope", "image/jpeg"), 400)
expect("empty upload", post_file("/scan", "a.jpg", b"", "image/jpeg"), 400)
expect("both url and file",
       client.post(PREFIX + "/scan", data={"url": BASE + "/a.jpg"},
                   files={"file": ("a.jpg", jpeg(64, 64), "image/jpeg")}), 400)

print("\n  FETCH_VIA")
service._recent.clear()
service.media.FETCH_VIA["cdn.test"] = "127.0.0.1:%d" % server.server_address[1]
expect("unresolvable host via backend", post_url("/scan", "http://cdn.test/cdn.jpg"), 200)
del service.media.FETCH_VIA["cdn.test"]
expect("same host without FETCH_VIA", post_url("/scan", "http://cdn.test/cdn.jpg"), 400)

print("\n  blocked addresses")
service._recent.clear()
expect("private range", post_url("/scan", "http://10.0.0.1/a.jpg"), 400)
expect("private range 192.168", post_url("/scan", "http://192.168.1.1/a.jpg"), 400)
expect("file scheme", post_url("/scan", "file:///etc/passwd"), 400)

print("\n  rate limit of 6 images per minute, per client address")
service._recent.clear()
for number in range(1, 7):
    expect("image %d" % number, post_url("/scan", BASE + "/a.jpg"), 200)
expect("image 7 refused", post_url("/scan", BASE + "/a.jpg"), 429)

# Buckets are per address, but TestClient only ever has one, so the "another
# caller is unaffected" case cannot be driven through HTTP any more - it used to
# be driven with a second key. Charging a different address directly is the
# closest honest substitute: it proves the budget is keyed, not global.
try:
    for _ in range(6):
        service._charge("10.9.9.9")
    passed = True
except Exception:
    passed = False
failures += 0 if passed else 1
print("  [%s] %-32s %s" % ("OK " if passed else "FAIL",
                           "another address has its own budget",
                           "allowed" if passed else "refused"))

server.shutdown()

summary = "ALL PASSED" if failures == 0 else "%d FAILED" % failures
print("\n%s\n" % summary)
raise SystemExit(1 if failures else 0)
