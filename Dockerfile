# Builds a self-contained image for the NSFW scanning service. The model
# weights are downloaded during the build, so a copy of this directory on a new
# server needs one command and no network access at run time.
#
# Step order is deliberate. Docker discards every cached layer below the first
# one that changes, so the slow, rarely edited installs come first: on this
# machine they measured 564s for apt, 1163s for the Python packages and 296s to
# fetch the weights.
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

# NudeNet, the exposed-body-part detector behind the "Explicit" score. Its
# 12MB ONNX weights ship inside the wheel, so nothing is fetched at run time.
# It has its own layer below the weights so adding it did not throw away the
# cached download above. Pinned: a new release could move the scores that the
# callers' block threshold (0.6) was chosen against.
RUN pip install --no-cache-dir nudenet==3.4.2

COPY app.py media.py logs.py ./

RUN mkdir -p /app/logs \
    && useradd --create-home --uid 10001 scanner \
    && chown -R scanner:scanner /app
USER scanner

ENV MODEL_PATH=/app/models/nsfw-image-detection
ENV LOG_DIR=/app/logs
EXPOSE 8508

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8508/media-moderation/health',timeout=4)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8508"]
