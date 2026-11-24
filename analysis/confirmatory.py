# -*- coding: utf-8 -*-
"""研究3 確認的分析（OSF 事前登録 2026-11-24 の §5 をそのまま実装する）。

  python analysis/confirmatory.py dump.json            # 盲検（条件ラベルを参加者単位でシャッフル）
  python analysis/confirmatory.py dump.json --unblind  # 本番（募集終了後に1回だけ）

盲検モードは、スクリプトの動作確認用。条件ラベルを参加者単位で無作為に入れ替えるので、
条件の効果は出ない（出ても偶然）。**募集を締めるまでは --unblind を付けない**（プレレジ 3.5：中間解析なし）。

対応（プレレジ §5）：
  H1  any-deception（試行・②混合と③主責のみ＝3.4）を GLMM ロジット（参加者ランダム切片）。
      lme4 が無いので、ランダム切片の周辺尤度を Gauss–Hermite 求積（25点）で直接最尤推定する
      （lme4 の nAGQ>1 と同じ推定量）。頑健性：参加者レベル（2試行中に欺瞞あり）ロジスティック回帰。
      補助：欺瞞件数の負の二項回帰（参加者クラスタ頑健SE）。
  H2  deviation_from_truth（試行）を LMM（参加者ランダム切片）。媒介：条件→欺瞞→乖離、
      参加者単位のクラスタ・ブートストラップ 5,000 回、バイアス修正 CI。
  H3a disclosed_dashcam を sf_own_fault＋sf_opp_fault＋sf_evidence_favor＋条件＋共変量でロジスティック回帰
      （試行単位・参加者クラスタ頑健SE）。確認的検定は自己奉仕3係数の同時 Wald 検定。
  H3b 同モデルの条件 OR に TOST（境界 0.67–1.5、90%CI が境界内なら等価）。
  H3c 開示者（1回以上開示した参加者）の backfire_gap の参加者平均に片側1標本 t 検定（H1: >0）。
  H4  svi_process（参加者・t2）を 平均|乖離|・条件・交互作用で OLS。Low 条件で高SVI-P×大|乖離|の割合。
  Holm 補正：H1・H2・H3a の3検定。
除外（§5.4）：is_trial、source≠cw、研究者の確認回、t2 未到達（未完了）、注意チェック不通過、
  高速通過（合計時間＜中央値/3）、操作チェックが条件と正反対の極値、認識チェック不通過の試行。
  感度分析：操作チェック・認識チェックの除外を戻した版、hidden_ms 上位5%を除いた版。
"""
import json
import math
import sys
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import optimize, stats

sys.path.insert(0, __import__("os").path.dirname(__file__))
from analyze import EXCLUDE_PARTICIPANTS, _aware_unfavorable, num  # noqa: E402

warnings.simplefilter("ignore")
SEED = 20261124
COV = "accident + ai_use + age_c"                   # 共変量（§5.1：事故経験・AI利用・年齢）
TRIAL_TERMS = "C(cell) + C(scenario) + order"       # 過失構造セル＋シナリオ＋試行順
AI_USE = {"使ったことがない": 0, "ほとんど使わない": 1, "たまに使う": 2, "よく使う": 3}


