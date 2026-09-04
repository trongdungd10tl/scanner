"""
Turns video, HLS streams and PDFs into still frames for the scanner.

Every path here is bounded on purpose. A caller waits for the response, so the
work must finish in a predictable time: the sampling interval is derived from
the media's own duration so that no input ever yields more than MAX_FRAMES,
a live HLS stream is cut off after HLS_SECONDS because it would otherwise never
end, and a PDF is read at most MAX_PAGES deep.

Frame timestamps come out arithmetically rather than from ffmpeg metadata, which
keeps the caller able to say where in the video the worst frame was without
parsing ffmpeg's output. Verified against a video of coloured blocks: the
reported times matched the real changes exactly.

HLS is fetched here in Python rather than by handing ffmpeg the URL, for two
reasons that survive whichever ffmpeg is installed. Every segment address then
goes through the same public-address check as the playlist, which matters
because those addresses come out of a file the caller chose. And the smallest
rendition can be picked deliberately: frames are resized to 256x256 before
scoring, so a 1080p stream is thrown away almost entirely, and on one test
stream the first listed variant was 2.1 Mbps against 246 Kbps for the smallest
-- 18MB fetched for two segments against 2MB for six.
"""
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

MAX_FRAMES = int(os.getenv("MAX_FRAMES", "60"))
MAX_PAGES = int(os.getenv("MAX_PAGES", "40"))
HLS_SECONDS = int(os.getenv("HLS_SECONDS", "60"))
MAX_SEGMENTS = int(os.getenv("MAX_SEGMENTS", "120"))
MAX_SEGMENT_BYTES = int(os.getenv("MAX_SEGMENT_BYTES", str(64 * 1024 * 1024)))
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(512 * 1024 * 1024)))
PDF_DPI = int(os.getenv("PDF_DPI", "72"))

# Hosts allowed to be fetched despite resolving to a private address. An
# uploading application usually sits on one -- a CMS on localhost or behind the
# same firewall -- so without this the check that stops the scanner being an
# open proxy also stops it reading anything the caller actually owns. Naming
# them one at a time keeps that hole exactly as wide as it has to be.
ALLOW_HOSTS = {
    h.strip().lower()
    for h in os.getenv("ALLOW_HOSTS", "").split(",")
    if h.strip()
}


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


