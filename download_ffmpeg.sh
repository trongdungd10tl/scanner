#!/usr/bin/env bash
#
# Downloads a static ffmpeg build into ./bin, which media.py needs to turn video
# and HLS streams into frames.
#
# A static build rather than the distribution package: apt wants 130MB to fetch
# and adds 455MB to the image, because it pulls in the whole desktop media stack
# to satisfy dependencies. This archive is 39MB and carries everything it needs
# inside the two binaries.
#
# download_file.sh does the fetching, over several connections and resuming each
# one, because a single connection to this host measured 10 KB/s and dropped
# after thirteen minutes.
#
# Extraction goes through Python rather than tar, which would need xz-utils
# installed. That package would have to be added to an earlier apt layer, and
# editing that layer discards the cached 354MB model download below it.
#
# The published checksum is fetched alongside the archive. That catches a
# truncated or corrupted transfer, which is what has actually gone wrong here
# before; it is not a defence against the mirror itself serving something else.
set -uo pipefail

cd "$(dirname "$0")"
. ./download_file.sh

BASE="https://johnvansickle.com/ffmpeg/releases"
ARCHIVE="ffmpeg-release-amd64-static.tar.xz"
DEST="bin"

mkdir -p "$DEST"

if [ -x "$DEST/ffmpeg" ] && [ -x "$DEST/ffprobe" ]; then
  echo "ffmpeg already present"
  exit 0
fi

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "Fetching $ARCHIVE.md5"
curl -sSL --retry 5 --max-time 60 "$BASE/$ARCHIVE.md5" -o "$WORK/$ARCHIVE.md5" || {
  echo "Checksum download failed"; exit 1; }

fetch_parallel "$BASE/$ARCHIVE" "$WORK/$ARCHIVE" || exit 1

echo "Verifying md5"
if ! (cd "$WORK" && md5sum -c "$ARCHIVE.md5" >/dev/null 2>&1); then
  echo "CHECKSUM MISMATCH"
  echo "  expected: $(awk '{print $1}' "$WORK/$ARCHIVE.md5")"
  echo "  actual  : $(md5sum "$WORK/$ARCHIVE" | awk '{print $1}')"
  exit 1
fi

echo "Extracting"
python3 - "$WORK/$ARCHIVE" "$WORK" <<'PYTHON'
import os
import shutil
import sys
import tarfile

archive, target = sys.argv[1], sys.argv[2]
with tarfile.open(archive, "r:xz") as bundle:
    for member in bundle.getmembers():
        if member.isfile() and os.path.basename(member.name) in ("ffmpeg", "ffprobe"):
            source = bundle.extractfile(member)
            out = os.path.join(target, os.path.basename(member.name))
            with open(out, "wb") as handle:
                shutil.copyfileobj(source, handle)
            os.chmod(out, 0o755)
PYTHON

for tool in ffmpeg ffprobe; do
  if [ ! -f "$WORK/$tool" ]; then
    echo "$tool was not in the archive"
    exit 1
  fi
  mv "$WORK/$tool" "$DEST/$tool"
  chmod +x "$DEST/$tool"
done

echo "Done - $("$DEST/ffmpeg" -version 2>/dev/null | head -1)"
