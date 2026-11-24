#!/usr/bin/env bash
# study3 の被害者動画9本（3ジオメトリ × 3過失セル）からフレームを抜き、1枚のコンタクトシートにする。
# 生成・差し替えのたびに「HUD・信号・左側通行・残渣」を一目で確認するための検収用ツール。
#
# 使い方: bash simulation/check_videos.sh [秒数]   # 既定 4.0秒地点
#         出力 -> check_study3_videos.png（プロジェクト直下）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
AT="${1:-4.0}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 行=ジオメトリ、列=過失セル（①潔白 → ②混合 → ③主責）の順に並べる
i=0
for SC in A_intersection B_right_turn C_signal_crossing; do
  for SUF in _study3_innocent _study3 _study3_mainfault; do
    F="$ROOT/videos/${SC}${SUF}_victim.mp4"
    OUT="$TMP/$(printf %02d $i).png"
    if [ -f "$F" ]; then
      ffmpeg -y -v error -ss "$AT" -i "$F" -frames:v 1 "$OUT"
    else
      ffmpeg -y -v error -f lavfi -i color=c=gray:s=1280x720 -frames:v 1 "$OUT"
      echo "[warn] 欠番: ${SC}${SUF}_victim.mp4"
    fi
    i=$((i+1))
  done
done

montage -tile 3x3 -geometry +3+3 -background white "$TMP"/*.png \
  -resize 1500x "$ROOT/check_study3_videos.png"
echo "作成: check_study3_videos.png（行=A/B/C・列=①潔白/②混合/③主責・${AT}秒地点）"
