# -*- coding: utf-8 -*-
"""探索（事前登録外）：嘘の基準を変えても「嘘があっても結果は歪まない／露見しても合意する」が成り立つか。

基準は3つ：実験時の抽出（Sonnet 4.6）、Opus 5.5、GPT（＝明言した嘘）。露見は実験中に実際に表示されたもの
（実験時の抽出に基づく）なので、厳しい基準では「嘘とは言えないのに露見と表示された試行」も数える。

  python analysis/explore_by_coder.py analysis/data/dump_final_20261124.json
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import judicial_map as JM
from confirmatory import build, exclusions, num
from recode_llm import units

HERE = os.path.dirname(__file__)


def main(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    T, _, _ = exclusions(*build(items))
    by = defaultdict(lambda: defaultdict(list))
    for it in items:
        sid, sk = it.get("pk", "")[8:], it.get("sk", "")
        for pre in ("OUTCOME", "DECISION#", "EVIDENCE#", "EXPOSURE#", "SURVEY#round"):
            if sk.startswith(pre):
                by[sid][pre].append(it)
    D = T[T.cell != "innocent"].copy()
    D["exposed"] = D.sid.map(lambda s: any(e.get("speaker") == "victim" for e in by[s]["EXPOSURE#"]))
    D["agree"] = D.resolution == "agreement"
    D["evidence"] = D.sid.map(lambda s: bool(by[s]["EVIDENCE#"]) or any(d.get("disclose_dashcam") for d in by[s]["DECISION#"]))

    def satis(s):
        rs = sorted(by[s]["SURVEY#round"], key=lambda x: float(x["ts"]))
        return num(rs[-1]["responses"].get("emo_satisfaction")) if rs else np.nan
    D["satis_last"] = D.sid.map(satis)

    us = [u for u in units(path) if u["text"] and u["sid"] in set(D.sid)]
    recs = {n: json.load(open(os.path.join(HERE, "data", f"recode_{n}.json"), encoding="utf-8")) for n in ("opus55", "gpt")}
    coders = {"実験時 Sonnet 4.6": lambda u: u["stances"], "Opus 5.5": lambda u: recs["opus55"].get(u["id"]),
              "GPT（明言した嘘）": lambda u: recs["gpt"].get(u["id"])}
    for name, fn in coders.items():
        lied = defaultdict(int)
        for u in us:
            facts = {f["key"]: f for f in JM.game_facts(u["scenario"], fault_cell=u["cell"])}
            if any(JM.classify_deception(facts[k], "victim", st) in ("false_claim", "denial_of_true")
                   for k, st in (fn(u) or {}).items() if k in facts):
                lied[u["sid"]] = 1
        D["lied"] = D.sid.map(lied).fillna(0).astype(int)
        D["group"] = np.where(D.lied == 0, np.where(D.exposed, "1b 嘘なし・露見表示あり", "1a 嘘なし"),
                              np.where(D.exposed, "3 嘘あり・露見", "2 嘘あり・露見なし"))
        print(f"\n===== 基準：{name}（嘘あり {D.lied.sum()}/{len(D)} 試行） =====")
        print(D.groupby("group").agg(n=("pid", "size"), 合意率=("agree", "mean"), ずれ絶対値=("abs_dev", "mean"),
                                     完全一致=("abs_dev", lambda x: (x == 0).mean()),
                                     最終ラウンドの満足=("satis_last", "mean")).round(2).to_string())
        print("証拠の有無 × 嘘：|乖離| の平均（n）")
        print(D.groupby(["evidence", "lied"]).abs_dev.agg(["size", "mean"]).round(2).to_string())


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
