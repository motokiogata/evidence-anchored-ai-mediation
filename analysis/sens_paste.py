# -*- coding: utf-8 -*-
"""登録した感度分析：貼り付けの試行があった参加者を除いた版（元の除外規則）。

OSF 登録本文（https://osf.io/m5qhz/ ・Data inclusion and exclusion の項目8）は
「元の規則（貼り付け試行のある参加者を除外）での結果も感度分析として報告する」と定めている。
confirmatory.py の主分析・既存の感度分析は変えず、同じ関数をそのまま使う。

  python analysis/sens_paste.py analysis/data/dump_final_20261124.json
"""
import json
import sys

from confirmatory import build, exclusions, h1, h2, h3

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    T, P = build(data.get("items", data))
    keep = P[~(P.paste > 0)]
    print(f"貼り付け試行あり {int((P[P.completed].paste > 0).sum())} 人を除外（完了者 {int(P.completed.sum())} 人中）")
    T, P, log = exclusions(T[T.pid.isin(keep.pid)], keep)
    for k, v in log:
        print(f"  {k:<38}{v:>5}")
    print(f"  条件 High/Low = {int(P.high.sum())}/{int((1 - P.high).sum())}")
    nz = T[T.cell != "innocent"]
    print(f"\n[H1] 欺瞞率（②③ n={len(nz)}）High {nz[nz.high == 1].deceive.mean():.3f} / Low {nz[nz.high == 0].deceive.mean():.3f}")
    try:
        r = h1(T, P)
        print(f"     GLMM OR(High)={r['OR']:.2f} [{r['CI'][0]:.2f}, {r['CI'][1]:.2f}], p={r['p']:.4f}")
    except Exception as e:  # noqa: BLE001  小標本でモデルが推定できない場合は、その旨をそのまま報告する
        print(f"     登録した GLMM は推定不能（{type(e).__name__}: {e}）。Fisher の正確検定（試行単位・参考）で代える。")
        from scipy.stats import fisher_exact
        import pandas as pd
        tab = pd.crosstab(nz.high, nz.deceive)
        o, pv = fisher_exact(tab.to_numpy())
        print(f"     OR={o:.2f}, p={pv:.4f}  表（行=Low/High, 列=欺瞞なし/あり）{tab.to_numpy().tolist()}")
    r2 = h2(T)
    print(f"[H2] 乖離 b(High)={r2['b']:.2f} [{r2['CI'][0]:.2f}, {r2['CI'][1]:.2f}], p={r2['p']:.4f}／|乖離| b={r2['abs_b']:.2f}, p={r2['abs_p']:.4f}")
    r3 = h3(T)
    print("[H3a] 3係数の同時 Wald p=%.4f／" % r3["wald_p"] + str({k: v for k, v in r3.items() if k != "wald_p"})[:600])
