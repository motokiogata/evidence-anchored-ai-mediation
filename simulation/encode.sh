#!/usr/bin/env bash
# 生成済みフレームを MP4 化する。
#
# 2026-09 改訂（専門家フィードバック対応）。旧版からの変更点:
#  1. 【左側通行化をパイプラインに内蔵】生フレームを hflip してから HUD を焼く。
#     従来は完成mp4に delogo+hflip+再オーバーレイを後がけしていたため、ぼかし跡の灰色矩形と
#     鏡像ゴーストが残っていた。生フレームの時点で反転すれば残渣は原理的に発生しない。
#  2. 【HUDを左上へ】旧HUDは左下(y=620)で、ブラウザの <video controls> の操作バー
#     （左端＝経過時間表示）と重なって読めなかった。
#  3. 【制限速度を併記】旧HUDは「67 km/h」だけで、制限速度が画面のどこにも無かった＝
#     読めても超過だと判断できなかった。開示ゲームは「自分のタイプを知っている」ことが前提なので致命的。
#     制限速度は judicial_map.speed_limit_kmh（単一の真実源）から取得する。
#  4. 【速度の文字色を一定に】旧版は超過時に赤字＝「あなたが悪い」という需要特性を与えていた。
#     事実は数字で読ませ、色では誘導しない。信号だけはランプの色が事実そのものなので色を残す。
#  5. 【文字切れの解消】"SIGNAL YELLOW" が黒帯からはみ出ていた。日本語表記＋帯幅を見直した。
#
# 使い方: ./encode.sh <scenario> <out_dir> [suffix]
set -euo pipefail
SC="$1"; OUT="$2"; SUF="${3:-}"
FPS=20
HERE="$(cd "$(dirname "$0")" && pwd)"
FONT="${HUD_FONT:-/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc}"
# 左側通行（日本仕様）へ反転するか。CARLAの町は右側通行なので既定で反転する。
HFLIP="${HFLIP:-1}"

# 制限速度は judicial_map が単一の真実源（HUD・私的情報テキスト・エージェント発言で同じ値を使う）
LIMIT="$(PYTHONPATH="$HERE/../backend:${PYTHONPATH:-}" python -c \
  "import judicial_map as J;print(J.JUDICIAL_SCENARIOS['$SC']['speed_limit_kmh'])")"

# HUD レイアウト（1280x720 の左上。操作バーとは反対側）
BOX_X=24; BOX_W=430
SPD_Y=24;  SPD_H=112
SIG_Y=148; SIG_H=76

for VP in victim opponent; do
  DIR="$OUT/_frames_rhd/$SC/$VP"
  ls "$DIR"/*.png >/dev/null 2>&1 || { echo "skip $SC/$VP (no frames)"; continue; }
  KMH=$(cat "$DIR/speed_kmh.txt" 2>/dev/null || echo 0)

  VF=""
  [ "$HFLIP" = "1" ] && VF="hflip,"
  # 速度パネル: 制限（小さめ・淡色）と現在速度（大きめ・白）を対比させる。
  VF="${VF}drawbox=x=${BOX_X}:y=${SPD_Y}:w=${BOX_W}:h=${SPD_H}:color=black@0.62:t=fill"
  VF="${VF},drawtext=fontfile='${FONT}':text='制限速度 ${LIMIT} km/h':x=$((BOX_X+22)):y=$((SPD_Y+14)):fontsize=30:fontcolor=0xD0D0D0"
  VF="${VF},drawtext=fontfile='${FONT}':text='速度 ${KMH} km/h':x=$((BOX_X+22)):y=$((SPD_Y+56)):fontsize=42:fontcolor=white"

  SIGMSG=""
  if [ -f "$DIR/signal_state.txt" ]; then
    STATE=$(cat "$DIR/signal_state.txt")
    case "$STATE" in
      GREEN)  SIGCOL=0x33DD33; SIGJA="青" ;;
      YELLOW) SIGCOL=0xFFD000; SIGJA="黄" ;;
      RED)    SIGCOL=0xFF3030; SIGJA="赤" ;;
      *)      SIGCOL=white;    SIGJA="?"  ;;
    esac
    # 信号パネル: ランプ（色つき四角）＋「前方信号 ○」。色は事実そのものなので残す。
    VF="${VF},drawbox=x=${BOX_X}:y=${SIG_Y}:w=${BOX_W}:h=${SIG_H}:color=black@0.62:t=fill"
    VF="${VF},drawbox=x=$((BOX_X+18)):y=$((SIG_Y+18)):w=40:h=40:color=${SIGCOL}:t=fill"
    VF="${VF},drawtext=fontfile='${FONT}':text='前方信号 ${SIGJA}':x=$((BOX_X+74)):y=$((SIG_Y+16)):fontsize=40:fontcolor=${SIGCOL}"
    SIGMSG=" +signal(${STATE}/${SIGJA})"
  fi

  ffmpeg -y -framerate $FPS -i "$DIR/%04d.png" \
    -vf "$VF" \
    -c:v libx264 -pix_fmt yuv420p -crf 20 -movflags +faststart \
    "$OUT/${SC}${SUF}_${VP}.mp4" -loglevel error
  echo "encoded ${SC}${SUF}_${VP}.mp4 (制限${LIMIT} / 速度${KMH}${SIGMSG}${HFLIP:+ hflip=$HFLIP})"
done
