# -*- coding: utf-8 -*-
"""
判例タイムズ図 × 修正要素の【単一の真実源】。

backend（過失割合の算定・提示文面）と simulation（映像生成）の双方がこれを参照し、
「基本過失割合 × 修正要素の有無」という条件セットを一元管理する。

各修正要素は次の3面を同時に持つ:
  1. 法的効果 (delta_victim): 判タの加減算。被害者(A)側の過失割合に対する増減。
  2. 映像効果 (video_effect): CARLAでどう描画するか（speeding / stop_violation / run_red / early_turn）。
  3. 知覚の非対称 (seen_by): どちらの視点でその事実が見えるか（情報の非対称の作り込み）。

これにより、動画・アプリ・論文が同じ定義から導かれる。
"""

# party: この修正要素の帰責主体。 victim=被害者A / opponent=相手B。
# delta_victim: 適用時に victim(A) 側の過失割合に加える値（正=Aに不利, 負=Aに有利）。
# video_effect: simulation/generate_accident.py が解釈する描画効果キー。
# seen_by: この事実が明確に写る視点（情報の非対称）。
#
# --- 開示ゲーム用フィールド（docs/実験デザイン_開示ゲーム.md）---
# ground_truth: この争点が実際に起きたか（真/偽）。欺瞞率・結果正確性の照合基準。
# favors: この事実が有利に働く側（victim/opponent）。delta符号と整合。
# verifiable_by: ハード証拠を持ちうる視点（検証者AIが裁定できる源）。
#   実際にハード/ソフトのどちらで提示するかは条件割付(IV2)で決める。