# --- データの組み立て ----------------------------------------------------------
def build(items):
    """試行（セッション）単位の DataFrame と、参加者単位の DataFrame を返す。"""
    meta, outcome, surveys = {}, {}, defaultdict(dict)
    for it in items:
        pk, sk = it.get("pk", ""), it.get("sk", "")
        if not pk.startswith("SESSION#"):
            continue
        sid = pk.split("#", 1)[1]
        if sk == "META":
            meta[sid] = it
        elif sk == "OUTCOME":
            outcome[sid] = it
        elif sk.startswith("SURVEY#"):
            surveys[sid][sk.split("#")[1]] = it

    trials, people = [], defaultdict(lambda: {"start": None})
    for sid, m in meta.items():
        c = m.get("condition") or {}
        pid = c.get("participant_id")
        if (m.get("study") != "study3" or m.get("is_trial") or m.get("source") != "cw"
                or not pid or pid in EXCLUDE_PARTICIPANTS):
            continue
        p = people[pid]
        t = num(m.get("createdAt"))
        p["start"] = t if p["start"] is None else min(p["start"], t)
        p["risk"] = c.get("risk")
        sv = surveys[sid]
        if "t0" in sv:
            p["t0"] = sv["t0"].get("responses", {})
        if "t2" in sv:
            p["t2"] = sv["t2"].get("responses", {})
            p["t2_ts"] = num(sv["t2"].get("ts"))
            p["integrity"] = sv["t2"].get("integrity") or {}
        o = outcome.get(sid)
        if m.get("status") != "finalized" or not o:
            continue                                   # 除外3：finalize 未到達の試行
        g = o.get("game") or {}
        cell = g.get("fault_cell") or c.get("fault_cell")
        pc = (sv.get("perception") or {}).get("responses", {})
        dec = num((g.get("deception") or {}).get("deception_count"))
        dev = num(g.get("deviation_from_truth"))
        agreed_r, exp_r = num(g.get("agreed_victim_ratio")), num(pc.get("sf_expected_ratio"))
        trials.append({
            "pid": pid, "sid": sid, "cell": cell, "scenario": c.get("scenario"),
            "order": int(num(c.get("trial_index")) or 0),
            "deceive": None if dec is None else int(dec > 0), "dec_count": dec,
            "deviation": dev, "abs_dev": None if dev is None else abs(dev),
            "disclosed": int(bool(g.get("disclosed_dashcam"))),
            "sf_own": num(pc.get("sf_own_fault")), "sf_opp": num(pc.get("sf_opp_fault")),
            "sf_ev": num(pc.get("sf_evidence_favor")),
            "backfire": (agreed_r - exp_r) if agreed_r is not None and exp_r is not None else None,
            "aware": _aware_unfavorable(pc, cell, c.get("scenario")),
            "resolution": g.get("resolution"),
        })

    prow = []
    for pid, p in people.items():
        t0, t2 = p.get("t0", {}), p.get("t2")
        svi = [num(t2.get(f"svi_pr{i}")) for i in range(1, 5)] if t2 else []
        svi = [v for v in svi if v is not None]
        prow.append({
            "pid": pid, "risk": p.get("risk"), "completed": t2 is not None,
            "attn_ok": bool(t2) and str(t2.get("attn1")) == "4",
            "minutes": (p["t2_ts"] - p["start"]) / 60000 if t2 and p.get("t2_ts") else None,
            "mc_verif": num(t2.get("mc_verif")) if t2 else None,
            "hidden_ms": num((p.get("integrity") or {}).get("hidden_ms")),
            "paste": num((p.get("integrity") or {}).get("paste_blocked")),
            "accident": 1 if t0.get("accident_exp") == "ある" else 0,
            "ai_use": AI_USE.get(t0.get("ai_familiarity"), np.nan),
            "age": num(t0.get("age")),
            "svi_p": sum(svi) / len(svi) if len(svi) >= 3 else None,   # §5.5 欠測>20%は除外
        })
    return pd.DataFrame(trials), pd.DataFrame(prow)


def exclusions(T, P, mc_exclude=True, aware_exclude=True, hidden_top5=False):
    """§5.4 の除外を順に適用し、各段の件数を記録する。"""
    log = [("cw 参加者（研究者の確認回を除く）", len(P))]
    P = P[P.completed]; log.append(("t2 まで完了", len(P)))
    P = P[P.attn_ok]; log.append(("注意チェック通過", len(P)))
    cut = P.minutes.median() / 3
    P = P[P.minutes >= cut]; log.append((f"高速通過を除外（< {cut:.1f}分）", len(P)))
    if mc_exclude:
        opp = ((P.risk == "high") & (P.mc_verif == 1)) | ((P.risk == "low") & (P.mc_verif == 7))
        P = P[~opp]; log.append(("操作チェックの逆極値を除外", len(P)))
    if hidden_top5:
        q = P.hidden_ms.quantile(.95)
        P = P[P.hidden_ms <= q]; log.append((f"離席時間 上位5%を除外（> {q/1000:.0f}秒）", len(P)))
    T = T[T.pid.isin(P.pid)]
    log.append(("　試行（finalize 済み）", len(T)))
    if aware_exclude:
        T = T[T.aware != False]  # noqa: E712  None（判定不能）は含める
        log.append(("　認識チェック不通過の試行を除外", len(T)))
    P = P.assign(high=(P.risk == "high").astype(int), age_c=P.age - P.age.mean())
    T = T.merge(P[["pid", "high", "accident", "ai_use", "age_c"]], on="pid")
    return T, P, log


