# -*- coding: utf-8 -*-
"""
CARLA 映像生成用シナリオ定義（幾何・車両・シミュレーション設定）。

法的な定義（基本過失割合・修正要素・非対称）は backend/judicial_map.py が単一の真実源。
本ファイルはそこに「CARLAでどう撮るか」の情報だけを足す:
  - kind: 交差形態（crossing=出会い頭 / direct_vs_turn=直進vs右左折）
  - junction_type: 信号あり/なし（交差点選択のフィルタ。judicial_mapと一致）
  - vehicles: 車両ブループリント・色・基準速度
  - sim: fps・長さ・衝突時刻

修正要素は --mods で judicial_map のキー（opp_stop_violation 等）を渡す。
"""
import os
import sys

# backend/judicial_map.py を参照（法的定義の単一の真実源）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from judicial_map import JUDICIAL_SCENARIOS  # noqa: E402


# CARLA幾何・撮影パラメータ（judicial_map に無い情報だけ）
_SIM = {
    "A_intersection": {
        "kind": "crossing",
        # Town04 の無信号交差点(256,-248)=jid53 を狙い撮り（全マップ全数プローブで
        # A/B とも gap<1.5m の唯一の適地。A_gap=0.13m）。
        "junction_xy": (256.0, -248.0),
        "vehicles": {
            "victim":   {"blueprint": "vehicle.toyota.prius", "color": "200,200,210", "speed_mps": 11.0},
            "opponent": {"blueprint": "vehicle.audi.tt",      "color": "40,40,45",    "speed_mps": 11.0},
        },
        "sim": {"fps": 20, "duration_s": 6.0, "collision_at_s": 5.2},
    },
    "B_right_turn": {
        "kind": "direct_vs_turn",
        "junction_xy": (256.0, -248.0),   # 同一の無信号交差点(B_gap=0.30m)
        "vehicles": {
            "victim":   {"blueprint": "vehicle.toyota.prius", "color": "200,200,210", "speed_mps": 12.0},
            "opponent": {"blueprint": "vehicle.audi.tt",      "color": "40,40,45",    "speed_mps": 7.0},
        },
        "sim": {"fps": 20, "duration_s": 6.0, "collision_at_s": 5.0},
    },
    "C_signal_crossing": {
        "kind": "crossing",
        # Town10HD_Opt の信号あり4差路 jid=189 中心(-48,20) を狙い撮り
        # （probe_signalized.py で出会い頭 gap=0.02m・victim/opponent 双方の正対信号取得可を確認）。
        "junction_xy": (-48.0, 20.0),
        "vehicles": {
            "victim":   {"blueprint": "vehicle.toyota.prius", "color": "200,200,210", "speed_mps": 11.0},
            "opponent": {"blueprint": "vehicle.audi.tt",      "color": "40,40,45",    "speed_mps": 11.0},
        },
        "sim": {"fps": 20, "duration_s": 6.0, "collision_at_s": 5.2},
    },
}


# 撮影マップ。信号なしシナリオ(A/B)は「信号機が実在しない交差点」で撮る。
# 全マップ全数プローブ(probe_junctions.py)の結果、A(出会い頭)/B(対向右折)とも
# gap<1.5m で綺麗に衝突する無信号交差点は Town04 (256,-248)=jid53 が唯一。
# → A/B は Town04(消灯ではなく本物の無信号交差点＝滅灯問題を解消)。
# C(信号あり)は信号機が要るため Town10HD_Opt のまま。
# 住宅街の見た目が要件になれば Town07(郊外マップ・未導入)の追加導入→probeが本筋。
_MAP = {"A_intersection": "Town04", "B_right_turn": "Town04",
        "C_signal_crossing": "Town10HD_Opt"}


def _build():
    """judicial_map と CARLA幾何をマージした完全なシナリオ定義を返す。"""
    out = {}
    for sid, sim in _SIM.items():
        j = JUDICIAL_SCENARIOS[sid]
        out[sid] = {
            "id": sid,
            "map": _MAP.get(sid, "Town10HD_Opt"),
            "kind": sim["kind"],
            "junction_type": j["junction_type"],   # judicial_map と一致
            "junction_xy": sim.get("junction_xy"),  # 狙い撮りする交差点中心（無ければ自動選択）
            "figure": j["figure"],
            "title": j["title"],
            "base_ratio": j["base_ratio"],
            "vehicles": sim["vehicles"],
            "sim": sim["sim"],
        }
    return out


SCENARIOS = _build()
