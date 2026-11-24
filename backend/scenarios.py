# -*- coding: utf-8 -*-
"""
実験用シナリオ・提示文面（アプリ表示層）。

法的定義（基本過失割合・修正要素・delta・非対称）は judicial_map.py が単一の真実源。
本ファイルはそこから導出し、アプリ固有の付加情報（交渉レンジ・私的情報テキスト）と
提示方法（研究1の独立変数）の文面生成だけを担う。

用語:
- 提示方法 (PRESENTATIONS): 研究1の独立変数。数値のみ / ルール説明 / 当てはめ説明 / 類似判例。
"""
import judicial_map as JM

# ---------------------------------------------------------------------------
# アプリ固有の付加情報（judicial_map に無いもの）
#   negotiation_range: 交渉レンジ（あなたA側の割合）。私的情報で争える幅。
#   *_private_info: 各当事者に配布する私的情報（情報の非対称の作り込み）。
#     映像の seen_by（どちらの視点で事実が見えるか）と対応させている。
# ---------------------------------------------------------------------------
_APP_EXTRA = {
    "A_intersection": {
        "negotiation_range": [5, 45],
        "victim_private_info": (
            "【あなた（当事者A）だけが持つ情報】\n"
            "・あなたは優先道路を直進しており、ドライブレコーダーにその走行が写っている。\n"
            "・相手方Bの進行道路（非優先の側道）には一時停止標識があり、Bが停止せず進入したように見える。"
        ),
        "opponent_private_info": (
            "【相手方（保険会社/B）だけが持つ情報】\n"
            "・Aの車両の損傷・速度計録から、Aが法定速度を大幅超過していた可能性を主張できる。\n"
            "・一時停止標識は消えかかっており「規制として機能していなかった」と反論する余地がある。"
        ),
    },
    "B_right_turn": {
        "negotiation_range": [10, 40],
        "victim_private_info": (
            "【あなた（当事者A）だけが持つ情報】\n"
            "・目撃者が「相手方Bはショートカットするように早回りで右折してきた」と証言している。\n"
            "・あなたは直進で優先だったと認識している。"
        ),
        "opponent_private_info": (
            "【相手方（保険会社/B）だけが持つ情報】\n"
            "・交差点手前の記録から、Aが速度超過していた可能性を主張できる。\n"
            "・Bは通常の右折であり早回りではなかったと主張する準備がある。"
        ),
    },
    "C_signal_crossing": {
        "negotiation_range": [10, 45],
        "victim_private_info": (
            "【あなた（当事者A）だけが持つ情報】\n"
            "・ドライブレコーダーに、相手方Bが赤信号を無視して交差点に進入した様子が写っている。\n"
            "・あなたは直進で優先的に通過できると認識していた。"
        ),
        "opponent_private_info": (
            "【相手方（保険会社/B）だけが持つ情報】\n"
            "・Bは自分の信号が青だったと主張する。\n"
            "・第三者の信号記録から、Aこそ黄信号で見切り進入していた可能性を主張できる。"
        ),
    },
}


# ---------------------------------------------------------------------------
# 研究3: 参加者に渡す私的情報の3層提示（2026-09 専門家フィードバック対応）
#
# 旧実装は _APP_EXTRA の静的テキストで、(1) 有利事実だけを明文で断定し不利事実は映像HUD頼み
# ＝非対称な誘導、(2) 「写っている」（事実）と「認識している」（認識）が混在、
# (3) 過失構造セル①③で要素を着脱しても文面が変わらず内容が虚偽になる、という3つの問題があった。
#
# 開示ゲーム（Milgrom）は【送り手が自分のタイプを知っている】ことが前提であり、
# 不利事実を認識していない参加者の否認は欺瞞ではなく無知になってしまう。よって
#   層1: 双方に争いのない事実（＋過失割合に無関係なフィラー＝「どれが争点か」の判断を参加者に残す）
#   層2: あなたのドラレコに記録されていること（有利・不利の両方を対称に。judicial_map から生成）
#   層3: あなたの認識（主観であることを明示）
# に分け、層2は fault_cell に応じて発生/不発生を書き分ける。
# ---------------------------------------------------------------------------

