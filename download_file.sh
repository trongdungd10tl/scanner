#!/usr/bin/env bash
#
# Shared downloader for the two large files this project fetches. Source it,
# then call: fetch_parallel URL DESTINATION
#
# It exists because both downloads hit the same two problems, and writing the
# answer twice meant getting it wrong the second time.
#
# Problem one is speed. These hosts throttle each connection rather than total
# bandwidth, which was confirmed by running two at once and watching neither
# slow down. One connection to the model host managed 110 KB/s and one to the
# ffmpeg host 10 KB/s; eight at once moved 354MB in under five minutes.
#
# Problem two is dropped connections. "curl --retry" restarts a failed transfer
# from zero instead of continuing it, so on a link that dies part way through it
# can loop forever without finishing. It also leaves the partial file in place,
# which once produced a model file 134MB short that looked complete enough to
# concatenate. Every part here loops asking only for the bytes it still lacks,
# and every part is checked against its expected length before any of them are
# joined, because discovering a short part after concatenation costs the whole
# download.
set -uo pipefail

PARTS=${PARTS:-8}
MAX_TRIES=${MAX_TRIES:-60}

size_of() { [ -f "$1" ] && stat -c%s "$1" || echo 0; }

remote_size() {
    curl -sSLI --max-time 60 "$1" \
        | tr -d '\r' \
        | awk 'tolower($1) == "content-length:" {print $2}' \
        | tail -1
}

_fetch_part() {
    local url=$1 file=$2 first=$3 last=$4
    local needed=$(( last - first + 1 ))

    for _ in $(seq 1 "$MAX_TRIES"); do
        local have
        have=$(size_of "$file")
        [ "$have" -ge "$needed" ] && return 0
        curl -sL --max-time 900 -r "$(( first + have ))-${last}" "$url" >> "$file" || true
    done

    [ "$(size_of "$file")" -ge "$needed" ]
}

# fetch_parallel URL DESTINATION [EXPECTED_SIZE]
#
# Downloads URL to DESTINATION using PARTS connections, resuming each one until
# it is whole. Pass EXPECTED_SIZE to pin the length; otherwise it is read from
# the server. Returns non-zero if anything is still missing, and leaves the
# parts in place so that running again continues rather than restarts.
fetch_parallel() {
    local url=$1 dest=$2 total=${3:-}

    if [ -z "$total" ]; then
        total=$(remote_size "$url")
    fi
    if ! [ "${total:-0}" -gt 0 ] 2>/dev/null; then
        echo "Could not determine the size of $url"
        return 1
    fi

    local work="/tmp/parts-$(echo "$url" | md5sum | cut -c1-12)"
    mkdir -p "$work" "$(dirname "$dest")"

    echo "Downloading $(( total / 1024 / 1024 )) MB over $PARTS connections"
    local each=$(( total / PARTS )) i first last
    for i in $(seq 0 $(( PARTS - 1 ))); do
        first=$(( i * each ))
        if [ "$i" -eq $(( PARTS - 1 )) ]; then last=$(( total - 1 )); else last=$(( first + each - 1 )); fi
        _fetch_part "$url" "$work/p$i" "$first" "$last" &
    done
    wait

    local bad=0 needed have
    for i in $(seq 0 $(( PARTS - 1 ))); do
        first=$(( i * each ))
        if [ "$i" -eq $(( PARTS - 1 )) ]; then last=$(( total - 1 )); else last=$(( first + each - 1 )); fi
        needed=$(( last - first + 1 ))
        have=$(size_of "$work/p$i")
        if [ "$have" -ne "$needed" ]; then
            echo "  part $i: $have / $needed  INCOMPLETE"
            bad=1
        fi
    done
    if [ "$bad" -eq 1 ]; then
        echo "Incomplete. Run again to resume."
        return 1
    fi

    cat $(for i in $(seq 0 $(( PARTS - 1 ))); do echo "$work/p$i"; done) > "$dest"

    local actual
    actual=$(size_of "$dest")
    if [ "$actual" -ne "$total" ]; then
        echo "Size mismatch: $actual / $total"
        return 1
    fi

    rm -rf "$work"
    return 0
}
