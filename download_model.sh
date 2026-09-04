#!/usr/bin/env bash
#
# Downloads the model into ./models so the service does not depend on the
# network at run time.
#
# download_file.sh does the fetching, over several connections and resuming each
# one; see the reasoning there. The sha256 check below is separate from that and
# matters on its own: a file can arrive at exactly the right length and still be
# wrong, and a corrupted set of weights would not fail loudly, it would just
# score every image badly.
set -uo pipefail

cd "$(dirname "$0")"
. ./download_file.sh

REPO="https://huggingface.co/strangerguardhf/nsfw_image_detection/resolve/main"
DEST="models/nsfw-image-detection"
WEIGHTS="$DEST/model.safetensors"
TOTAL=371761540
EXPECTED_SHA="846e9b76948a5c2e2cd44f91698a79b18850db6ba57d840233aa92a792b3a28f"

mkdir -p "$DEST"

for small in config.json preprocessor_config.json; do
  if [ ! -s "$DEST/$small" ]; then
    echo "Fetching $small"
    curl -sSL --retry 5 --max-time 120 "$REPO/$small" -o "$DEST/$small" || {
      echo "Failed: $small"; exit 1; }
  fi
done

if [ "$(size_of "$WEIGHTS")" -eq "$TOTAL" ]; then
  echo "Weights already present"
  exit 0
fi

fetch_parallel "$REPO/model.safetensors" "$WEIGHTS" "$TOTAL" || exit 1

echo "Verifying sha256"
SHA=$(sha256sum "$WEIGHTS" | awk '{print $1}')
if [ "$SHA" != "$EXPECTED_SHA" ]; then
  echo "CONTENT MISMATCH"
  echo "  expected: $EXPECTED_SHA"
  echo "  actual  : $SHA"
  exit 1
fi

echo "Done - $(( TOTAL / 1024 / 1024 )) MB, sha256 matches"