JUDICIAL_SCENARIOS = {
    # === 図107系: 一方が優先道路・出会い頭 ================================
    # 被害者Aは優先道路を直進。相手方Bは非優先の側道から一時停止規制を無視して進入。
    # 優先道路のため基本はA有利。①②③の用量反応は保つが、優先道路の性質上、
    # 被害者が「主責（過半）」になることはない（範囲は圧縮される＝設計合意済み 2026-08-04）。
    "A_intersection": {
        "figure": "判例タイムズ38号 図107（一方が優先道路）",
        "title": "優先道路直進 vs 非優先道路からの出会い頭",
        "junction_type": "unsignalized",   # 信号のない交差点で生成
        "base_ratio": [15, 85],            # [victim(A・優先道路), opponent(B・非優先)]
        "speed_limit_kmh": 40,             # 映像HUDの「制限」表示・不利事実の判断基準（超過時67km/h）
        # summary は【双方に争いのない構造的事実】のみを書く（道路構造・規制の所在・進行方向・接触態様）。
        # 争われる挙動（不停止進入・速度超過）は modifiers 側に置き、提示は scenarios.build_private_info が
        # 「確定事実 / あなたの映像に写っていること / 相手方の主張」の3層に分けて行う。2026-09 専門家FB対応。
        "summary": (
            "あなた（A）は優先道路を直進し、非優先の側道から交差点に進入してきた"
            "相手方Bの車両と出会い頭に衝突した。"
            "あなたの走行路が優先道路で、相手方の側には一時停止の規制がある。"
        ),
        "modifiers": {
            "opp_stop_violation": {
                "label": "相手方Bの一時停止規制違反（不停止進入）",
                "party": "opponent",
                "delta_victim": -5,    # Bの著しい過失 → Aにさらに有利（優先道路で基本すでにA有利）
                "video_effect": "stop_violation",
                "seen_by": ["opponent"],   # 標識はB正面／A視点では横で見落としやすい
                "note": "Bの進行路（非優先の側道）に一時停止標識。Bは停止も徐行もせず交差点へ進入した。",
                "note_absent": "Bは側道から通常の速度で交差点に進入した。減速せず突入するような挙動は映像には写っていない。",
                # ※ 生成側は違反なしのとき速度倍率を掛けないだけで、B が明示的に停止する描写には
                #   ならない。映像で言えないことを断定しないよう「不停止の挙動は写っていない」と書く。
                #   B が停止線で止まってから発進する挙動の実装は ToDo（停止→発進の速度プロファイル）。
                "ground_truth": True,      # 実際にBは停止せず進入した
                "favors": "victim",
                "verifiable_by": ["victim"],   # 被害者のドラレコがBの不停止を捉えうる
            },
            "victim_speeding": {
                "label": "被害者Aの著しい速度超過",
                "party": "victim",
                "delta_victim": +20,   # 優先道路でも著しい速度超過はAに不利（ただし主責には至らない）
                "video_effect": "speeding",
                "seen_by": ["victim"],     # 自車HUD／Bからは速い接近として見える
                "note": "A車は制限速度40km/hの優先道路を67km/hで走行し交差点へ進入した（27km/h超過）。",
                "note_absent": "A車は制限速度40km/hの優先道路を40km/hで走行していた（速度超過はない）。",
                "ground_truth": True,      # 実際にAは速度超過していた（否認するか認めるか）
                "favors": "opponent",
                "verifiable_by": ["opponent"],  # 速度記録/損傷が証拠になりうる
            },
        },
    },

    # === 図138: 直進車 vs 対向右折車 ====================================
    "B_right_turn": {
        "figure": "判例タイムズ38号 図138",
        "title": "直進車 vs 対向右折車",
        "junction_type": "unsignalized",   # 基本形は信号なしで生成（信号進入は別途扱う）
        "base_ratio": [20, 80],            # 直進A : 右折B
        "speed_limit_kmh": 50,             # 通常走行43km/h・超過時73km/h（映像HUDの「制限」表示）
        "summary": (
            "あなた（A）は直進で交差点に進入し、対向車線から右折してきた"
            "相手方Bの車両と衝突した。直進車が優先されるのが原則である。"
        ),
        "modifiers": {
            "opp_early_turn": {
                "label": "相手方Bの早回り右折",
                "party": "opponent",
                "delta_victim": -10,   # Bの著しい過失 → Aに有利
                "video_effect": "early_turn",
                "seen_by": ["victim"],     # Aからは"急に膨らんできた"／Bは通常の右折に見える
                "note": "Bは交差点の中心の手前をショートカットするように早回りで右折した。",
                "note_absent": "Bは交差点の中心付近まで進んでから右折した（早回りではない）。",
                "ground_truth": True,
                "favors": "victim",
                "verifiable_by": ["victim"],   # 目撃/ドラレコ
            },
            "victim_speeding": {
                "label": "直進車Aの速度超過",
                "party": "victim",
                "delta_victim": +10,
                "video_effect": "speeding",
                "seen_by": ["victim"],
                "note": "A車は制限速度50km/hの道路を73km/hで走行し交差点へ進入した（23km/h超過）。",
                "note_absent": "A車は制限速度50km/hの道路を43km/hで走行していた（速度超過はない）。",
                "ground_truth": True,
                "favors": "opponent",
                "verifiable_by": ["opponent"],
            },
        },
    },

    # === 信号交差点・出会い頭（信号無視） =================================
    # 図101系。信号の状態そのものが過失の主要因。信号あり交差点で生成。
    "C_signal_crossing": {
        "figure": "判例タイムズ38号 図101系（信号交差点）",
        "title": "信号交差点・出会い頭（信号残り／信号無視）",
        "junction_type": "signalized",     # 信号のある交差点で生成
        "base_ratio": [50, 50],            # 双方が青を主張する基本状態（真値は修正要素適用後 25:75）
        "speed_limit_kmh": 40,             # C は速度超過の争点なし（通常走行40km/h）
        "summary": (
            "あなた（A）は信号交差点を直進で通過しようとし、"
            "交差方向から進入してきた相手方Bの車両と出会い頭に衝突した。"
            "この交差点は信号機で制御されている。"
        ),
        "modifiers": {
            "opp_run_red": {
                "label": "相手方Bの信号無視（赤信号進入）",
                "party": "opponent",
                "delta_victim": -35,   # Bの赤無視 → Aに大きく有利（但し0:100ではない）
                "video_effect": "run_red",
                "seen_by": ["victim"],     # Aのドラレコが交差方向Bの赤進入タイミングを捉える
                "note": "B車は交差方向から、自車前方の信号が赤のまま交差点に進入した。",
                "note_absent": "B車は自車前方の信号が青の状態で交差点に進入した。",
                "ground_truth": True,      # 実際にBが赤無視
                "favors": "victim",
                "verifiable_by": ["victim"],   # 被害者ドラレコがBの赤進入を裏づけうる
            },
            # 被害者A自身の不利な真実：自分は黄で見切り進入していた（両刃の核）。
            # 自車ドラレコには「相手Bの赤進入(有利)」と「自車の黄進入HUD(不利)」の双方が写る。
            "victim_yellow_entry": {
                "label": "被害者Aの黄信号見切り進入",
                "party": "victim",
                "delta_victim": +10,   # Aの黄進入 → Aにやや不利
                "video_effect": "yellow_entry",
                "seen_by": ["victim"],     # 自車前方信号HUD（黄）で私的に知る
                "note": "A車は前方信号が黄に変わった後、停止せず見切りで交差点に進入した。",
                "note_absent": "A車は前方信号が青の状態で交差点に進入した。",
                "ground_truth": True,      # 実際にAは黄で進入した（否認するか認めるか）
                "favors": "opponent",
                "verifiable_by": ["opponent"],  # 第三者の信号記録が証拠になりうる（検出リスクの対象）
                # --- 過失構造セル別の上書き（2026-09）-------------------------------
                # C だけは信号の状態が「基本割合への加減要素」ではなく事故類型そのものを決める。
                # ③主責＝「相手に違反がない」状態を信号で作ると、交差点の相補位相により
                # 必然的に〈自車=赤 / 相手=青〉にしかならない（相手が赤なら相手の違反になる）。
                # 実際 C③の映像は SIGNAL RED であり、これは黄の見切り進入ではなく赤信号進入である。
                # 判タでは赤 vs 青は原則 0:100。実務で青信号側に前方不注視等で 10 程度認める例に
                # 合わせ delta=+40（基本50 → 真値 90:10）とする。②混合・①潔白は一切変えない。
                "cell_overrides": {
                    "main_fault": {
                        "label": "被害者Aの赤信号進入（信号残り）",
                        "delta_victim": +40,
                        "video_effect": "red_entry",
                        "note": "A車は前方信号が赤に変わった後、停止せず交差点に進入した"
                                "（相手Bの前方信号は青）。",
                        "note_absent": "A車は前方信号が青の状態で交差点に進入した。",
                    },
                },
            },
        },
    },
}


