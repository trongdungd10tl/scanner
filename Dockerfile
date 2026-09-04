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

# A static ffmpeg rather than apt's. The package wants 130MB to fetch and adds
# 455MB to the image, because it drags in the whole desktop media stack to
# satisfy its dependencies; this archive is 42MB and self-contained.
COPY download_ffmpeg.sh .
RUN sed -i 's/\r$//' download_ffmpeg.sh && bash download_ffmpeg.sh \
    && mv bin/ffmpeg bin/ffprobe /usr/local/bin/ && rmdir bin

COPY requirements-media.txt .
RUN pip install --no-cache-dir -r requirements-media.txt

COPY app.py media.py ./

RUN useradd --create-home --uid 10001 scanner && chown -R scanner:scanner /app
USER scanner

ENV MODEL_PATH=/app/models/nsfw-image-detection
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
