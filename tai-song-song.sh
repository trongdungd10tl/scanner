#!/usr/bin/env bash
# Tai model.safetensors bang 8 ket noi song song.
#
# HuggingFace gioi han toc do TUNG ket noi (~110 KB/s do duoc tu day), khong
# phai bang thong tong - da kiem bang cach chay hai ket noi cung luc, ca hai
# deu giu nguyen toc do. Nen chia thanh nhieu doan la nhanh len that.
set -euo pipefail

U="https://huggingface.co/strangerguardhf/nsfw_image_detection/resolve/main/model.safetensors"
DICH="models/nsfw-image-detection/model.safetensors"
TONG=$(cat /tmp/kich-thuoc.txt)
SO_PHAN=8

mkdir -p /tmp/phan && rm -f /tmp/phan/*
MOI_PHAN=$(( TONG / SO_PHAN ))

for i in $(seq 0 $(( SO_PHAN - 1 ))); do
  BAT_DAU=$(( i * MOI_PHAN ))
  if [ "$i" -eq $(( SO_PHAN - 1 )) ]; then KET_THUC=$(( TONG - 1 )); else KET_THUC=$(( BAT_DAU + MOI_PHAN - 1 )); fi
  curl -sL -r "${BAT_DAU}-${KET_THUC}" "$U" -o "/tmp/phan/p$i" --retry 5 --retry-delay 3 &
done
wait

cat /tmp/phan/p0 /tmp/phan/p1 /tmp/phan/p2 /tmp/phan/p3 \
    /tmp/phan/p4 /tmp/phan/p5 /tmp/phan/p6 /tmp/phan/p7 > "$DICH"
rm -f /tmp/phan/*

THUC=$(stat -c%s "$DICH")
echo "mong doi: $TONG"
echo "thuc te : $THUC"
[ "$THUC" -eq "$TONG" ] && echo "KHOP" || { echo "LECH KICH THUOC"; exit 1; }