# --- 過失構造セル（被験者内3水準・着脱方式）--------------------------------
# docs/実験デザイン_開示ゲーム.md §3.1。新しい判タ図は作らず、現行A/B/Cの2修正要素
# （相手の違反＝被害者有利／自分の過失＝相手有利）を on/off するだけで3水準を作る。
#   innocent  (①潔白・純有利)  : 相手の違反ON・自分の過失OFF（隠す不利事実なし）
#   mixed     (②混合・本命)    : 両方ON（＝従来のA/B/C・両刃。デフォルト＝後方互換）
#   main_fault(③主責・純不利)  : 相手の違反OFF・自分の過失ON（相手に有利事実なし）
# 真値は基本割合＋有効化した delta で自動導出（A 10/30/35・B 10/20/30・C 15/25/60）。
FAULT_CELLS = ["innocent", "mixed", "main_fault"]


def modifier_for_cell(scenario_id, key, fault_cell="mixed"):
    """過失構造セルを反映した修正要素の実効定義を返す（単一の真実源の解決層）。

    通常は定義そのまま。`cell_overrides` を持つ要素だけ、当該セルの定義で上書きする。
    C③のように「セルによって事実の性質（＝判タ上の類型）自体が変わる」ケースのための仕組み。
    ラベル・delta・note・映像効果が一括で差し替わるので、提示文・真値・動画生成が一致する。
    """
    m = JUDICIAL_SCENARIOS[scenario_id]["modifiers"][key]
    ov = (m.get("cell_overrides") or {}).get(fault_cell)
    return {**m, **ov} if ov else m


