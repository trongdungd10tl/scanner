"""Tai mo hinh ve thu muc ./models de khong phu thuoc mang luc chay."""
import sys
from transformers import AutoImageProcessor, SiglipForImageClassification

# Ten that lay tu app.py cua repo GitHub. README cua repo ghi la
# "prithivMLmods/..." nhung dinh danh do khong ton tai tren HuggingFace.
TEN = "strangerguardhf/nsfw_image_detection"
DICH = "models/nsfw-image-detection"

print(f"Dang tai {TEN} ...")
try:
    processor = AutoImageProcessor.from_pretrained(TEN)
    model = SiglipForImageClassification.from_pretrained(TEN)
except Exception as e:
    print("LOI:", type(e).__name__, e)
    sys.exit(1)

processor.save_pretrained(DICH)
model.save_pretrained(DICH)

print("Da luu vao", DICH)
print("Cac lop:", model.config.id2label)
print("So tham so: %.1f trieu" % (sum(p.numel() for p in model.parameters()) / 1e6))
