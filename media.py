"""
Fetches the image a caller names by link.

Every link goes through check_url first, so that the scanner cannot be used to
reach addresses inside the network; ALLOW_HOSTS names the few private hosts it
may still read from.
"""
import ipaddress
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(512 * 1024 * 1024)))

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


# Hosts fetched from an internal address instead of the one their name resolves
# to, as "host=address:port,...". A CDN is often only reachable from inside the
# network at its backend address: its public address refuses connections that
# start inside, and the backend listens on a port of its own (8080) that DNS
# cannot express. The request still carries the original Host header, so the
# web server there picks the right site. Naming a host here is a decision to
# trust it, so it skips the public-address check the same way ALLOW_HOSTS does.
#   FETCH_VIA=videocdn.kakoak.tls.tl=127.0.0.1:8080
FETCH_VIA = {}
for _entry in os.getenv("FETCH_VIA", "").split(","):
    if "=" in _entry:
        _host, _target = _entry.split("=", 1)
        FETCH_VIA[_host.strip().lower()] = _target.strip()


class MediaError(Exception):
    pass


def _route(url):
    """Returns the URL to connect to and the Host header to send, if any."""
    parsed = urlparse(url)
    via = FETCH_VIA.get((parsed.hostname or "").lower())
    if not via or parsed.scheme not in ("http", "https"):
        check_url(url)
        return url, {}
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return "http://" + via + path, {"Host": parsed.hostname}


def check_url(url):
    """
    Refuses URLs that point back inside the network.

    Without this the scanner is an open proxy: anyone allowed to submit a link
    could make the server fetch its own admin ports, a neighbour's database, or
    a cloud metadata endpoint, all from an address those services trust more
    than the caller's.
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
    target, headers = _route(url)
    headers["User-Agent"] = "nsfw-scanner"
    request = urllib.request.Request(target, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            body = response.read(limit + 1)
    except urllib.error.HTTPError as e:
        raise MediaError(f"Link returned HTTP {e.code}")
    except Exception:
        raise MediaError("Link could not be reached")
    if len(body) > limit:
        raise MediaError("Image is larger than allowed")
    if declared and declared.isdigit() and len(body) != int(declared):
        raise MediaError(
            f"Link sent {len(body)} of {declared} bytes; the transfer was cut short"
        )
    return body


def fetch_bytes(url, limit=None):
    """Reads a whole image into memory."""
    return _get(url, limit or MAX_DOWNLOAD_BYTES, timeout=300)