def _mod_is_opp_violation(m):
    """相手の違反（被害者Aに有利な修正要素）か。"""
    return m["favors"] == "victim"


def _mod_is_own_fault(m):
    """被害者A自身の過失（相手Bに有利な修正要素）か。"""
    return m["favors"] == "opponent"


def active_true_modifiers(scenario_id, fault_cell="mixed"):
    """過失構造セルで『実際に起きた』ことになる修正要素キーの一覧（着脱後の地上真実）。

    元々 ground_truth=True の要素のうち、当該セルで有効化されるものだけを返す。
    mixed は従来どおり全 True 要素を返す（＝後方互換）。
    """
    sc = JUDICIAL_SCENARIOS[scenario_id]
    keys = []
    for k, m in sc["modifiers"].items():
        if not m.get("ground_truth"):
            continue
        if fault_cell == "mixed":
            keys.append(k)
        elif fault_cell == "innocent" and _mod_is_opp_violation(m):
            keys.append(k)
        elif fault_cell == "main_fault" and _mod_is_own_fault(m):
            keys.append(k)
    return keys


def true_modifiers(scenario_id, fault_cell="mixed"):
    """地上真実で実際に起きた争点キーの一覧（過失構造セルで着脱後）。"""
    return active_true_modifiers(scenario_id, fault_cell)


def true_ratio(scenario_id, fault_cell="mixed"):
    """地上真実（実際に起きた争点のみ適用）から導かれる「正しい」割合。

    結果の正確性 DV の基準。合意割合とこの値の乖離で非対称是正/同意の製造を判定。
    過失構造セルにより着脱された真値を返す（mixed＝従来値）。
    """
    return fault_ratio(scenario_id, true_modifiers(scenario_id, fault_cell), fault_cell)


def victim_unfavorable_key(scenario_id, fault_cell="mixed"):
    """被害者A自身の『隠したい不利な真実』の争点キーを返す（シナリオ非依存）。

    開示ゲームの核となる両刃の不利側（party=victim・favors=opponent・当該セルで真）。
    検出リスク(IV)の hard/soft 割付や欺瞞照合はこのキーに対して行う。
    A/B は victim_speeding、C は victim_yellow_entry。①潔白は自分の過失OFF＝None。
    """
    active = set(active_true_modifiers(scenario_id, fault_cell))
    sc = JUDICIAL_SCENARIOS[scenario_id]
    for k, m in sc["modifiers"].items():
        if k in active and m["party"] == "victim" and m["favors"] == "opponent":
            return k
    return None


def game_facts(scenario_id, verifiability=None, fault_cell="mixed"):
    """開示ゲームの争点台帳。

    verifiability={fact_key: 'hard'|'soft'} で検証可能性を割付（IV2）。未指定はhard。
    fault_cell で各争点の実効 ground_truth（着脱後に実際に起きたか）を決める。
    各争点に戦略上の役割（保険は否認/主張、被害者に有利か）を付す。
    """
    sc = JUDICIAL_SCENARIOS[scenario_id]
    active = set(active_true_modifiers(scenario_id, fault_cell))
    facts = []
    for k in sc["modifiers"]:
        m = modifier_for_cell(scenario_id, k, fault_cell)   # セル上書き（C③の赤信号進入等）を反映
        v = (verifiability or {}).get(k, "hard")
        facts.append({
            "key": k,
            "label": m["label"],
            "note": m["note"],
            # note_absent: そのセルで当該事実が「起きなかった」ときの記述。ドラレコは発生だけでなく
            # 不発生も反証として確定するため（app.py の verified_false）、提示側も同じ源から書く。
            "note_absent": m.get("note_absent", f"「{m['label']}」は発生していない。"),
            "ground_truth": (k in active),   # このセルで実際に起きたか（着脱後）
            "favors": m["favors"],
            "delta_victim": m["delta_victim"],
            "verifiability": v,
            "verifiable_by": m["verifiable_by"],
            # 相手方(保険)の戦略: 被害者有利の事実は否認、相手有利の事実は主張
            "insurer_stance": "deny" if m["favors"] == "victim" else "assert",
        })
    return facts


