#!/usr/bin/env bash
# CARLA サーバを NVIDIA GPU 固定でヘッドレス起動する。
# Optimus ノート(Intel+NVIDIA)では Vulkan が Intel(Mesa) を掴んでクラッシュするため、
# Vk ICD を NVIDIA に固定し、Mesa のデバイス選択レイヤを無効化する。
set -u
CARLA_ROOT="${CARLA_ROOT:-$HOME/carla/CARLA_0.9.15}"
PORT="${1:-2000}"
# 第2引数で起動時マップ指定可（例: Town10HD＝非Opt・信号が set_state に反応・要動的ライティング）。
# runtime の load_world は本GPUで segfault するため、非Optで撮るには【起動時ロード】が必須。
START_MAP="${2:-}"
# 信号(エミッシブ)描画には Epic 品質が要る場合がある。QUALITY 環境変数で上書き可。
QUALITY="${QUALITY:-Low}"

export DISPLAY="${DISPLAY:-:1}"
# Vulkan を NVIDIA のみに固定
export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json
export __NV_PRIME_RENDER_OFFLOAD=1
export __GLX_VENDOR_LIBRARY_NAME=nvidia
export __VK_LAYER_NV_optimus=NVIDIA_only
# Mesa のデバイス選択実装レイヤを無効化（Intel を選ばせない）
export MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE=0
export DISABLE_LAYER_NV_OPTIMUS_1=1

pkill -9 -f CarlaUE4 2>/dev/null; sleep 3
nohup "$CARLA_ROOT/CarlaUE4.sh" ${START_MAP:+$START_MAP} -RenderOffScreen -nosound -quality-level="$QUALITY" \
  -carla-rpc-port="$PORT" > /tmp/carla_server.log 2>&1 &
echo "launched CARLA pid=$! port=$PORT map=${START_MAP:-default} quality=$QUALITY (NVIDIA固定)"
