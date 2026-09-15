# Builds a self-contained image for the NSFW scanning service. The model
# weights are downloaded during the build, so a copy of this directory on a new
# server needs one command and no network access at run time.
#
# Step order is deliberate. Docker discards every cached layer below the first
# one that changes, so the slow, rarely edited installs come first: on this
# machine they measured 564s for apt, 1163s for the Python packages and 296s to
# fetch the weights. That is also why the media dependencies sit in their own
# requirements file installed further down. Folding them into requirements.txt
# would move the edit above the weights layer and turn a two minute rebuild into
# a thirty minute one every time one of them changes.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
        torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The sed strips carriage returns. A checkout on Windows turns the script's line
# endings into CRLF, and bash then fails on every line with "\r: command not
# found" -- a copy of this directory made on Windows would otherwise not build
# on a Linux server.
COPY download_file.sh download_model.sh ./
RUN sed -i 's/\r$//' download_file.sh download_model.sh && bash download_model.sh

# apt's ffmpeg. It costs 130MB to fetch and 455MB in the image, against 39MB
# for a static build, and it drags in Mesa and LLVM for a service with no
# display. It is still the right choice, because the static build was tried and
# crashes: any URL with a hostname segfaulted, and so did reading a plain local
# MPEG-TS file, which is the format HLS segments arrive in. Two unrelated
# crashes in one binary is enough.
#
# Set APT_MIRROR when deb.debian.org is slow or unreachable from the build
# machine. From here it delivered 17 KB/s, which put this step at two hours,
# while a local mirror measured 12 MB/s for the same file -- the packages are
# not the problem, the route to them is. Measure before choosing one:
#
#   curl -o /dev/null -w '%{speed_download}\n' \
#        https://MIRROR/debian/dists/trixie/main/binary-amd64/Packages.gz
#
#   APT_MIRROR=your.mirror docker compose build
# Lines mentioning security are left alone: a mirror that carries /debian often
# does not carry /debian-security, and rewriting both turns a slow build into a
# failing one with a 404 on the security Release file.
ARG APT_MIRROR=deb.debian.org
RUN if [ "$APT_MIRROR" != "deb.debian.org" ]; then \
        sed -i "/security/!s|deb.debian.org|$APT_MIRROR|g" \
            /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-media.txt .
RUN pip install --no-cache-dir -r requirements-media.txt

COPY app.py media.py logs.py ./

RUN mkdir -p /app/logs \
    && useradd --create-home --uid 10001 scanner \
    && chown -R scanner:scanner /app
USER scanner

ENV MODEL_PATH=/app/models/nsfw-image-detection
ENV LOG_DIR=/app/logs
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/media-moderation/health',timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