def check_url(url):
    """
    Refuses URLs that point back inside the network.

    Without this the scanner is an open proxy: anyone allowed to submit a stream
    could make the server fetch its own admin ports, a neighbour's database, or
    a cloud metadata endpoint, all from an address those services trust more
    than the caller's. Segment addresses are checked too, since a playlist can
    name any host it likes.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MediaError("Only http and https URLs are accepted")
    if not parsed.hostname:
        raise MediaError("URL has no host")
    if parsed.hostname.lower() in ALLOW_HOSTS:
        return
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise MediaError("Host could not be resolved")
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast):
            raise MediaError(
                f"Host resolves to a non-public address ({address}). "
                "Add it to ALLOW_HOSTS if the scanner should read from it."
            )


def _get(url, limit, timeout=60):
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "nsfw-scanner"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            body = response.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise MediaError(f"Stream returned HTTP {e.code}")
    except Exception:
        raise MediaError("Stream could not be reached")
    if len(body) > limit:
        raise MediaError("Stream part is larger than allowed")
    if declared and declared.isdigit() and len(body) != int(declared):
        raise MediaError(
            f"Link sent {len(body)} of {declared} bytes; the transfer was cut short"
        )
    return body


def _bandwidth_of(line):
    for part in line.split(","):
        part = part.strip()
        if part.startswith("BANDWIDTH="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


def fetch_bytes(url, limit=None):
    """Reads a whole resource into memory. For images and PDFs."""
    return _get(url, limit or MAX_DOWNLOAD_BYTES, timeout=300)


def fetch_to_file(url, path, limit=None):
    """
    Streams a resource to disk. For video, which is too large to hold in memory.

    The ceiling matters more here than on an upload. An upload costs the caller
    the bandwidth it spends; a link costs the scanner, so a short request can
    ask it to pull a file of any size.
    """
    check_url(url)
    limit = limit or MAX_DOWNLOAD_BYTES
    request = urllib.request.Request(url, headers={"User-Agent": "nsfw-scanner"})
    written = 0
    expected = None
    try:
        with urllib.request.urlopen(request, timeout=600) as response, \
                open(path, "wb") as target:
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit():
                expected = int(declared)
                if expected > limit:
                    raise MediaError(
                        f"Resource is larger than {limit // 1024 // 1024}MB"
                    )
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise MediaError(
                        f"Resource is larger than {limit // 1024 // 1024}MB"
                    )
                target.write(chunk)
    except MediaError:
        raise
    except urllib.error.HTTPError as e:
        raise MediaError(f"Link returned HTTP {e.code}")
    except Exception:
        raise MediaError("Link could not be read")
    if not written:
        raise MediaError("Link returned nothing")
    # A dropped connection leaves a shorter file and no exception, and ffmpeg
    # then reads whatever arrived. For a video that is worse than an error: a
    # truncated file can scan clean while the part that never downloaded is the
    # part that mattered.
    if expected is not None and written != expected:
        raise MediaError(
            f"Link sent {written} of {expected} bytes; the transfer was cut short"
        )
    return path


def _parse_playlist(text):
    """Returns ([(bandwidth, uri)], [(duration, uri)], init_uri) for one playlist."""
    variants, segments, init, pending = [], [], None, None
    variant_bandwidth = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            variant_bandwidth = _bandwidth_of(line)
            continue
        if line.startswith("#EXT-X-MAP:"):
            for part in line[len("#EXT-X-MAP:"):].split(","):
                if part.strip().startswith("URI="):
                    init = part.split("=", 1)[1].strip().strip('"')
            continue
        if line.startswith("#EXTINF:"):
            try:
                pending = float(line[len("#EXTINF:"):].split(",")[0])
            except ValueError:
                pending = 0.0
            continue
        if line.startswith("#"):
            continue
        if variant_bandwidth is not None:
            variants.append((variant_bandwidth, line))
            variant_bandwidth = None
        else:
            segments.append((pending or 0.0, line))
            pending = None

    return variants, segments, init


def _fetch_stream(url, folder):
    """
    Downloads enough of an HLS stream to sample, and writes a local playlist.

    ffmpeg then reads that playlist from disk, so it never touches the network.
    """
    text = _get(url, 4 * 1024 * 1024).decode("utf-8", "replace")
    variants, segments, init = _parse_playlist(text)

    if variants and not segments:
        # The smallest rendition, not the first one listed. The model resizes
        # every frame to 256x256 before scoring, so a 1080p rendition is thrown
        # away almost entirely; on one test stream the first listed variant was
        # 2.1 Mbps against 246 Kbps for the smallest, nearly nine times the
        # bytes for a picture that ends up identical.
        _, chosen = min(variants, key=lambda item: item[0])
        url = urljoin(url, chosen)
        text = _get(url, 4 * 1024 * 1024).decode("utf-8", "replace")
        _, segments, init = _parse_playlist(text)

    if not segments:
        raise MediaError("Playlist contained no segments")

    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10"]

    if init:
        body = _get(urljoin(url, init), MAX_SEGMENT_BYTES)
        with open(os.path.join(folder, "init.mp4"), "wb") as handle:
            handle.write(body)
        lines.append('#EXT-X-MAP:URI="init.mp4"')

    collected = 0.0
    for index, (seconds, uri) in enumerate(segments[:MAX_SEGMENTS]):
        body = _get(urljoin(url, uri), MAX_SEGMENT_BYTES)
        name = "s%05d%s" % (index, os.path.splitext(urlparse(uri).path)[1] or ".ts")
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(body)
        lines.append("#EXTINF:%.3f," % seconds)
        lines.append(name)
        collected += seconds
        if collected >= HLS_SECONDS:
            break

    lines.append("#EXT-X-ENDLIST")
    playlist = os.path.join(folder, "local.m3u8")
    with open(playlist, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return playlist


def hls_frames(url):
    """Samples the first HLS_SECONDS of a stream, which may never end on its own."""
    folder = tempfile.mkdtemp(prefix="hls-")
    playlist = _fetch_stream(url, folder)
    interval = max(1.0, HLS_SECONDS / MAX_FRAMES)
    return _extract(playlist, interval, MAX_FRAMES, [], timeout=600)


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
