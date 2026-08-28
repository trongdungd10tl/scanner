#!/usr/bin/env bash
#
# Tai model.safetensors bang nhieu ket noi song song, co noi tiep va tu kiem.
#
# VI SAO CHIA NHIEU PHAN
# HuggingFace bop toc do TUNG ket noi (~110 KB/s do duoc tu day), khong phai
# bang thong tong - da kiem bang cach chay hai ket noi cung luc, ca hai deu giu
# nguyen toc do. Chia ra nen nhanh len that: do duoc ~1.1 MB/s voi 8 phan.
#
# VI SAO PHAI NOI TIEP DUOC
# Ban dau chi dung "curl --retry" roi cho tat ca chay xong. Cach do HONG: khi
# ket noi dut giua chung, curl bo cuoc va de lai mot phan tai do, con --retry
# khong nối lại từ chỗ đứt. Ghep lai duoc mot tep thieu 134MB.
# Gio moi phan chay trong vong lap, moi vong xin dung phan con thieu, cho toi
# khi du kich thuoc.
#
# Cuoi cung doi chieu sha256 voi gia tri HuggingFace cong bo - dung kich thuoc
# van co the sai noi dung.
set -uo pipefail

U="https://huggingface.co/strangerguardhf/nsfw_image_detection/resolve/main/model.safetensors"
DICH="models/nsfw-image-detection/model.safetensors"
TONG=371761540
SHA_MONG_DOI="846e9b76948a5c2e2cd44f91698a79b18850db6ba57d840233aa92a792b3a28f"
SO_PHAN=8
LAN_THU_TOI_DA=40

mkdir -p /tmp/phan "$(dirname "$DICH")"
MOI_PHAN=$(( TONG / SO_PHAN ))

kich_thuoc() { [ -f "$1" ] && stat -c%s "$1" || echo 0; }

tai_mot_phan() {
  local i=$1 bat_dau=$2 ket_thuc=$3
  local tep="/tmp/phan/p$i"
  local can=$(( ket_thuc - bat_dau + 1 ))

  for _ in $(seq 1 $LAN_THU_TOI_DA); do
    local co
    co=$(kich_thuoc "$tep")
    [ "$co" -ge "$can" ] && return 0
    # Xin tiep tu dung cho dang thieu.
    curl -sL -r "$(( bat_dau + co ))-${ket_thuc}" "$U" --max-time 900 >> "$tep" || true
  done

  [ "$(kich_thuoc "$tep")" -ge "$can" ]
}

echo "Tai $SO_PHAN phan song song..."
for i in $(seq 0 $(( SO_PHAN - 1 ))); do
  BAT_DAU=$(( i * MOI_PHAN ))
  if [ "$i" -eq $(( SO_PHAN - 1 )) ]; then KET_THUC=$(( TONG - 1 )); else KET_THUC=$(( BAT_DAU + MOI_PHAN - 1 )); fi
  tai_mot_phan "$i" "$BAT_DAU" "$KET_THUC" &
done
wait

# Kiem tung phan TRUOC khi ghep. Ghep roi moi phat hien thieu thi mat ca me.
LOI=0
for i in $(seq 0 $(( SO_PHAN - 1 ))); do
  BAT_DAU=$(( i * MOI_PHAN ))
  if [ "$i" -eq $(( SO_PHAN - 1 )) ]; then KET_THUC=$(( TONG - 1 )); else KET_THUC=$(( BAT_DAU + MOI_PHAN - 1 )); fi
  CAN=$(( KET_THUC - BAT_DAU + 1 ))
  CO=$(kich_thuoc "/tmp/phan/p$i")
  if [ "$CO" -ne "$CAN" ]; then
    echo "  phan $i: $CO / $CAN  THIEU"
    LOI=1
  fi
done
[ "$LOI" -eq 1 ] && { echo "Chua du, chay lai script de tai tiep."; exit 1; }

cat $(for i in $(seq 0 $(( SO_PHAN - 1 ))); do echo "/tmp/phan/p$i"; done) > "$DICH"

THUC=$(kich_thuoc "$DICH")
[ "$THUC" -ne "$TONG" ] && { echo "Lech kich thuoc: $THUC / $TONG"; exit 1; }

echo "Doi chieu sha256..."
SHA=$(sha256sum "$DICH" | awk '{print $1}')
if [ "$SHA" != "$SHA_MONG_DOI" ]; then
  echo "SAI NOI DUNG"
  echo "  mong doi: $SHA_MONG_DOI"
  echo "  thuc te : $SHA"
  exit 1
fi

rm -f /tmp/phan/p*
echo "XONG - $(( TONG / 1024 / 1024 )) MB, sha256 khop"
