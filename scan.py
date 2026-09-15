"""
Command line client. Scans one image through a running service and prints the
per-label scores as a bar chart.

    python scan.py path/to/image.jpg

Set SCANNER_URL to reach a service on another host.
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

path = sys.argv[1]
service = os.getenv("SCANNER_URL", "http://127.0.0.1:8000")

if not os.path.isfile(path):
    print("File not found:", path)
    sys.exit(1)

content = open(path, "rb").read()
mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
name = os.path.basename(path)

boundary = "----scan"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
    f"Content-Type: {mime}\r\n\r\n"
).encode() + content + f"\r\n--{boundary}--\r\n".encode()

request = urllib.request.Request(
    f"{service}/scan",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)

try:
    result = json.loads(urllib.request.urlopen(request, timeout=300).read())
except urllib.error.HTTPError as e:
    print("Rejected:", json.loads(e.read()).get("detail"))
    sys.exit(1)

print(f"\n{name}  ({len(content)/1024:.0f} KB, {mime})\n")
for label, score in sorted(result["scores"].items(), key=lambda x: -x[1]):
    print(f"  {label:<22} {score:6.4f}  {'#' * round(score * 40)}")
print(f"\n  verdict    : {result['label']}")
print(f"  nsfw_score : {result['nsfw_score']}")
print(f"  took       : {result['took_ms']} ms\n")