# --- GLMM ロジット（参加者ランダム切片・適応なし Gauss–Hermite 求積） -----------------
def glmm_logit(formula, df, group="pid", nq=25):
    y, Xdf = _design(formula, df)
    X = Xdf.to_numpy()
    g = pd.factorize(df[group])[0]
    xk, wk = np.polynomial.hermite.hermgauss(nq)
    b_nodes, w = np.sqrt(2) * xk, wk / np.sqrt(np.pi)

    def nll(theta):
        beta, sig = theta[:-1], np.exp(theta[-1])
        eta = (X @ beta)[:, None] + sig * b_nodes[None, :]
        ll = y[:, None] * eta - np.logaddexp(0, eta)            # 観測ごと×節点
        per = np.zeros((g.max() + 1, nq))
        np.add.at(per, g, ll)
        m = per.max(axis=1, keepdims=True)
        return -np.sum(m[:, 0] + np.log(np.exp(per - m) @ w))

    start = np.r_[sm.Logit(y, X).fit(disp=0).params, np.log(0.5)]
    r = optimize.minimize(nll, start, method="BFGS")
    H = _num_hessian(nll, r.x)
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    names = list(Xdf.columns) + ["log_sigma_u"]
    out = pd.DataFrame({"coef": r.x, "se": se}, index=names)
    out["z"] = out.coef / out.se
    out["p"] = 2 * stats.norm.sf(out.z.abs())
    return out, r.success, -r.fun


def _design(formula, df):
    import patsy
    y, X = patsy.dmatrices(formula, df, return_type="dataframe")
    return y.iloc[:, 0].to_numpy(), X


def _num_hessian(f, x, h=1e-4):
    n = len(x); H = np.zeros((n, n))
    for i in range(n):
        for j in range(i, n):
            e_i, e_j = np.eye(n)[i] * h, np.eye(n)[j] * h
            H[i, j] = H[j, i] = (f(x + e_i + e_j) - f(x + e_i - e_j)
                                 - f(x - e_i + e_j) + f(x - e_i - e_j)) / (4 * h * h)
    return H


# --- 各仮説 ---------------------------------------------------------------------
def h1(T, P):
    d = T[T.cell.isin(["mixed", "main_fault"]) & T.deceive.notna()].dropna(subset=["ai_use", "age_c"])
    tab, ok, _ = glmm_logit(f"deceive ~ high + {TRIAL_TERMS} + {COV}", d)
    b = tab.loc["high"]
    res = {"n_trials": len(d), "n_part": d.pid.nunique(), "OR": math.exp(b.coef),
           "CI": (math.exp(b.coef - 1.96 * b.se), math.exp(b.coef + 1.96 * b.se)), "p": b.p,
           "sigma_u": math.exp(tab.loc["log_sigma_u"].coef), "converged": ok}
    # 頑健性：参加者レベル（②③のいずれかで欺瞞あり）
    pl = d.groupby("pid").agg(any_dec=("deceive", "max")).reset_index().merge(
        P[["pid", "high", "accident", "ai_use", "age_c"]], on="pid")
    m = smf.logit(f"any_dec ~ high + {COV}", pl).fit(disp=0)
    res["robust_participant"] = (math.exp(m.params.high), m.pvalues.high, len(pl))
    # 補助：欺瞞件数（負の二項・クラスタ頑健SE）
    nb = smf.negativebinomial(f"dec_count ~ high + {TRIAL_TERMS} + {COV}", d).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(d.pid)[0]})
    res["negbin_IRR"] = (math.exp(nb.params.high), nb.pvalues.high)
    return res


def h2(T):
    d = T[T.deviation.notna()].dropna(subset=["ai_use", "age_c"])
    m = smf.mixedlm(f"deviation ~ high + {TRIAL_TERMS} + {COV}", d, groups=d.pid).fit(reml=True)
    ma = smf.mixedlm(f"abs_dev ~ high + {TRIAL_TERMS} + {COV}", d, groups=d.pid).fit(reml=True)
    ci = m.conf_int().loc["high"]
    return {"n_trials": len(d), "n_part": d.pid.nunique(), "b": m.params.high, "CI": tuple(ci),
            "p": m.pvalues.high, "abs_b": ma.params.high, "abs_p": ma.pvalues.high,
            "converged": m.converged}


