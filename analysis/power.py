# -*- coding: utf-8 -*-
"""
検定力分析（G*Power 相当）。研究1/2/3の計画に必要なサンプルサイズを算出する。

- ANOVA主効果・交互作用: statsmodels FTestAnovaPower
- 2群比率(合意率・欺瞞率など): 正規近似の効果量 h
実行: conda activate sam-app; python power.py
"""
import math
from statsmodels.stats.power import FTestAnovaPower, NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize


def anova_n(k_groups, f, power=0.80, alpha=0.05):
    """一元ANOVA相当で総サンプルN（効果量 Cohen's f）。"""
    analysis = FTestAnovaPower()
    n = analysis.solve_power(effect_size=f, k_groups=k_groups, alpha=alpha, power=power)
    return math.ceil(n)


def factorial_n(cells, f, power=0.80, alpha=0.05):
    """要因計画の交互作用/主効果の目安（自由度をk_groups=cellsで近似）。"""
    return anova_n(cells, f, power, alpha)


def twoprop_n(p1, p2, power=0.80, alpha=0.05):
    """2群比率差に必要な各群N（合意率・欺瞞率など）。"""
    h = proportion_effectsize(p1, p2)
    n = NormalIndPower().solve_power(effect_size=abs(h), alpha=alpha, power=power,
                                     ratio=1, alternative="two-sided")
    return math.ceil(n), round(h, 3)


def line(label, n_total, per_cell=None):
    extra = f"（1セル {per_cell}）" if per_cell else ""
    print(f"  {label:<48} 総N ≈ {n_total}{extra}")


def main():
    print("=" * 74)
    print("検定力分析（power=.80, α=.05）")
    print("=" * 74)

    print("\n■ 研究1 ヴィネット（提示4 × 起草2 = 8セル・被験者間）")
    for f, tag in [(0.25, "中(f=.25)"), (0.15, "小(f=.15)")]:
        n = factorial_n(8, f)
        line(f"交互作用/群差 {tag}", n, math.ceil(n / 8))

    print("\n■ 研究2 対話型（支援3 × 戦略2 = 6セル・被験者間）")
    for f, tag in [(0.25, "中"), (0.15, "小")]:
        n = factorial_n(6, f)
        line(f"支援×戦略 交互作用 {tag}", n, math.ceil(n / 6))
    for p1, p2 in [(0.5, 0.7), (0.5, 0.65)]:
        n, h = twoprop_n(p1, p2)
        line(f"合意率 {int(p1*100)}%→{int(p2*100)}% (h={h})", n * 2, n)

    print("\n■ 研究3 203x示談プラットフォーム（検出リスク 高 vs 低 = 2群・被験者間）")
    print("  欺瞞率の群間比較（検出リスク 高[監視多] vs 低[監視少], 各群N）:")
    for p1, p2 in [(0.5, 0.25), (0.4, 0.2), (0.5, 0.3)]:
        n, h = twoprop_n(p1, p2)
        line(f"    欺瞞 {int(p1*100)}%→{int(p2*100)}% (h={h})", n * 2, n)
    print("  結果乖離（連続DV）の2群 t 検定 各群N:")
    for f, tag in [(0.5, "大"), (0.35, "中"), (0.2, "小")]:
        # Cohen's d ≒ 2f。2群t検定の各群N近似（power=.80,α=.05,両側）は 16/d^2 + 1。
        d = 2 * f
        per = math.ceil(16 / (d * d)) + 1
        line(f"    乖離 d={round(d,2)} ({tag})", per * 2, per)

    print("\n" + "=" * 74)
    print("推奨: 研究3は『検出リスク 高 vs 低』の2群比較。欺瞞率のような比率DVは効果が大きい傾向で")
    print("各群30〜60、結果乖離のような連続DVは各群〜90を目安に。")
    print("開示率も主要DV（2群の比率差）として同様に評価。")
    print("最終確定は本番のパイロットで効果量を推定してから（プレレジ）。")


if __name__ == "__main__":
    main()
