#!/usr/bin/env bash
# study3 過失構造3水準（①潔白 innocent / ②混合 mixed / ③主責 main_fault）×3ジオメトリ の
# 被害者ドラレコ動画を【広角FOV】で生成する（docs/実験デザイン_開示ゲーム.md §3.1・着脱方式）。
#
# --mods は judicial_map.active_true_modifiers(scenario, cell) から動的に取得（着脱で真値が決まる）:
#   innocent  : 相手の違反のみ（自分の過失OFF・速度超過HUDなし＝白）
#   mixed     : 両方（＝従来クリップ・速度超過HUDあり＝赤／C:自車黄）
#   main_fault: 自分の過失のみ（相手の違反OFF・C:相手は青）
# 出力（app._study3_video_url と一致）:
#   mixed      -> videos/{sc}_study3_victim.mp4          （既存を広角で差し替え）
#   innocent   -> videos/{sc}_study3_innocent_victim.mp4
#   main_fault -> videos/{sc}_study3_mainfault_victim.mp4
#
# 使い方（conda activate carla 済み前提）:
#   bash simulation/_gen_study3_cells.sh                  # 全 3x3
#   bash simulation/_gen_study3_cells.sh A_intersection   # 特定ジオメトリのみ
#   ONLY_CELL=mixed bash simulation/_gen_study3_cells.sh   # 特定セルのみ（検証用）
#   FOV=140 bash simulation/_gen_study3_cells.sh           # 画角を上書き
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../videos"
FOV="${FOV:-130}"
export PYTHONPATH="$HERE/../backend:${PYTHONPATH:-}"

SCENARIOS=(A_intersection B_right_turn C_signal_crossing)
[ $# -gt 0 ] && SCENARIOS=("$@")
CELLS=(innocent mixed main_fault)
[ -n "${ONLY_CELL:-}" ] && CELLS=("$ONLY_CELL")
declare -A SUFFIX=( [mixed]=_study3 [innocent]=_study3_innocent [main_fault]=_study3_mainfault )

mods_for() {  # scenario cell -> "key1,key2"（着脱後に実際に起きた修正要素）
  python -c "import sys,judicial_map as J;print(','.join(J.active_true_modifiers(sys.argv[1],sys.argv[2])))" "$1" "$2"
}

pkill -9 -f CarlaUE4 2>/dev/null || true; sleep 8
bash "$HERE/launch_carla.sh" 2000
for i in $(seq 1 30); do
  sleep 5
  if (exec 3<>/dev/tcp/127.0.0.1/2000) 2>/dev/null; then exec 3>&-; echo "port open ($((i*5))s)"; sleep 5; break; fi
done

for SC in "${SCENARIOS[@]}"; do
  for CELL in "${CELLS[@]}"; do
    MODS="$(mods_for "$SC" "$CELL")"
    SUF="${SUFFIX[$CELL]}"
    echo "== $SC / $CELL (mods=$MODS, fov=$FOV) -> ${SC}${SUF}_victim.mp4 =="
    python "$HERE/generate_accident.py" --scenario "$SC" --mods "$MODS" --fov "$FOV" --out "$OUT" \
      --fault-cell "$CELL"
    # 撮影地 Town04 (256,-248) は全方向一時停止で、自車レーンの路面に "STOP" が描かれている。
    # シナリオAだけは「自車が優先道路」（判タ図107）なので画面と設定が矛盾する。
    # B は「直進車が優先」という一般原則の記述で、全方向一時停止でも直進車は曲がる車に優先するため
    # 矛盾しない。C は信号交差点なので無関係。よって【A のみ】文字を消す。
    # CARLA側で消す手は全滅しているため（ToDo参照）、生フレームの段階で後処理する。
    if [ "$SC" = "A_intersection" ]; then
      echo "   路面の文字を消去（優先道路シナリオのため）"
      # ★conda の carla 環境(py3.8)には scipy が無い。ここで python3 をそのまま使うと
      #   「scipy が要る」で即失敗する。scipy/numpy/PIL が揃うインタプリタを選ぶ。
      ERASE_PY="python3"
      if ! "$ERASE_PY" -c "import scipy, numpy, PIL" >/dev/null 2>&1; then
        ERASE_PY="/usr/bin/python3"
      fi
      if ! "$ERASE_PY" -c "import scipy, numpy, PIL" >/dev/null 2>&1; then
        echo "[fatal] scipy/numpy/PIL が揃う python が無い。消さないまま配信すると"
        echo "        「自車が優先道路」の設定と画面が矛盾するので中止する。"
        exit 1
      fi
      # ★失敗を握り潰さないこと。消えていない動画をそのまま配信するのが最悪の結果。
      "$ERASE_PY" "$HERE/erase_road_text.py" "$OUT/_frames_rhd/$SC/victim" || exit 1
      if ls "$OUT/_frames_rhd/$SC/opponent"/*.png >/dev/null 2>&1; then
        "$ERASE_PY" "$HERE/erase_road_text.py" "$OUT/_frames_rhd/$SC/opponent" || exit 1
      fi
    fi
    bash "$HERE/encode.sh" "$SC" "$OUT" "$SUF"
  done
done

pkill -9 -f CarlaUE4 2>/dev/null || true
echo "== study3 3水準 生成完了（fov=$FOV）=="
ls -lh "$OUT"/*_study3*_victim.mp4