def mediation(T, B=5000):
    """条件→欺瞞（線形確率）→乖離。参加者単位のクラスタ・ブートストラップ、バイアス修正CI。"""
    d = T[T.cell.isin(["mixed", "main_fault"]) & T.deceive.notna() & T.deviation.notna()]
    d = d.dropna(subset=["ai_use", "age_c"]).reset_index(drop=True)
    fa = f"deceive ~ high + {TRIAL_TERMS} + {COV}"
    fb = f"deviation ~ deceive + high + {TRIAL_TERMS} + {COV}"

    def ab(x):
        return smf.ols(fa, x).fit().params.high * smf.ols(fb, x).fit().params.deceive

    est = ab(d)
    groups = {k: v.index.to_numpy() for k, v in d.groupby("pid")}
    keys = list(groups)
    rng = np.random.default_rng(SEED)
    boots = []
    for _ in range(B):
        idx = np.concatenate([groups[k] for k in rng.choice(keys, len(keys))])
        try:
            boots.append(ab(d.loc[idx].reset_index(drop=True)))
        except Exception:  # noqa: BLE001  特異な再標本は飛ばす（件数を報告）
            pass
    boots = np.array(boots)
    z0 = stats.norm.ppf((boots < est).mean())
    lo, hi = stats.norm.cdf(2 * z0 + np.array([-1.96, 1.96]))
    return {"indirect": est, "CI": tuple(np.quantile(boots, [lo, hi])), "B": len(boots)}


def h3(T):
    d = T.dropna(subset=["sf_own", "sf_opp", "sf_ev", "ai_use", "age_c"])
    m = smf.logit(f"disclosed ~ sf_own + sf_opp + sf_ev + high + {COV}", d).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(d.pid)[0]})
    wald = m.wald_test("sf_own = 0, sf_opp = 0, sf_ev = 0", scalar=True)
    b, se = m.params.high, m.bse.high
    lo90, hi90 = math.exp(b - 1.645 * se), math.exp(b + 1.645 * se)
    # TOST：H0a OR≤0.67, H0b OR≥1.5
    p_tost = max(stats.norm.sf((b - math.log(0.67)) / se), stats.norm.cdf((b - math.log(1.5)) / se))
    # H3c：開示者の参加者平均
    disc = T[(T.disclosed == 1) & T.backfire.notna()].groupby("pid").backfire.mean()
    tc = stats.ttest_1samp(disc, 0, alternative="greater") if len(disc) > 1 else None
    return {
        "n_trials": len(d), "disclosure_rate": d.disclosed.mean(),
        "coefs": {k: (math.exp(m.params[k]), m.pvalues[k]) for k in ("sf_own", "sf_opp", "sf_ev")},
        "wald_p": float(wald.pvalue),
        "h3b_OR": math.exp(b), "h3b_CI90": (lo90, hi90), "h3b_tost_p": p_tost,
        "h3b_equivalent": lo90 > 0.67 and hi90 < 1.5,
        "h3c_n": len(disc), "h3c_mean": disc.mean() if len(disc) else None,
        "h3c_t": None if tc is None else (tc.statistic, tc.pvalue),
    }


