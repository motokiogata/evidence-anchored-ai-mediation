# -*- coding: utf-8 -*-
"""
実験データ分析: 管理エクスポートを取得し、条件別に従属変数を集計する。

主対象は研究3（203x示談プラットフォーム 開示ゲーム）。
操作要因は被験者間1要因: 検出リスクの文脈手がかり（監視環境 低/高）。
内部では detect_prob=0.2/0.8 の隠し抽選が record_exists を決め hard/soft に変換する
（旧「AI役割(none/mediator/verifier)×検証可能性」設計は廃止）。集計キーは検出確率 p。
DV: 欺瞞率・結果の正確性(真の割合からの乖離)・開示率・露見・合意率・決着様式(合意/裁定)・
SVI手続的公正 ほか。2026-08以降はラウンド上限7＋裁定(BATNA)により決裂でも乖離DVが定義される。

使い方:
  export API_BASE=https://xxxx.execute-api.ap-northeast-1.amazonaws.com
  export ADMIN_KEY=xxxx
  python analyze.py            # ライブ取得
  python analyze.py dump.json  # ローカルJSONから
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict


def fetch(api_base, key):
    url = f"{api_base}/api/admin/export?key={key}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def load():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            return json.load(f)
    api = os.environ["API_BASE"]
    key = os.environ["ADMIN_KEY"]
    return fetch(api, key)


def group_sessions(items):
    """セッションIDごとに meta/outcome/surveys をまとめる。"""
    sess = defaultdict(lambda: {"surveys": {}})
    for it in items:
        pk = it.get("pk", "")
        if not pk.startswith("SESSION#"):
            continue
        sid = pk.split("#", 1)[1]
        sk = it.get("sk", "")
        if sk == "META":
            sess[sid]["meta"] = it
        elif sk == "OUTCOME":
            sess[sid]["outcome"] = it
        elif sk.startswith("SURVEY#"):
            stage = sk.split("#")[1]
            sess[sid]["surveys"][stage] = it.get("responses", {})
    return sess


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def svi_process_mean(t2):
    """SVI process 下位尺度(svi_pr1..4)の平均。"""
    vals = [num(t2.get(k)) for k in ("svi_pr1", "svi_pr2", "svi_pr3", "svi_pr4")]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


# --- 認識チェック（2026-09 追加）-------------------------------------------
# 参加者が「自分の不利事実」を映像から認識できていたか。認識していない試行での否認は
# 欺瞞ではなく無知なので、主分析からは除外し感度分析で含める（プレレジ記載）。
#   A/B の不利事実 = 速度超過 → pc_own_speed が「制限速度を超えていた」
#   C   の不利事実 = 信号。**セルで内容が変わる**（judicial_map の cell_overrides）:
#       ②混合   = 黄信号見切り進入 → pc_own_signal が「黄だった」
#       ③主責   = 赤信号進入（信号残り）→ pc_own_signal が「赤だった」
#   ①潔白セルは不利事実が無いので判定不要（True を返す＝除外しない）。
_UNFAV_CORRECT = {
    ("A_intersection", "mixed"): ("pc_own_speed", "制限速度を超えていた"),
    ("A_intersection", "main_fault"): ("pc_own_speed", "制限速度を超えていた"),
    ("B_right_turn", "mixed"): ("pc_own_speed", "制限速度を超えていた"),
    ("B_right_turn", "main_fault"): ("pc_own_speed", "制限速度を超えていた"),
    ("C_signal_crossing", "mixed"): ("pc_own_signal", "黄だった"),
    ("C_signal_crossing", "main_fault"): ("pc_own_signal", "赤だった"),
}


def _aware_unfavorable(perception, fault_cell, scenario_id):
    """不利事実を認識できていたか。判定不能（旧データ等）は None。"""
    if fault_cell == "innocent":
        return True            # 隠すべき不利事実が存在しない＝除外対象にしない
    if not perception:
        return None            # perception 段階の導入前データ
    spec = _UNFAV_CORRECT.get((scenario_id, fault_cell))
    if not spec:
        return None
    field, correct = spec
    got = perception.get(field)
    return None if got in (None, "") else (got == correct)


def analyze_study3(sessions):
    cells = defaultdict(list)
    for sid, s in sessions.items():
        meta = s.get("meta")
        if not meta or meta.get("study") != "study3":
            continue
        if meta.get("is_trial"):
            continue  # 開発者トライアルは分析対象外
        cond = meta.get("condition", {})
        # 唯一の操作要因: 被害者の不利事実が第三者記録に残る検出確率 p（低/高）。実現値は別途参照。
        dp = cond.get("detect_prob")
        if dp is not None:
            verif = f"p={int(round(dp*100))}%"
        else:  # 旧データ（hard/soft）互換。検証可能性は被害者の不利事実1キーのみ（速度超過/黄進入…）
            verif = next(iter((cond.get("verifiability") or {}).values()),
                         cond.get("key_verifiability", "?"))
        game = (s.get("outcome") or {}).get("game", {})
        t2 = s["surveys"].get("t2", {})
        # H3の自己奉仕的公正評価は 2026-09 から perception 段階（映像直後・試行ごと）で測る。
        # それ以前のデータは t0（第1試行のみ）にあるためフォールバックする。
        pc = s["surveys"].get("perception", {})
        t0 = s["surveys"].get("t0", {})
        sf = pc if pc else t0
        post = s["surveys"].get("post_trial", {}) or t2   # 開示行動の自己申告（試行ごと）
        agreed_r = num(game.get("agreed_victim_ratio"))
        exp_r = num(sf.get("sf_expected_ratio"))
        # 裏目(H3c): 実現の被害者割合 − 予期割合。正=期待より不利化＝自己奉仕的開示の裏目。
        backfire = (agreed_r - exp_r) if (agreed_r is not None and exp_r is not None) else None
        rec = {
            "sid": sid,
            "verif": verif,                # 条件（検出リスク）をレコードにも保持（H3b用）
            # 過失構造（被験者内3水準）。旧データ（fault_cell 未記録）は mixed 扱い。
            "fault_cell": game.get("fault_cell") or cond.get("fault_cell") or "mixed",
            "trial_index": int(num(game.get("trial_index")) or 0),
            "participant": game.get("participant_id") or cond.get("participant_id") or sid,
            "agreed": (s.get("outcome") or {}).get("agreed"),
            "agreed_ratio": agreed_r,
            "deviation": num(game.get("deviation_from_truth")),
            # 決着様式: agreement/adjudication（2026-08 裁定フォールバック導入後）。
            # 旧データは resolution 未記録 → agreed から breakdown（乖離欠測）に落とす。
            "resolution": game.get("resolution")
            or ("agreement" if (s.get("outcome") or {}).get("agreed") else "breakdown"),
            # 相手方ドラレコ（隠し抽選）: 保有有無と裁定時の提出実現値
            "opp_dashcam_exists": game.get("opp_dashcam_exists"),
            "opp_submitted": (game.get("adjudication") or {}).get("opp_submitted"),
            "deception_count": num((game.get("deception") or {}).get("deception_count")),
            "honest_count": num((game.get("deception") or {}).get("honest_count")),
            "victim_exposed": num(game.get("victim_exposed")),
            "insurer_exposed": num(game.get("insurer_exposed")),
            "disclosed": 1.0 if game.get("disclosed_dashcam") else 0.0,   # 開示行動DV
            "rounds": num((s.get("outcome") or {}).get("rounds")),
            "svi_process": svi_process_mean(t2),
            # --- H3: 自己奉仕的公正評価（映像直後・開示決定前に測定） ---
            "sf_own_fault": num(sf.get("sf_own_fault")),          # 低いほど自己奉仕的
            "sf_opp_fault": num(sf.get("sf_opp_fault")),
            "sf_evidence_favor": num(sf.get("sf_evidence_favor")),
            "expected_ratio": exp_r,                              # 予期した自分の割合
            "backfire_gap": backfire,                            # H3c: >0=裏目
            # --- 認識チェック（開示ゲームの前提＝自分のタイプを知っているか）---
            # 自分の不利事実に気づいていない参加者の「否認」は欺瞞ではなく無知であり、
            # 欺瞞率DVの構成概念が崩れる。主分析は aware のみ、感度分析で全件を用いる。
            "pc_recall": pc.get("pc_recall"),
            "pc_own_speed": pc.get("pc_own_speed"),
            "pc_own_signal": pc.get("pc_own_signal"),
            "pc_opp_entry": pc.get("pc_opp_entry"),
            "aware_unfavorable": _aware_unfavorable(
                pc, game.get("fault_cell") or cond.get("fault_cell") or "mixed",
                cond.get("scenario")),
            "sr_deny": post.get("sr_deny"),      # 開示行動の自己申告（試行ごと）
        }
        cells[verif].append(rec)
    return cells


def report(cells):
    print("=" * 82)
    print("研究3（203x示談プラットフォーム）条件別集計 — 操作要因: 検証可能な証拠の有無")
    print("  DV: 欺瞞=虚偽/否認の件数, 乖離=合意割合−真の割合(負ほど被害者有利=嘘が通った疑い),")
    print("      被害露見=客観証拠で捕捉された嘘, 開示率=ドラレコ提出率, SVI-P=手続的公正")
    print("=" * 82)
    hdr = (f"{'証拠':<8}{'N':>3}  {'合意率':>6} {'欺瞞':>6} {'乖離':>7} "
           f"{'被害露見':>8} {'相手露見':>8} {'開示率':>6} {'SVI-P':>6}")
    print(hdr)
    print("-" * 82)
    for verif, recs in sorted(cells.items()):
        n = len(recs)
        agr = mean([1.0 if r["agreed"] else 0.0 for r in recs])
        print(f"{verif:<8}{n:>3}  "
              f"{(agr if agr is not None else 0):>6} "
              f"{str(mean([r['deception_count'] for r in recs])):>6} "
              f"{str(mean([r['deviation'] for r in recs])):>7} "
              f"{str(mean([r['victim_exposed'] for r in recs])):>8} "
              f"{str(mean([r['insurer_exposed'] for r in recs])):>8} "
              f"{str(mean([r['disclosed'] for r in recs])):>6} "
              f"{str(mean([r['svi_process'] for r in recs])):>6}")
    print("-" * 82)
    print("\n※ 仮説の読み: 検出リスク高(監視多)は（低比）欺瞞↓・被害者露見↑・乖離が0へ収束を予測。")
    print("  開示行動: 有利事実(相手違反)のドラレコを出すほど自分に有利だが、出す/伏せるは本人の選択。")
    print("  同意の製造チェック: SVI-P↑ かつ 乖離が負に開くなら『確信した受諾だが実は不利』の兆候。")


def report_resolution(cells):
    """決着様式（合意/裁定）の集計。2026-08 ラウンド上限7＋裁定(BATNA)導入後のDV。

    裁定でも乖離DVは定義される（検証済み事実のみで判タ割合を機械適用）。
    裁定行の被害露見には「否認を続けた不利事実が裁定で精算された露見」も含まれる。
    """
    flat = [r for recs in cells.values() for r in recs]
    if not flat:
        return
    print("\n" + "=" * 82)
    print("決着様式（agreement=合意 / adjudication=裁定 / breakdown=旧データの決裂・乖離欠測）")
    print("=" * 82)
    by_res = defaultdict(list)
    for r in flat:
        by_res[r.get("resolution") or "?"].append(r)
    print(f"  {'様式':<14}{'N':>3}  {'乖離':>7} {'欺瞞':>6} {'被害露見':>8} {'ラウンド':>8}")
    print("-" * 82)
    for res, g in sorted(by_res.items()):
        print(f"  {res:<14}{len(g):>3}  "
              f"{str(mean([r['deviation'] for r in g])):>7} "
              f"{str(mean([r['deception_count'] for r in g])):>6} "
              f"{str(mean([r['victim_exposed'] for r in g])):>8} "
              f"{str(mean([r['rounds'] for r in g])):>8}")
    adj = [r for r in flat if r.get("resolution") == "adjudication"]
    if adj:
        subm = mean([1.0 if r.get("opp_submitted") else 0.0 for r in adj])
        print(f"\n  裁定 N={len(adj)}: 相手方記録の提出率={subm}"
              f"（隠し抽選 保有0.6×セル別提出確率の実現値）")
    # 過失構造セル別の裁定率（②混合で膠着→裁定が偏っていないかの点検）
    by_cell = defaultdict(list)
    for r in flat:
        by_cell[r.get("fault_cell", "mixed")].append(r)
    print(f"\n  {'過失':<8}{'N':>3}  {'合意率':>6} {'裁定率':>6}")
    for fc in FAULT_ORDER:
        g = by_cell.get(fc, [])
        if g:
            agr = mean([1.0 if r["resolution"] == "agreement" else 0.0 for r in g])
            adr = mean([1.0 if r["resolution"] == "adjudication" else 0.0 for r in g])
            print(f"  {FAULT_LABEL[fc]:<8}{len(g):>3}  {str(agr):>6} {str(adr):>6}")
    print("-" * 82)


def analyze_h3(cells):
    """H3(自己奉仕的公正評価による開示)の集計。cells(条件別recs)を平坦化して分析。

    H3a: 開示者は非開示者より自己過失知覚↓・相手過失知覚↑・自証拠有利性↑。
    H3b: 開示率は検出リスク条件でほぼ不変（＝合理的検出計算でなく動機づけ認知が支配）。
    H3c: 開示者は予期割合<実現割合（backfire_gap>0）＝自己奉仕的開示の裏目。
    """
    flat = [r for recs in cells.values() for r in recs]
    disc = [r for r in flat if r["disclosed"] == 1.0]
    nond = [r for r in flat if r["disclosed"] == 0.0]
    by_cond = defaultdict(list)
    for r in flat:
        by_cond[r["verif"]].append(r)
    return {"flat": flat, "disc": disc, "nond": nond, "by_cond": by_cond}


def report_h3(h3):
    flat, disc, nond, by_cond = h3["flat"], h3["disc"], h3["nond"], h3["by_cond"]
    if not flat:
        return
    print("\n" + "=" * 82)
    print("H3（自己奉仕的公正評価による証拠開示）")
    print("  自己奉仕指標(1-7): own_fault=自己の過失知覚(低いほど自己奉仕的), opp_fault=相手の過失知覚,")
    print("                     ev_favor=自ドラレコの有利性知覚。exp=予期した自分の割合")
    print("=" * 82)

    # H3a: 開示者 vs 非開示者の自己奉仕指標
    print("H3a  開示者 vs 非開示者の自己奉仕指標（予測: 開示者は own_fault↓ / opp_fault↑ / ev_favor↑）")
    print(f"  {'群':<12}{'N':>3}  {'own_fault':>10}{'opp_fault':>10}{'ev_favor':>10}")
    for label, g in (("開示した", disc), ("開示せず", nond)):
        print(f"  {label:<12}{len(g):>3}  "
              f"{str(mean([r['sf_own_fault'] for r in g])):>10}"
              f"{str(mean([r['sf_opp_fault'] for r in g])):>10}"
              f"{str(mean([r['sf_evidence_favor'] for r in g])):>10}")

    # H3b: 条件別の開示率（予測: ほぼ不変）
    print("\nH3b  検出リスク条件別の開示率（予測: 条件でほぼ不変＝開示はIV駆動でない）")
    print(f"  {'条件':<10}{'N':>3}  {'開示率':>7}")
    for cond, recs in sorted(by_cond.items()):
        print(f"  {cond:<10}{len(recs):>3}  {str(mean([r['disclosed'] for r in recs])):>7}")

    # H3c: 開示者の裏目（予期割合 vs 実現割合）
    print("\nH3c  開示者の裏目（予測: 予期割合 < 実現割合 ＝ backfire_gap>0）")
    if disc:
        print(f"  開示者 N={len(disc)}  予期割合={mean([r['expected_ratio'] for r in disc])}"
              f"  実現割合={mean([r['agreed_ratio'] for r in disc])}"
              f"  裏目gap={mean([r['backfire_gap'] for r in disc])}（>0=期待より不利化）")
    else:
        print("  開示者がまだいません。")
    print("-" * 82)


# --- 過失構造（被験者内3水準）＝ H1b 用量反応 ---------------------------------
FAULT_ORDER = ["innocent", "mixed", "main_fault"]
FAULT_LABEL = {"innocent": "①潔白", "mixed": "②混合", "main_fault": "③主責"}


def analyze_fault_structure(cells):
    """条件別recs（analyze_study3の出力）を平坦化し、過失構造セル別に集計する。

    返り値: {"flat", "by_cell"(セル別), "by_cell_risk"(セル×検出リスク), "first"(第1試行のみ)}。
    """
    flat = [r for recs in cells.values() for r in recs]
    by_cell = defaultdict(list)
    by_cell_risk = defaultdict(list)
    for r in flat:
        by_cell[r.get("fault_cell", "mixed")].append(r)
        by_cell_risk[(r.get("fault_cell", "mixed"), r.get("verif", "?"))].append(r)
    first = [r for r in flat if r.get("trial_index", 0) == 0]
    return {"flat": flat, "by_cell": by_cell, "by_cell_risk": by_cell_risk, "first": first}


def _cell_row(label, g):
    return (f"  {label:<8}{len(g):>3}  "
            f"{str(mean([r['deception_count'] for r in g])):>6} "
            f"{str(mean([r['deviation'] for r in g])):>7} "
            f"{str(mean([r['disclosed'] for r in g])):>6} "
            f"{str(mean([r['victim_exposed'] for r in g])):>8}")


def _mixedlm_dose(flat):
    """H1b 用量反応の確認的検定：参加者ランダム切片つき混合モデル。

    deception_count ~ cell_ordinal(①=0/②=1/③=2) + (1|participant)。
    被験者内で①②③を各1回受けるため参加者をランダム効果に。statsmodels 必須。
    """
    rows = [(r["participant"], FAULT_ORDER.index(r["fault_cell"]), r["deception_count"])
            for r in flat
            if r.get("fault_cell") in FAULT_ORDER and r.get("deception_count") is not None
            and r.get("participant")]
    n_part = len({p for p, _, _ in rows})
    if len(rows) < 6 or n_part < 3:
        print("  （混合モデルは参加者・観測が十分に集まってから実行されます）")
        return
    try:
        import warnings
        import pandas as pd
        import statsmodels.formula.api as smf
        df = pd.DataFrame(rows, columns=["participant", "cell", "deception"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = smf.mixedlm("deception ~ cell", df, groups=df["participant"]).fit(reml=False)
        b = m.params.get("cell"); p = m.pvalues.get("cell")
        print(f"  混合モデル deception ~ cell (①0<②1<③2) + (1|participant): "
              f"β_cell={b:.3f}, p={p:.4f}  （β>0 かつ p<.05 で用量反応①<②<③を支持）")
    except Exception as e:  # noqa: BLE001
        print(f"  （混合モデルの実行に失敗: {e}）")


def report_fault_structure(fs):
    flat = fs["flat"]
    if not flat:
        return
    print("\n" + "=" * 82)
    print("H1b 過失構造（被験者内3水準・着脱方式）＝欺瞞の用量反応 ①潔白 < ②混合 < ③主責")
    print("  DV: 欺瞞=虚偽/否認件数, 乖離=合意割合−その試行の真値, 開示率, 被害露見")
    print("=" * 82)
    print(f"  {'過失':<8}{'N':>3}  {'欺瞞':>6} {'乖離':>7} {'開示率':>6} {'被害露見':>8}")
    print("-" * 82)
    for fc in FAULT_ORDER:
        g = fs["by_cell"].get(fc, [])
        if g:
            print(_cell_row(FAULT_LABEL[fc], g))
    print("-" * 82)
    print("  予測: 欺瞞は ①≒0 < ② < ③（用量反応）。①は隠す不利事実が無く検出操作が不活性（統制）。")

    # セル × 検出リスク（①では列差が消え、②③で High<Low が出るか）
    print("\n  過失構造 × 検出リスク（欺瞞件数の平均）")
    conds = sorted({v for (_, v) in fs["by_cell_risk"].keys()})
    print(f"  {'過失':<8}" + "".join(f"{c:>12}" for c in conds))
    for fc in FAULT_ORDER:
        cellvals = []
        for c in conds:
            g = fs["by_cell_risk"].get((fc, c), [])
            cellvals.append(str(mean([r['deception_count'] for r in g])) if g else "—")
        print(f"  {FAULT_LABEL[fc]:<8}" + "".join(f"{v:>12}" for v in cellvals))

    # 第1試行のみ（順序・学習の影響を受けない最も素朴な指標）
    first_by_cell = defaultdict(list)
    for r in fs["first"]:
        first_by_cell[r.get("fault_cell", "mixed")].append(r)
    print("\n  第1試行のみ（被験者間・順序/学習の交絡なし）")
    print(f"  {'過失':<8}{'N':>3}  {'欺瞞':>6} {'乖離':>7} {'開示率':>6} {'被害露見':>8}")
    for fc in FAULT_ORDER:
        g = first_by_cell.get(fc, [])
        if g:
            print(_cell_row(FAULT_LABEL[fc], g))

    # 確認的：参加者ランダム効果の混合モデル
    print("\n  H1b 確認的検定（混合モデル）")
    _mixedlm_dose(flat)
    print("-" * 82)


def report_awareness(cells):
    """認識チェックの通過率（欺瞞率DVの前提が成立している試行の割合）。

    aware=False の試行では、不利事実の否認は欺瞞ではなく無知の可能性がある。
    主分析は aware!=False に限り、ここで除外率を必ず報告する（プレレジ記載）。
    """
    flat = [r for g in cells.values() for r in g]
    scored = [r for r in flat if r.get("aware_unfavorable") is not None]
    if not scored:
        print("\n[認識チェック] 該当データなし（perception 段階の導入前データのみ）。")
        return
    print("\n" + "=" * 82)
    print("[認識チェック] 自分の不利事実を映像から認識できていたか（開示ゲームの前提）")
    print("-" * 82)
    print(f"  {'過失':<8}{'N':>4}{'認識':>6}{'認識率':>8}")
    for fc in FAULT_ORDER:
        g = [r for r in scored if r.get("fault_cell") == fc]
        if not g:
            continue
        ok = sum(1 for r in g if r["aware_unfavorable"])
        print(f"  {FAULT_LABEL[fc]:<8}{len(g):>4}{ok:>6}{ok/len(g)*100:>7.1f}%")
    ok_all = sum(1 for r in scored if r["aware_unfavorable"])
    print(f"  {'全体':<8}{len(scored):>4}{ok_all:>6}{ok_all/len(scored)*100:>7.1f}%")
    print(f"  ※ 主分析からの除外候補: {len(scored)-ok_all} 試行（感度分析では含める）")
    print("=" * 82)


# 研究者自身が本番リンク（?src=cw・devKeyなし）で通した動作確認。is_trial が付かないのでここで除く（理由を残す）。
EXCLUDE_PARTICIPANTS = {
    "P-oef6rqovmwu2oo4x",  # 2026-11-24 研究者の通し確認。ドラレコ誤提出の不具合を発見した回（修正済み）
}


def main():
    data = load()
    items = data.get("items", data)
    sessions = group_sessions(items)
    # 分析対象はクラウドワークス経由（source=cw）のみ（プレレジ 3.2）。それ以前に通常リンクで記録された
    # 動作確認期（2026-07〜09）のセッションは is_trial が付いていないので、ここで除く。DM募集は中止しデータ無し。
    non_cw = [k for k, s in sessions.items()
              if s.get("meta", {}).get("study") == "study3"
              and not s.get("meta", {}).get("is_trial")
              and (s.get("meta", {}).get("source") != "cw"
                   or (s.get("meta", {}).get("condition") or {}).get("participant_id") in EXCLUDE_PARTICIPANTS)]
    for k in non_cw:
        del sessions[k]
    print(f"クラウドワークス経由以外・研究者の確認回の研究3セッションを除外: {len(non_cw)} 件")
    n3 = sum(1 for s in sessions.values()
             if s.get("meta", {}).get("study") == "study3"
             and not s.get("meta", {}).get("is_trial"))
    n_trial = sum(1 for s in sessions.values() if s.get("meta", {}).get("is_trial"))
    print(f"取得: {len(items)} items / セッション {len(sessions)} 件"
          f"（うち研究3本番: {n3} / 開発トライアル除外: {n_trial}）\n")
    cells = analyze_study3(sessions)
    if not any(cells.values()):
        print("研究3の完了データがまだありません（セッションを実施後に再実行）。")
    else:
        report(cells)
        report_awareness(cells)        # 認識チェック（欺瞞率DVの前提・除外率）
        report_resolution(cells)       # 決着様式（合意/裁定）と裁定の内訳
        report_fault_structure(analyze_fault_structure(cells))  # H1b: 過失構造の用量反応
        report_h3(analyze_h3(cells))   # H3: 自己奉仕的公正評価による開示
    # 生データも保存（キーは条件ラベル verif そのもの）
    out = os.path.join(os.path.dirname(__file__), "report_study3.json")
    with open(out, "w") as f:
        json.dump({k: v for k, v in cells.items()}, f, ensure_ascii=False, indent=2)
    print(f"\n生データ: {out}")


if __name__ == "__main__":
    main()