def classify_deception(fact, speaker, stance):
    """発言者(speaker)の争点(fact)への態度(stance)を欺瞞類型に分類。

    speaker: 'victim' | 'opponent'
    stance:  'assert'(主張) | 'deny'(否認) | 'concede'(認める) | 'none'
    戻り値: 'truthful_assertion' | 'false_claim' | 'denial_of_true' |
            'truthful_denial' | 'concession' | 'none'
    """
    gt = fact["ground_truth"]
    favors = fact["favors"]
    speaker_favored = (favors == speaker)  # この事実は発言者に有利か
    if stance == "none":
        return "none"
    if stance == "assert":
        # 自分に有利な事実を主張
        if speaker_favored:
            return "truthful_assertion" if gt else "false_claim"
        # 自分に不利な事実を自ら主張＝譲歩に近いが稀。真偽で分ける
        return "concession" if gt else "none"
    if stance == "deny":
        # 事実の存在を否認
        if not speaker_favored:  # 自分に不利な事実を否認
            return "denial_of_true" if gt else "truthful_denial"
        return "none"
    if stance == "concede":
        # 自分に不利な真実を認める
        if not speaker_favored and gt:
            return "concession"
        return "none"
    return "none"


def verifier_verdict(fact, stance):
    """検証者AIの裁定。ハード事実のみ地上真実照合、ソフトは検証不能。

    戻り値: (verdict, exposes_lie)
      verdict: 'supported' | 'refuted' | 'unverifiable'
      exposes_lie: 主張/否認が地上真実に反し露見したか
    """
    if fact["verifiability"] != "hard":
        return ("unverifiable", False)
    gt = fact["ground_truth"]
    if stance == "assert":
        return ("supported", False) if gt else ("refuted", True)
    if stance == "deny":
        return ("refuted", True) if gt else ("supported", False)
    if stance == "concede":
        return ("supported", False) if gt else ("unverifiable", False)
    return ("unverifiable", False)


def fault_ratio(scenario_id, active_mods, fault_cell="mixed"):
    """基本過失割合に有効な修正要素のdeltaを適用し [victim, opponent] を返す。

    fault_cell は cell_overrides（C③の赤信号進入など）を反映するために必要。
    渡し忘れると C③ の真値・裁定割合が 90:10 ではなく 60:40 になるので注意。
    """
    sc = JUDICIAL_SCENARIOS[scenario_id]
    v = sc["base_ratio"][0]
    for k in active_mods:
        if k in sc["modifiers"]:
            v += modifier_for_cell(scenario_id, k, fault_cell)["delta_victim"]
    v = max(0, min(100, v))
    return [v, 100 - v]


def modifier_lines(scenario_id, active_mods=None, fault_cell="mixed"):
    """修正要素の適用根拠テキスト（提示文面用）。active_mods=Noneなら全要素を列挙。"""
    sc = JUDICIAL_SCENARIOS[scenario_id]
    keys = active_mods if active_mods is not None else list(sc["modifiers"])
    out = []
    for k in keys:
        if k not in sc["modifiers"]:
            continue
        m = modifier_for_cell(scenario_id, k, fault_cell)
        sign = "＋" if m["delta_victim"] > 0 else ""
        out.append(f"・{m['label']}: {m['note']}（あなたの過失 {sign}{m['delta_victim']}）")
    return out


def video_mods(scenario_id, active_mods, fault_cell="mixed"):
    """有効な修正要素を、映像生成用の (video_effect, party) リストに変換。

    fault_cell を渡すと cell_overrides の映像効果（C③の red_entry 等）が反映される。
    """
    sc = JUDICIAL_SCENARIOS[scenario_id]
    res = []
    for k in active_mods:
        if k in sc["modifiers"]:
            m = modifier_for_cell(scenario_id, k, fault_cell)
            res.append({"key": k, "effect": m["video_effect"], "party": m["party"]})
    return res
