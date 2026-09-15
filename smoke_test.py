"""
Smoke test. Sends one generated image to a running service to confirm it
answers. Proves the service is alive, not that the model judges well.

    python smoke_test.py [http://host:port]
"""
import io
import sys
import urllib.request

from PIL import Image

service = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

buffer = io.BytesIO()
Image.new("RGB", (256, 256), (200, 180, 160)).save(buffer, format="JPEG")
content = buffer.getvalue()

boundary = "----smoke"
body = (
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="file"; filename="test.jpg"\r\n'
    "Content-Type: image/jpeg\r\n\r\n"
).encode() + content + f"\r\n--{boundary}--\r\n".encode()

request = urllib.request.Request(
    f"{service}/scan",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
print(urllib.request.urlopen(request, timeout=120).read().decode())
