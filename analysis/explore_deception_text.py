# -*- coding: utf-8 -*-
"""探索（事前登録外）：①「欺瞞」の中身の内訳と、見間違いの可能性 ②発言文の質的集計（受諾・開示）。

確認的分析（confirmatory.py）の定義・結果は変えない。ここは論文で「探索」と明記して報告する。

  python analysis/explore_deception_text.py analysis/data/dump_final_20261124.json
"""
import json
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

from confirmatory import build, exclusions


def load(path):
    with open(path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    T, P, _ = exclusions(*build(items))
    by = defaultdict(lambda: defaultdict(list))
    for it in items:
        sid = it.get("pk", "")[8:]
        by[sid][it.get("sk", "").split("#")[0] + ("#" + it["sk"].split("#")[1] if it.get("sk", "").startswith("SURVEY") else "")].append(it)
    return T, P, by


def perceived(by, sid):
    """映像を見た直後の理解度チェックで、相手の進入をどう見たか。"""
    s = by[sid]["SURVEY#perception"]
    v = (s[0]["responses"].get("pc_opp_entry") or "") if s else ""
    return "違反と見た" if "違反" in v else "わからない" if "わから" in v else "ルール通りと見た" if "従って" in v else "欠測"


def part1(T, by):
    print("=" * 70, "\n① 「欺瞞あり」の内訳（②③の試行）\n" + "=" * 70)
    rows = []
    for r in T[T.cell != "innocent"].itertuples():
        o = by[r.sid]["OUTCOME"][0]["game"]["deception"]["tally"]
        fc, dn = int(o["false_claim"]) > 0, int(o["denial_of_true"]) > 0
        rows.append({"sid": r.sid, "pid": r.pid, "cell": r.cell, "scenario": r.scenario[0], "high": r.high,
                     "false_claim": fc, "denial": dn, "abs_dev": r.abs_dev, "pc": perceived(by, r.sid)})
    D = pd.DataFrame(rows)
    D["any"] = D.false_claim | D.denial
    D["kind"] = D.apply(lambda x: "両方" if x.false_claim and x.denial else "相手の違反を言い立てた（実際は無い）のみ" if x.false_claim
                        else "自分の不利な事実を否認のみ" if x.denial else "なし", axis=1)
    print(f"試行 n={len(D)}、欺瞞あり {D['any'].sum()}（{D['any'].mean():.1%}）")
    print(D.kind.value_counts().to_string())
    print("\nセル別：", D.groupby("cell")[["false_claim", "denial", "any"]].mean().round(3).to_string(), sep="\n")
    print("\n相手の違反を言い立てた試行で、映像直後に相手の進入をどう見ていたか（シナリオ別）")
    print(pd.crosstab(D[D.false_claim].scenario, D[D.false_claim].pc, margins=True).to_string())
    print("\n（参考）言い立てなかった③の試行での見え方")
    m = D[(D.cell == "main_fault") & ~D.false_claim]
    print(pd.crosstab(m.scenario, m.pc, margins=True).to_string())

    # 定義を狭めたときの率（探索）
    D["knowing_fc"] = D.false_claim & (D.pc == "ルール通りと見た")
    D["strict"] = D.denial | D.knowing_fc
    print("\n定義別の欺瞞率（試行）と条件差（参加者単位の平均の差・Welch t）")
    from scipy import stats
    for col, lab in (("any", "登録した定義（どちらか）"), ("denial", "自分の不利な事実の否認"),
                     ("strict", "否認 or 『ルール通りと見た』うえでの言い立て")):
        pm = D.groupby(["pid", "high"])[col].mean().reset_index()
        h, l = pm[pm.high == 1][col], pm[pm.high == 0][col]
        t = stats.ttest_ind(h, l, equal_var=False)
        print(f"  {lab}: 全体 {D[col].mean():.1%}（{D[col].sum()}/{len(D)}）  High {D[D.high==1][col].mean():.1%} / Low {D[D.high==0][col].mean():.1%}"
              f"  t={t.statistic:.2f}, p={t.pvalue:.3f}")
    print("\n参加者のうち一度でも：", {c: int(D.groupby("pid")[c].max().sum()) for c in ("any", "denial", "strict")},
          f"／{D.pid.nunique()}人")
    print("\n種類別の |乖離|：", D.groupby("kind").abs_dev.agg(["size", "mean"]).round(2).to_string(), sep="\n")


def count(texts, pat):
    return sum(bool(re.search(pat, t)) for t in texts)


def part2(T, by):
    print("\n" + "=" * 70, "\n② 発言文の質的集計（キーワードによる機械的な分類。1発言が複数に該当しうる）\n" + "=" * 70)
    fin, dis = [], []
    for r in T.itertuples():
        ds = sorted(by[r.sid]["DECISION"], key=lambda x: int(x["round"]))
        said = [x for x in ds if (x.get("claim_text") or "").strip()]
        if said and r.resolution == "agreement":
            fin.append(said[-1]["claim_text"])
        first = next((x for x in ds if x.get("disclose_dashcam")), None)
        if first:
            dis.append((r.cell, int(first["round"]), (first.get("claim_text") or "").strip()))
    n = len(fin)
    print(f"合意で終わった試行の、参加者の最後の発言 n={n}")
    for lab, pat in (("受諾を明示（合意・受け入れ・受諾・同意・承諾）", "合意|受け入|受入|受諾|同意|承諾|手を打|飲み"),
                     ("　うち調停AIの案・勧告に言及", "調停|勧告|裁定"),
                     ("　うち相手方の案に言及", "相手(方|側)?の(案|条件|主張|割合|提案)"),
                     ("自分の非に言及して受諾", "非が|過失が大き|私にも|こちらにも|自車が|認めたうえ"),
                     ("早期解決に言及", "早期|早く")):
        c = count(fin, pat)
        print(f"  {lab}: {c}（{c/n:.0%}）")
    L = pd.Series([len(t) for t in fin])
    print(f"  文字数 中央値 {L.median():.0f}、20字以下 {(L<=20).mean():.0%}")

    print(f"\nドラレコを初めて出した手 n={len(dis)}")
    print("  出したラウンド：", dict(sorted(Counter(r for _, r, _ in dis).items())))
    print("  セル別の提出率：", T.groupby("cell").disclosed.mean().round(2).to_dict())
    tx = [t for _, _, t in dis]
    for lab, pat in (("発言なし（提出のみ）", "^$"), ("映像・証拠に言及", "映像|ドラ|レコーダー|証拠|記録"),
                     ("相手の落ち度を述べる", "相手|そちら|あちら|向こう"),
                     ("自分は守っていたと述べる", "守|制限速度(内|で)|青信号|安全運転|超過は(一切)?(な|あり)|違反(は|も)(な|し)"),
                     ("自分の非を認める", "超過して|認め|非が|私にも|こちらにも|自身の")):
        c = count(tx, pat)
        print(f"  {lab}: {c}（{c/len(tx):.0%}）")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    T, P, by = load(sys.argv[1])
    part1(T, by)
    part2(T, by)
