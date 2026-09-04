"""
Turns video, HLS streams and PDFs into still frames for the scanner.

Every path here is bounded on purpose. A caller waits for the response, so the
work must finish in a predictable time: the sampling interval is derived from
the media's own duration so that no input ever yields more than MAX_FRAMES,
a live HLS stream is cut off after HLS_SECONDS because it would otherwise never
end, and a PDF is read at most MAX_PAGES deep.

Frame timestamps come out arithmetically rather than from ffmpeg metadata,
which keeps the caller able to say where in the video the worst frame was
without parsing ffmpeg's output format.
"""
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
from urllib.parse import urlparse

MAX_FRAMES = int(os.getenv("MAX_FRAMES", "60"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "40"))
HLS_SECONDS = int(os.getenv("HLS_SECONDS", "60"))
PDF_DPI = int(os.getenv("PDF_DPI", "72"))


class MediaError(Exception):
    pass


def _run(args, timeout):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise MediaError("ffmpeg is not installed in this image")
    except subprocess.TimeoutExpired:
        raise MediaError("ffmpeg timed out")


def _duration(source, timeout=60):
    result = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", source],
        timeout,
    )
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def _extract(source, interval, limit, extra, timeout):
    folder = tempfile.mkdtemp(prefix="frames-")
    pattern = os.path.join(folder, "f%05d.jpg")
    result = _run(
        ["ffmpeg", "-v", "error", "-y", *extra, "-i", source,
         "-vf", f"fps=1/{interval}", "-frames:v", str(limit),
         "-q:v", "4", pattern],
        timeout,
    )
    names = sorted(n for n in os.listdir(folder) if n.endswith(".jpg"))
    if not names:
        detail = result.stderr.decode("utf-8", "replace").strip()[:200]
        raise MediaError(detail or "no frames could be read")
    return [(round(i * interval, 2), os.path.join(folder, n)) for i, n in enumerate(names)]


def video_frames(path):
    """Samples a video file, spreading MAX_FRAMES evenly across its length."""
    duration = _duration(path)
    interval = max(1.0, duration / MAX_FRAMES) if duration else 1.0
    return _extract(path, interval, MAX_FRAMES, [], timeout=600)


def hls_frames(url):
    """Samples the first HLS_SECONDS of a stream, which may never end on its own."""
    check_url(url)
    interval = max(1.0, HLS_SECONDS / MAX_FRAMES)
    return _extract(url, interval, MAX_FRAMES, ["-t", str(HLS_SECONDS)], timeout=HLS_SECONDS * 4)


def check_url(url):
    """
    Refuses URLs that point back inside the network.

    Without this the scanner is an open proxy: anyone allowed to submit a
    stream could make the server fetch its own admin ports, a neighbour's
    database, or a cloud metadata endpoint, all from an address those services
    trust more than the caller's.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MediaError("Only http and https URLs are accepted")
    if not parsed.hostname:
        raise MediaError("URL has no host")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise MediaError("Host could not be resolved")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            raise MediaError(f"Host resolves to a non-public address ({address})")


def pdf_pages(data):
    """Renders PDF pages to images. Text is ignored; only what is drawn matters."""
    try:
        import fitz
    except ImportError:
        raise MediaError("PDF support needs pymupdf")

    try:
        document = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise MediaError("Not a readable PDF")

    folder = tempfile.mkdtemp(prefix="pages-")
    pages = []
    for number in range(min(document.page_count, MAX_PAGES)):
        target = os.path.join(folder, "p%05d.png" % number)
        document.load_page(number).get_pixmap(dpi=PDF_DPI).save(target)
        pages.append((number + 1, target))
    document.close()

    if not pages:
        raise MediaError("PDF has no pages")
    return pages
