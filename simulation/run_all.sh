#!/usr/bin/env bash
# CARLA サーバをヘッドレス起動し、事故シナリオ2種の2視点動画を生成→MP4化する。
# 前提: conda env `carla`(py3.8) / CARLA_ROOT に 0.9.15 展開済み / ffmpeg
set -euo pipefail

CARLA_ROOT="${CARLA_ROOT:-$HOME/carla/CARLA_0.9.15}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../videos"
FPS=20
mkdir -p "$OUT"

echo "== CARLA サーバ起動（NVIDIA固定・ヘッドレス。Optimus機のIntel誤掴みクラッシュ回避） =="
# 素の CarlaUE4.sh 直起動は Vulkan が Intel GPU を掴んでクラッシュするため、
# NVIDIA 固定の launch_carla.sh 経由で起動する（全撮影が成功した実績のある起動法）。
bash "$HERE/launch_carla.sh" 2000
trap 'pkill -9 -f CarlaUE4 2>/dev/null || true' EXIT

echo "   サーバ起動待ち..."
for i in $(seq 1 60); do
  if python - <<'PY' 2>/dev/null
import carla; carla.Client("127.0.0.1",2000).get_server_version()
PY
  then echo "   接続OK"; break; fi
  sleep 2
done

for SC in A_intersection B_right_turn; do
  echo "== 生成: $SC =="
  python "$HERE/generate_accident.py" --scenario "$SC" --out "$OUT"
  for VP in victim opponent; do
    DIR="$OUT/_frames_rhd/$SC/$VP"
    if ls "$DIR"/*.png >/dev/null 2>&1; then
      echo "   MP4化: $SC/$VP"
      ffmpeg -y -framerate $FPS -pattern_type glob -i "$DIR/*.png" \
        -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart \
        "$OUT/${SC}_${VP}.mp4" -loglevel error
    fi
  done
done

echo "== 完了。生成された動画 =="
ls -lh "$OUT"/*.mp4 2>/dev/null || echo "（MP4なし。ログ確認）"
