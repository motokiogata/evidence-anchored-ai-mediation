# -*- coding: utf-8 -*-
"""探索（事前登録外）：人は証拠に基づく調停を受け入れたか。嘘が露見した人はその後どうしたか。

  python analysis/explore_acceptance.py analysis/data/dump_final_20261124.json

主分析と同じ除外を適用。②③（不利な事実がある試行）を、嘘なし／嘘あり・露見なし／嘘あり・露見 に分ける。
"""
import json
import sys
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, __import__("os").path.dirname(__file__))
from confirmatory import build, exclusions, num  # noqa: E402

pd.set_option("display.width", 220)


def main(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    T, P, _ = exclusions(*build(items))
    by = defaultdict(lambda: defaultdict(list))
    for it in items:
        sid, sk = it.get("pk", "")[8:], it.get("sk", "")
        for pre in ("OUTCOME", "DECISION#", "EVIDENCE#", "EXPOSURE#", "SURVEY#round", "SURVEY#post_trial", "SURVEY#t2"):
            if sk.startswith(pre):
                by[sid][pre].append(it)

    def row(sid):
        b = by[sid]
        o = b["OUTCOME"][0]; g = o.get("game", {})
        dec = sorted(b["DECISION#"], key=lambda x: int(x["round"]))
        ev_rounds = [int(e["round"]) for e in b["EVIDENCE#"]] + \
                    [int(d["round"]) for d in dec if d.get("disclose_dashcam")]
        first_ev = min(ev_rounds) if ev_rounds else np.nan
        rounds = num(o.get("rounds")) or len(dec)
        rs = sorted(b["SURVEY#round"], key=lambda x: float(x["ts"]))
        last = rs[-1]["responses"] if rs else {}
        exposed = any(e.get("speaker") == "victim" for e in b["EXPOSURE#"])
        post = b["SURVEY#post_trial"][0]["responses"] if b["SURVEY#post_trial"] else {}
        return pd.Series({
            "exposed": exposed, "rounds": rounds, "first_ev": first_ev,
            "rounds_after_ev": rounds - first_ev if ev_rounds else np.nan,
            "anger_last": num(last.get("emo_anger")), "satis_last": num(last.get("emo_satisfaction")),
            "sr_deny": post.get("sr_deny"), "agree": g.get("resolution") == "agreement",
        })

    T = pd.concat([T.reset_index(drop=True), T.sid.apply(row).reset_index(drop=True)], axis=1)
    D = T[T.cell != "innocent"].copy()
    D["group"] = np.where(D.deceive == 0, "1 嘘なし", np.where(D.exposed, "3 嘘あり・露見", "2 嘘あり・露見なし"))

    print("A. ②③ の試行：嘘と露見ごとの決着")
    print(D.groupby("group").agg(n=("pid", "size"), 合意率=("agree", "mean"), ラウンド数=("rounds", "mean"),
                                 ずれ絶対値=("abs_dev", "mean"), 完全一致=("abs_dev", lambda x: (x == 0).mean()),
                                 感情の回答数=("anger_last", "count"), 最終ラウンドの怒り=("anger_last", "mean"),
                                 最終ラウンドの満足=("satis_last", "mean")).round(2), "\n")

    E = T[T.first_ev.notna()]
    print("B. 証拠が出た後（全試行）")
    print(f"  証拠が出た試行 {len(E)}：合意 {E.agree.mean():.1%}、証拠が出てから決着まで 平均 {E.rounds_after_ev.mean():.2f} ラウンド"
          f"（中央値 {E.rounds_after_ev.median():.0f}）、真の割合と完全一致 {(E.abs_dev == 0).mean():.1%}")
    N = T[T.first_ev.isna()]
    print(f"  証拠が出なかった試行 {len(N)}：合意 {N.agree.mean():.1%}、平均 {N.rounds.mean():.2f} ラウンド\n")

    # C. 参加者レベル：一度でも嘘が露見した人 vs それ以外（t2）
    t2 = {}
    meta = {i["sessionId"]: i for i in items if i.get("sk") == "META"}
    for sid, b in by.items():
        if b["SURVEY#t2"] and sid in meta:
            t2[(meta[sid].get("condition") or {}).get("participant_id")] = b["SURVEY#t2"][0]["responses"]
    pe = D.groupby("pid").agg(ever_exposed=("exposed", "max"), ever_lied=("deceive", "max")).reset_index()
    pe = P.merge(pe, on="pid")
    pe["trust"] = pe.pid.map(lambda p: np.nanmean([num(t2[p].get("trust_ai1")) or np.nan, num(t2[p].get("trust_ai2")) or np.nan]))
    pe["accept"] = pe.pid.map(lambda p: np.nanmean([num(t2[p].get("ai1")) or np.nan, num(t2[p].get("ai2")) or np.nan]))
    pe["grp"] = np.where(pe.ever_exposed, "3 嘘が露見した", np.where(pe.ever_lied == 1, "2 嘘あり・露見なし", "1 嘘なし"))
    print("C. 参加者ごと（事後アンケート・7点満点）")
    print(pe.groupby("grp").agg(n=("pid", "size"), 手続きの満足=("svi_p", "mean"), AIへの信頼=("trust", "mean"),
                                受け入れ意向=("accept", "mean")).round(2))
    a, b = pe[pe.ever_exposed], pe[~pe.ever_exposed]
    for v, lab in (("svi_p", "手続きの満足"), ("trust", "AIへの信頼"), ("accept", "受け入れ意向")):
        t = stats.ttest_ind(a[v].dropna(), b[v].dropna(), equal_var=False)
        sd = np.sqrt((a[v].var() + b[v].var()) / 2)
        print(f"  露見した人 vs それ以外 {lab}: 差 {a[v].mean() - b[v].mean():+.2f}, d={(a[v].mean() - b[v].mean()) / sd:+.2f}, p={t.pvalue:.3f}")

    print("\nD. 自己申告（各試行の直後）と、記録から判定した嘘の対応（②③）")
    print(pd.crosstab(D.sr_deny, D.deceive.map({0: "嘘なし", 1: "嘘あり"}), margins=True))


if __name__ == "__main__":
    main(sys.argv[1])
