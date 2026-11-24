# -*- coding: utf-8 -*-
"""探索（事前登録外・記述）：相手方の代理AIは事実と違うことを述べたか。

  python analysis/explore_opponent.py analysis/data/dump_final_20261124.json opp_opus55 [--opponent は不要]

実験中、相手方の発言は LLM で分類していない（露見は証拠の確定から機械的に記録）。
ここでは recode_llm.py --opponent で別の LLM が付けたスタンスを、参加者と同じ決定論の規則
（judicial_map.classify_deception）で地上真実に照合する。主分析と同じ除外を適用。
"""
import json
import os
import sys
from collections import Counter, defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv.append("--opponent")
sys.path.insert(0, HERE)
import recode_llm as R  # noqa: E402
from confirmatory import build, exclusions  # noqa: E402

JM = R.JM


def main(path, name):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    T, _, _ = exclusions(*build(items))
    keep = T.set_index("sid")
    rec = json.load(open(os.path.join(HERE, "data", f"recode_{name}.json"), encoding="utf-8"))
    us = [u for u in R.units(path) if u["text"] and u["sid"] in keep.index]
    print(f"相手方の発言 {len(us)} 件（主分析の {len(keep)} 試行）／コーディング済み {sum(u['id'] in rec for u in us)}")
    kinds, per = Counter(), defaultdict(Counter)
    for u in us:
        facts = {f["key"]: f for f in JM.game_facts(u["scenario"], fault_cell=u["cell"])}
        ks = {JM.classify_deception(facts[k], "opponent", st) for k, st in (rec.get(u["id"]) or {}).items() if k in facts}
        for k in ks:
            kinds[k] += 1
            per[u["sid"]][k] += 1
        per[u["sid"]]["n"] += 1
    print("発言単位（1発言が複数に該当しうる）:", {k: f"{v}（{v / len(us):.1%}）" for k, v in kinds.most_common()})
    D = pd.DataFrame({"sid": list(per)})
    D["cell"] = D.sid.map(keep.cell)
    D["high"] = D.sid.map(keep.high)
    D["abs_dev"] = D.sid.map(keep.abs_dev)
    for k in ("false_claim", "denial_of_true", "concession"):
        D[k] = D.sid.map(lambda s: per[s][k] > 0).astype(int)
    D["lied"] = ((D.false_claim + D.denial_of_true) > 0).astype(int)
    print("\n試行単位：相手方AIが1回以上（割合）")
    print(D.groupby("cell")[["lied", "false_claim", "denial_of_true", "concession"]].mean().round(2).assign(n=D.groupby("cell").size()))
    print("全体:", D[["lied", "false_claim", "denial_of_true", "concession"]].mean().round(3).to_dict())
    print("条件別（Low=0/High=1）:", D.groupby("high").lied.mean().round(3).to_dict())
    ev = D.sid.map(lambda s: bool(keep.loc[s].get("any_evidence"))) if "any_evidence" in keep else None
    print("\n相手方AIの嘘の有無と結果（|乖離| の平均, n）")
    print(D.groupby("lied").abs_dev.agg(["mean", "size"]).round(2))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
