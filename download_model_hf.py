"""
Alternative downloader using the transformers library. Simpler than
download_model.sh but uses a single connection, which HuggingFace throttles,
so it takes roughly ten times longer. Kept as a fallback.

    python download_model_hf.py
"""
import sys

from transformers import AutoImageProcessor, SiglipForImageClassification

HUB_NAME = "strangerguardhf/nsfw_image_detection"
DEST = "models/nsfw-image-detection"

print(f"Downloading {HUB_NAME} ...")
try:
    processor = AutoImageProcessor.from_pretrained(HUB_NAME)
    model = SiglipForImageClassification.from_pretrained(HUB_NAME)
except Exception as e:
    print("FAILED:", type(e).__name__, e)
    sys.exit(1)

processor.save_pretrained(DEST)
model.save_pretrained(DEST)

print("Saved to", DEST)
print("Labels:", model.config.id2label)
print("Parameters: %.1f million" % (sum(p.numel() for p in model.parameters()) / 1e6))