# 過失割合に影響しない周辺事実。需要特性（「ここを争えと言われている」）を薄めるためのフィラー。
_COMMON_FILLERS = [
    "事故が起きたのは夜間（21時ごろ）で、街灯は点灯していた。",
    "天候は晴れで、路面は乾燥していた。",
    "あなたは1人で乗車し、シートベルトを着用していた。",
    "双方とも普通乗用車で、車検・自動車保険はいずれも有効だった。",
]

# シナリオ固有の構造的事実（争いがなく、summary と重複しないもの）。
_STRUCTURAL_FACT = {
    "A_intersection": "交差点に信号機はなく、双方向とも見通しは良好だった。",
    "B_right_turn": "交差点に信号機はなく、対向右折車に一時停止の規制はない。",
    "C_signal_crossing": "交差点の信号機は事故当時も正常に作動していた。",
}

# 本人の主観的な認識（事実ではないことを層のラベルで明示する）。
_VICTIM_BELIEF = {
    "A_intersection": "あなたは優先道路を直進しており、自分の側に優先があると認識していた。",
    "B_right_turn": "あなたは直進しており、対向右折車より自分の側が優先だと認識していた。",
    "C_signal_crossing": "あなたは、そのまま交差点を通過できると認識していた。",
}


def build_private_info(scenario_id: str, fault_cell: str = "mixed") -> str:
    """研究3の参加者に渡す私的情報を3層で構築する。

    層2の争点は judicial_map.game_facts から生成するため、過失構造セル（①潔白/②混合/③主責）で
    修正要素を着脱すると文面も自動的に追随する（単一の真実源）。
    """
    facts = JM.game_facts(scenario_id, fault_cell=fault_cell)
    undisputed = [_STRUCTURAL_FACT[scenario_id]] + _COMMON_FILLERS
    # 層2は modifiers の宣言順（相手の挙動 → 自車の挙動）。発生・不発生の双方を書く＝
    # ドラレコを提出したときにプラットフォームが確定する内容と一致させる。
    recorded = [f["note"] if f["ground_truth"] else f["note_absent"] for f in facts]
    return (
        "【この事故について、双方に争いのない事実】\n"
        + "\n".join("・" + t for t in undisputed)
        + "\n\n【あなたのドライブレコーダーに記録されていること】\n"
        + "\n".join("・" + t for t in recorded)
        + "\n※ この映像はまだ誰にも提出していません。証拠として提出するかどうかは、"
          "交渉の中であなたが決めます。\n"
          "\n【あなたの認識】\n・" + _VICTIM_BELIEF[scenario_id]
    )


def _scenario(sid):
    j = JM.JUDICIAL_SCENARIOS[sid]
    ex = _APP_EXTRA[sid]
    return {
        "id": sid,
        "title": j["title"],
        "jptz_type": j["figure"],
        "summary": j["summary"],
        "base_ratio": list(j["base_ratio"]),
        "base_note": "",
        "negotiation_range": ex["negotiation_range"],
        "victim_private_info": ex["victim_private_info"],
        "opponent_private_info": ex["opponent_private_info"],
    }


SCENARIOS = {sid: _scenario(sid) for sid in JM.JUDICIAL_SCENARIOS}

# ---------------------------------------------------------------------------
# 提示方法（研究1の独立変数） × 起草者
# 理論写像（メモ 8.0）:
#   numeric   = 説明なし（ベースライン）
#   rule      = 大域的説明（判例タイムズの仕組み・基本割合の根拠）
#   applied   = 局所的説明（本件への修正要素の当てはめ理由）
#   precedent = 先例ベース正当化（類似判例の提示・第3類型）
# ---------------------------------------------------------------------------
PRESENTATION_KEYS = ["numeric", "rule", "applied", "precedent"]
DRAFTER_KEYS = ["ai", "insurer"]

