# -*- coding: utf-8 -*-
"""探索（事前登録外・確認的分析の後に実施 2026-11-24）：なぜ嘘があっても合意が真の割合からずれなかったか。

  python analysis/explore_mechanism.py analysis/data/dump_final_20261124.json

仕組み（backend/app.py negotiate）：調停AIの提案割合は「証拠で確定した事実だけ」を judicial_map.fault_ratio に
当てはめて決定論で計算する。証拠の経路は3つ：①本人のドラレコ提出（全争点が確定）、②相手方のドラレコ提出
（隠し抽選・全争点が確定）、③第三者記録（検出リスク条件で確率 0.8/0.2・本人の不利事実だけ確定）。
どれも出なければ提案は基本割合のままになる。主分析と同じ除外を適用する。
"""
import json
import sys

import pandas as pd

sys.path.insert(0, __import__("os").path.dirname(__file__))
from confirmatory import build, exclusions  # noqa: E402


def main(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    T, P, _ = exclusions(*build(items))
    game = {i["sessionId"]: i.get("game", {}) for i in items if i.get("sk") == "OUTCOME"}
    T = T.assign(third_party=T.sid.map(lambda s: bool(game[s].get("third_party_confirmed"))),
                 opp_dashcam=T.sid.map(lambda s: bool(game[s].get("opp_disclosed_during_negotiation"))))
    T["any_evidence"] = T.disclosed.astype(bool) | T.third_party | T.opp_dashcam
    D = T[T.cell != "innocent"]
    agg = dict(n=("pid", "size"), abs_dev=("abs_dev", "mean"), dev=("deviation", "mean"),
               exact=("abs_dev", lambda x: (x == 0).mean()))
    print("1. 証拠の有無と正確さ（全試行）")
    print(T.groupby("any_evidence").agg(**agg).round(2), "\n")
    print("2. ②③ 試行：嘘 × 本人のドラレコ提出")
    pd.set_option("display.width", 200)
    print(D.groupby(["deceive", "disclosed"]).agg(
        **agg, third_party=("third_party", "mean"), opp_dashcam=("opp_dashcam", "mean"),
        any_evidence=("any_evidence", "mean")).round(2), "\n")
    print("3. ②③ 試行：検出リスク条件ごとの証拠の出方（行動は同じでも証拠の量が違う）")
    print(D.groupby("high").agg(n=("pid", "size"), deceive=("deceive", "mean"),
                                disclosed=("disclosed", "mean"), third_party=("third_party", "mean"),
                                opp_dashcam=("opp_dashcam", "mean"), any_evidence=("any_evidence", "mean"),
                                abs_dev=("abs_dev", "mean")).round(2))


if __name__ == "__main__":
    main(sys.argv[1])