def h4(T, P):
    pm = T.groupby("pid").abs_dev.mean().rename("mad").reset_index()
    d = P.merge(pm, on="pid").dropna(subset=["svi_p", "mad"])
    m = smf.ols("svi_p ~ mad * high", d).fit()
    low = d[d.high == 0]
    # 象限の基準：SVI-P が尺度中点（4）より上、平均|乖離|が修正要素1単位（10点）以上
    quad = ((low.svi_p > 4) & (low.mad >= 10)).mean() if len(low) else None
    return {"n": len(d), "params": m.params.to_dict(), "pvalues": m.pvalues.to_dict(),
            "low_quadrant_share": quad}


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0] * len(ps), 0
    for r, i in enumerate(order):
        run = max(run, min(1, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


# --- 出力 -----------------------------------------------------------------------
def run(T, P, label, full=True):
    T, P, log = exclusions(T, P, **label[1])
    print(f"\n{'=' * 78}\n{label[0]}\n{'=' * 78}")
    for k, v in log:
        print(f"  {k:<38}{v:>5}")
    print(f"  条件 High/Low = {int(P.high.sum())}/{int((1 - P.high).sum())}")
    r1, r2, r3 = h1(T, P), h2(T), h3(T)
    adj = holm([r1["p"], r2["p"], r3["wald_p"]])
    print(f"\n[H1] 欺瞞（②③の試行 n={r1['n_trials']}・参加者 {r1['n_part']}）GLMM: "
          f"OR(High)={r1['OR']:.2f} [{r1['CI'][0]:.2f}, {r1['CI'][1]:.2f}], p={r1['p']:.4f}, "
          f"Holm p={adj[0]:.4f}, σ_u={r1['sigma_u']:.2f}, 収束={r1['converged']}")
    print(f"     頑健性 参加者レベル: OR={r1['robust_participant'][0]:.2f}, "
          f"p={r1['robust_participant'][1]:.4f} (n={r1['robust_participant'][2]})"
          f" ／ 負の二項 IRR={r1['negbin_IRR'][0]:.2f}, p={r1['negbin_IRR'][1]:.4f}")
    print(f"[H2] 乖離（試行 n={r2['n_trials']}）LMM: b(High)={r2['b']:.2f} "
          f"[{r2['CI'][0]:.2f}, {r2['CI'][1]:.2f}], p={r2['p']:.4f}, Holm p={adj[1]:.4f}"
          f" ／ |乖離|: b={r2['abs_b']:.2f}, p={r2['abs_p']:.4f}")
    if full:
        md = mediation(T)
        print(f"     媒介 条件→欺瞞→乖離: 間接効果={md['indirect']:.3f} "
              f"[{md['CI'][0]:.3f}, {md['CI'][1]:.3f}]（BC, B={md['B']}）")
    print(f"[H3a] 開示（試行 n={r3['n_trials']}, 開示率 {r3['disclosure_rate']:.2f}）: "
          + ", ".join(f"{k} OR={v[0]:.2f} p={v[1]:.4f}" for k, v in r3["coefs"].items())
          + f"／同時Wald p={r3['wald_p']:.4f}, Holm p={adj[2]:.4f}")
    print(f"[H3b] 条件 OR={r3['h3b_OR']:.2f}, 90%CI [{r3['h3b_CI90'][0]:.2f}, {r3['h3b_CI90'][1]:.2f}],"
          f" TOST p={r3['h3b_tost_p']:.4f} → 等価{'と判断' if r3['h3b_equivalent'] else 'は示せず'}")
    if r3["h3c_t"]:
        print(f"[H3c] 開示者 n={r3['h3c_n']}: 裏目ギャップ平均={r3['h3c_mean']:.2f}, "
              f"t={r3['h3c_t'][0]:.2f}, 片側 p={r3['h3c_t'][1]:.4f}")
    if full:
        r4 = h4(T, P)
        print(f"[H4] SVI-P ~ 平均|乖離| × 条件（n={r4['n']}）: "
              + ", ".join(f"{k}={v:.3f}(p={r4['pvalues'][k]:.3f})" for k, v in r4["params"].items()
                          if k != "Intercept")
              + f"／Low の高SVI-P×大|乖離| 割合={r4['low_quadrant_share']:.2f}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    unblind = "--unblind" in sys.argv
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    T, P = build(data.get("items", data))
    if not unblind:
        rng = np.random.default_rng(SEED)
        # 条件だけでなく結果変数も入れ替える（H3a・H4 の予測子は条件ではないので、条件の入れ替えだけでは盲検にならない）
        P = P.assign(risk=rng.permutation(P.risk.to_numpy()), svi_p=rng.permutation(P.svi_p.to_numpy()))
        outs = ["deceive", "dec_count", "deviation", "abs_dev", "disclosed", "backfire"]
        T = T.copy()
        T[outs] = T[outs].to_numpy()[rng.permutation(len(T))]
        print("★ 盲検モード：条件と結果変数を無作為に入れ替えています。下の推定値はすべて意味を持ちません（動作確認用）。")
    run(T, P, ("主分析（§5.4 の除外をすべて適用）", {}))
    run(T, P, ("感度：操作チェック・認識チェックの除外を戻す",
               {"mc_exclude": False, "aware_exclude": False}), full=False)
    run(T, P, ("感度：離席時間 上位5%を除外", {"hidden_top5": True}), full=False)
    print(f"\n[記述] 貼り付け試行あり: {(P[P.completed].paste > 0).sum()} / {P.completed.sum()} 人"
          "（除外には使わない。§5.4-8）")


if __name__ == "__main__":
    main()