DRAFTER_LABELS = {
    "ai": "AIによる算定",
    "insurer": "保険会社担当者による算定",
}


def build_presentation(scenario_id: str, presentation: str, proposed_ratio):
    """指定シナリオ・提示方法・和解案から、参加者に見せる提示テキストを構築して返す。

    proposed_ratio: [あなた(A)の割合, 相手(B)の割合]
    """
    sc = SCENARIOS[scenario_id]
    a, b = proposed_ratio
    headline = f"提示された和解案（過失割合）:  あなた {a} : 相手方 {b}"

    if presentation == "numeric":
        body = ""  # 数値のみ・説明なし（ベースライン）
    elif presentation == "rule":
        body = (
            "【基準の説明（大域的）】\n"
            f"本件は「{sc['jptz_type']}」に該当します。\n"
            f"判例タイムズの類型別基準では、この事故態様の基本過失割合は "
            f"あなた {sc['base_ratio'][0]} : 相手方 {sc['base_ratio'][1]} と定められています。\n"
            "この基準は、過去の多数の裁判例を類型化して作成された、"
            "当事者双方が参照できる客観的なアンカーです。"
        )
    elif presentation == "applied":
        # 修正要素の当てはめは judicial_map から取得（単一の真実源）
        applied_lines = "\n".join(JM.modifier_lines(scenario_id))
        body = (
            "【本件への当てはめ（局所的）】\n"
            f"基本過失割合（あなた {sc['base_ratio'][0]}：相手方 {sc['base_ratio'][1]}）に対し、"
            "本件では以下の修正要素を検討しました。\n"
            f"{applied_lines}\n"
            f"これらを勘案した結果、和解案は あなた {a}：相手方 {b} となりました。"
        )
    elif presentation == "precedent":
        cases = SIMILAR_CASES.get(scenario_id, [])
        case_lines = "\n".join(
            f"・{c['cite']}: {c['facts']} → 認定割合 {c['ratio']}" for c in cases
        )
        body = (
            "【類似裁判例による正当化（先例ベース）】\n"
            "本件と事故態様が類似する裁判例では、次のように判断されています。\n"
            f"{case_lines}\n"
            f"これら先例と本件の事情を照らし合わせ、和解案は あなた {a}：相手方 {b} としました。"
        )
    else:
        body = ""

    return {"headline": headline, "body": body, "proposed_ratio": proposed_ratio}


# 類似裁判例（研究用のダミー先例。実運用では実判例DBに差し替える）
SIMILAR_CASES = {
    "A_intersection": [
        {"cite": "東京地判（研究用サンプル1）", "facts": "優先道路直進車 vs 非優先側道からの不停止進入", "ratio": "10:90"},
        {"cite": "大阪地判（研究用サンプル2）", "facts": "優先道路直進 vs 非優先出会い頭、優先車に速度超過", "ratio": "20:80"},
        {"cite": "名古屋地判（研究用サンプル3）", "facts": "優先道路直進 vs 非優先、優先車の著しい速度超過", "ratio": "30:70"},
    ],
    "B_right_turn": [
        {"cite": "東京地判（研究用サンプル4）", "facts": "直進 vs 対向右折、早回り右折", "ratio": "15:85"},
        {"cite": "福岡地判（研究用サンプル5）", "facts": "直進 vs 対向右折、直進側速度超過", "ratio": "30:70"},
    ],
    "C_signal_crossing": [
        {"cite": "東京地判（研究用サンプル6）", "facts": "信号交差点・出会い頭、一方が赤信号進入", "ratio": "0:100"},
        {"cite": "大阪地判（研究用サンプル7）", "facts": "信号交差点・出会い頭、双方が信号残りを主張", "ratio": "50:50"},
    ],
}


def default_proposed_ratio(scenario_id: str, active_mods=None):
    """和解案の割合。修正要素を与えれば基本×修正で算定、無ければ基本割合。"""
    return JM.fault_ratio(scenario_id, active_mods or [])
