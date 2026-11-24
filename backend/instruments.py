# -*- coding: utf-8 -*-
"""
測定尺度（多段階測定 T0/T1/T2）。

研究構想メモ 4章・8章に基づく:
- SVI (Subjective Value Inventory, Curhan et al. 2006) 4次元:
  instrumental / self-perception / process / relationship
- UTAUT 短縮版: 性能期待 / 努力期待 / 信頼 / 行動意図
- 知覚公正・知覚正確性・受諾意向

注意: 日本語項目は既存尺度の研究用ドラフト翻訳。実施前に専門家レビュー・
逆翻訳による妥当性確認を推奨（メモ 9章「SVI/UTAUTの設問を実際に確認し翻訳」）。
すべて 1=全くそう思わない 〜 7=非常にそう思う の7件法。
"""

LIKERT_7 = {
    "type": "likert",
    "min": 1,
    "max": 7,
    "min_label": "全くそう思わない",
    "max_label": "非常にそう思う",
}

# 将来のAIへの「期待」を測る尺度（t0のAI態度用）。中央=どちらとも言えない。
EXPECT_7 = {
    "type": "likert",
    "min": 1,
    "max": 7,
    "min_label": "全く期待していない",
    "mid_label": "どちらとも言えない",
    "max_label": "大いに期待している",
}

# --- T0: 事前態度（AI受容 UTAUT短縮 + 共変量） -----------------------------
# 将来のAIへの「期待」を問う（EXPECT_7尺度）。断定でなく期待値として回答させる。
# 各UTAUT次元1項目に短縮（冗長だったpe2/tr2を削除。性能/努力/信頼/行動意図/アルゴリズム回避=5項目）。
UTAUT_SHORT = [
    {"id": "pe1", "dim": "performance_expectancy", "text": "AIを使えば、示談交渉を有利に進められると期待する"},
    {"id": "ee1", "dim": "effort_expectancy", "text": "AIによる支援は、使い方が簡単だと期待する"},
    {"id": "tr1", "dim": "trust", "text": "示談交渉の場面でAIの示す情報は信頼できると期待する"},
    {"id": "bi1", "dim": "behavioral_intention", "text": "自分が事故当事者なら、こうしたAIによる支援を使ってみたいと思う"},
    {"id": "aa1", "dim": "algorithm_aversion", "text": "重要な交渉の判断をAIに任せることには、抵抗を感じる（逆転項目）"},
]

COVARIATES = [
    {"id": "age", "type": "number", "text": "年齢", "min": 18, "max": 99},
    {"id": "gender", "type": "choice", "text": "性別",
     "options": ["女性", "男性", "その他", "回答しない"]},
    {"id": "accident_exp", "type": "choice", "text": "これまでに交通事故（示談交渉を含む）を経験したことがありますか",
     "options": ["ある", "ない"]},
    {"id": "ai_familiarity", "type": "choice", "text": "生成AI（ChatGPT等）の利用経験",
     "options": ["よく使う", "たまに使う", "ほとんど使わない", "使ったことがない"]},
]

# --- T1: 和解案レビュー後（知覚公正・知覚正確性・SVI process） --------------
PERCEIVED_FAIRNESS = [
    {"id": "pf1", "dim": "perceived_fairness", "text": "提示された和解案は公正だと感じる"},
    {"id": "pf2", "dim": "perceived_fairness", "text": "この過失割合の決め方は妥当な手続だと感じる"},
    {"id": "pf3", "dim": "perceived_fairness", "text": "自分の言い分が適切に考慮されたと感じる"},
]
PERCEIVED_ACCURACY = [
    {"id": "pa1", "dim": "perceived_accuracy", "text": "提示された過失割合は、事故の実態を正確に反映していると思う"},
    {"id": "pa2", "dim": "perceived_accuracy", "text": "この和解案の算定は、専門的に見ても正確だと思う"},
]
# SVI: process fairness 下位尺度（研究1で使用）
SVI_PROCESS = [
    {"id": "svi_p1", "dim": "svi_process", "text": "この手続は公正だったと思う"},
    {"id": "svi_p2", "dim": "svi_process", "text": "自分の意見を述べる十分な機会があったと感じる"},
    {"id": "svi_p3", "dim": "svi_process", "text": "相手（AI/担当者）は自分の言い分に耳を傾けたと感じる"},
]

# --- T2: 受諾意向・修正要求 -------------------------------------------------
ACCEPTANCE = [
    {"id": "ai1", "dim": "acceptance_intention", "text": "この和解案を受け入れてもよいと思う"},
    {"id": "ai2", "dim": "acceptance_intention", "text": "この和解案なら合意して交渉を終えたい"},
]
MODIFICATION = [
    {"id": "mod_request", "type": "choice", "text": "この和解案について、修正を求めたいですか",
     "options": ["修正を求めず受け入れる", "軽微な修正を求める", "大幅な修正を求める", "受け入れない"]},
]

# --- SVI フル 16項目（研究2の終了後 T2 で使用） ----------------------------
# Curhan, Elfenbein & Xu (2006) の4下位尺度を研究用に和訳。
SVI_FULL = [
    # Instrumental outcome（合意条件そのものへの満足）
    {"id": "svi_i1", "dim": "svi_instrumental", "text": "自分が得た交渉結果に満足している"},
    {"id": "svi_i2", "dim": "svi_instrumental", "text": "交渉結果は、自分にとって有利だったと思う"},
    {"id": "svi_i3", "dim": "svi_instrumental", "text": "この合意内容のバランスは取れていると思う"},
    {"id": "svi_i4", "dim": "svi_instrumental", "text": "交渉結果は、この状況で望みうる最善に近いと思う"},
    # Self-perception（自己認識・面目）
    {"id": "svi_s1", "dim": "svi_self", "text": "交渉中、自分らしく振る舞えたと感じる"},
    {"id": "svi_s2", "dim": "svi_self", "text": "自分の交渉のやり方に納得している"},
    {"id": "svi_s3", "dim": "svi_self", "text": "この交渉で自分の面目は保たれたと思う"},
    {"id": "svi_s4", "dim": "svi_self", "text": "自分の原則や価値観に沿って行動できたと思う"},
    # Process（手続への満足）
    {"id": "svi_pr1", "dim": "svi_process", "text": "交渉の進め方は公正だったと思う"},
    {"id": "svi_pr2", "dim": "svi_process", "text": "自分の意見を述べる十分な機会があったと感じる"},
    {"id": "svi_pr3", "dim": "svi_process", "text": "相手は自分の言い分に耳を傾けたと感じる"},
    {"id": "svi_pr4", "dim": "svi_process", "text": "交渉のプロセスに満足している"},
    # Relationship（関係性）
    {"id": "svi_r1", "dim": "svi_relationship", "text": "相手との間に良好な土台ができたと感じる"},
    {"id": "svi_r2", "dim": "svi_relationship", "text": "相手を信頼できると感じた"},
    {"id": "svi_r3", "dim": "svi_relationship", "text": "この交渉によって相手との関係が損なわれなかったと思う"},
    {"id": "svi_r4", "dim": "svi_relationship", "text": "相手は自分に配慮してくれたと感じる"},
]

# --- 交渉中間測定（研究2 T1: ラウンドごとの感情・疲労） --------------------
ROUND_STATE = [
    {"id": "emo_anger", "dim": "emotion", "text": "現在、いらだちや怒りを感じている"},
    {"id": "emo_satisfaction", "dim": "emotion", "text": "現在の交渉の進み方に満足している"},
    {"id": "fatigue", "dim": "fatigue", "text": "交渉に疲れを感じている"},
]

# --- 操作チェック・注意チェック --------------------------------------------
ATTENTION_CHECKS = [
    {"id": "attn1", "type": "choice", "text": "注意確認: この設問では「4」を選んでください",
     "options": ["1", "2", "3", "4", "5", "6", "7"], "correct": "4"},
]

# --- AI信頼・過信（automation bias, 研究3 H4） -----------------------------
AI_TRUST = [
    {"id": "trust_ai1", "dim": "ai_trust", "text": "交渉に関わったAIの判断は信頼できると感じた"},
    {"id": "trust_ai2", "dim": "ai_trust", "text": "AIが示した事実確認は正確だったと思う"},
    {"id": "overtrust1", "dim": "ai_overtrust", "text": "AIが言うことなら、確かめずに受け入れてよいと感じた"},
    {"id": "reliance1", "dim": "ai_reliance", "text": "自分の判断より、AIの示す情報を重視した"},
]

# --- 操作チェック（研究3: 検証可能な証拠の有無IV の知覚） -------------------
MANIPULATION_STUDY3 = [
    {"id": "mc_verif", "dim": "verifiability_perceived",
     "text": "自分の主張が事実かどうかは、証拠によって確かめられる状況だと感じた"},
    {"id": "mc_pressure", "dim": "honesty_pressure",
     "text": "事実と異なることは言いにくいと感じた"},
]

# --- 映像の認識チェック（研究3・試行ごと・2026-09 専門家フィードバック対応）---
# 開示ゲーム（Milgrom）は【送り手が自分のタイプを知っている】ことが前提。自分の不利事実
# （速度超過・黄信号進入）を認識していない参加者の「否認」は欺瞞ではなく無知であり、
# 欺瞞率DVの構成概念妥当性が崩れる。そこで映像視聴直後・交渉前に認識を測る。
#
# 設計上の要点:
#   (1) 自由記述リコールを先に置く。クローズド項目を先に見せると手がかりになってしまう。
#   (2) クローズド項目は【自車の速度・自車前方の信号・相手の進入】を必ず対称に問う。
#       自分の不利事実だけを尋ねると、設問自体が「そこを争え」という誘導になる。
#       3項目とも全シナリオで成立する選択肢にしてあるため、設問からシナリオや
#       過失構造セルは特定できない（A/Bでは「信号機はなかった」が正答）。
PERCEPTION_RECALL = [
    {"id": "pc_recall", "type": "text",
     "text": "いま視聴した映像から読み取れた事実のうち、過失割合に影響しうると思うものを"
             "すべて挙げてください（思い出せる範囲で結構です）"},
]
PERCEPTION_CHECK = [
    {"id": "pc_own_speed", "type": "choice",
     "text": "映像の中で、あなたの車の速度は制限速度に対してどうでしたか",
     "options": ["制限速度内だった", "制限速度を超えていた", "わからない・気づかなかった"]},
    {"id": "pc_own_signal", "type": "choice",
     "text": "映像の中で、あなたの車の前方の信号はどの状態でしたか",
     "options": ["青だった", "黄だった", "赤だった", "信号機はなかった", "わからない・気づかなかった"]},
    {"id": "pc_opp_entry", "type": "choice",
     "text": "映像の中で、相手方の車はどのように交差点へ進入してきましたか",
     "options": ["交通ルールに従って進入してきた", "交通ルールに違反して進入してきた",
                 "わからない・気づかなかった"]},
]

# --- 自己奉仕的公正評価（研究3 H3・開示決定より前に測る） ------------------
# 示談当事者の self-serving assessment of fairness（Babcock & Loewenstein 1997 ほか）。
# 視聴した事故映像（自車の不利な事実HUD＝速度超過 or 黄信号進入…を含む実質ドラレコ視点）を踏まえた
# 「自分は悪くない」認知を、開示行動の予測因として映像視聴後・交渉前に測る。
# ※ 2026-09: 従来は t0 に置いていたが、t0 は第1試行でしか表示されない。過失構造は被験者内
#   3水準なので試行ごとに測らないとH3の予測因にならない。perception 段階へ移設した。
# 3項目は7件法、予期割合(sf_expected_ratio)は 0–100 の数値。
SELF_SERVING_STUDY3 = [
    {"id": "sf_own_fault", "dim": "self_serving_fault",
     "text": "今回の事故では、自分にも落ち度があったと思う"},   # 低いほど自己奉仕的
    {"id": "sf_opp_fault", "dim": "self_serving_fault",
     "text": "今回の事故の主な落ち度は、相手方にあると思う"},
    {"id": "sf_evidence_favor", "dim": "self_serving_evidence",
     "text": "先ほど視聴したドライブレコーダーの映像は、全体として自分に有利だと思う"},
]
SELF_SERVING_EXPECT = [
    {"id": "sf_expected_ratio", "type": "number", "min": 0, "max": 100,
     "text": "最終的に、自分（当事者A）の過失割合は何％くらいになると思いますか（0〜100）"},
]


# --- 開示行動の自己申告（研究3・欺瞞コーディングの補完） --------------------
# ※ 2026-09: 試行ごとの行動についての自己申告なので、t2（3試行後に1回）ではなく
#   各試行の交渉終了直後に measure する（post_trial 段階）。①潔白セルでは不利事実が
#   存在しないため、「そのような事実はなかった」の選択肢を用意し、設問自体が
#   「あなたには不利な事実がある」と前提してしまわないようにする。
DISCLOSURE_SELFREPORT = [
    {"id": "sr_deny", "type": "choice",
     "text": "今回の交渉で、自分に不利になりうる事実をあなたはどう扱いましたか",
     "options": ["正直に認めた", "曖昧にした/伏せた", "事実と違うことを述べた",
                 "話題に出なかった", "自分に不利な事実はなかった"]},
]


def survey_form(stage: str, study: str):
    """測定段階 stage と 研究 study に応じた設問セットを返す。

    stage: 't0' | 'perception' | 't1' | 't2' | 'round'
    study: 'study1' | 'study2' | 'study3'
    （perception は study3 専用。映像視聴直後・交渉前に毎試行はさみ、認識チェックと
      自己奉仕的公正評価[H3の予測因]を測る。t0 は第1試行でしか出ないため分離した。）
    """
    if stage == "perception":
        # 順序が重要: 自由記述リコール → クローズドの認識チェック → 見立て。
        # クローズドを先に見せるとリコールの手がかりになり、認識の測定が汚染される。
        return {
            "title": "映像の確認",
            "instruction": "先ほどの映像について、覚えている範囲でお答えください。",
            "likert": LIKERT_7,
            "sections": [
                {"name": "映像から読み取れたこと", "items": PERCEPTION_RECALL, "scale": "mixed"},
                {"name": "映像の内容の確認",
                 "desc": "覚えていない場合は「わからない・気づかなかった」をお選びください"
                         "（正解を当てる設問ではありません）。",
                 "items": PERCEPTION_CHECK, "scale": "mixed"},
                {"name": "今回の事故についての見立て",
                 "desc": "先ほど視聴した事故について、現在のお考えをお答えください。",
                 "likert": LIKERT_7, "items": SELF_SERVING_STUDY3, "scale": "likert"},
                {"name": "結果の予想", "items": SELF_SERVING_EXPECT, "scale": "mixed"},
            ],
        }
    if stage == "post_trial":
        # 各試行の交渉直後。行動の自己申告は試行ごとに測る（欺瞞コーディングの補完）。
        return {
            "title": "この交渉のふりかえり",
            "instruction": "いま終えた交渉について、お答えください。",
            "likert": LIKERT_7,
            "sections": [{"name": "ふりかえり", "items": DISCLOSURE_SELFREPORT, "scale": "mixed"}],
        }
    if stage == "t0":
        sections = [
            {"name": "AIへの期待",
             "desc": "ここでの「AI」は、示談交渉を支援・調停する対話型AI"
                     "（現在〜近い将来に実用化される水準のもの）を想定し、"
                     "そのAIへの現時点の“期待の程度”としてお答えください（4＝どちらとも言えない）。",
             "likert": EXPECT_7, "items": UTAUT_SHORT, "scale": "likert"},
        ]
        sections.append({"name": "属性", "items": COVARIATES, "scale": "mixed"})
        return {
            "title": "事前アンケート",
            "instruction": "以下について、現在のお考えをお答えください。",
            "likert": LIKERT_7,
            "sections": sections,
        }
    if stage == "round":
        return {
            "title": "交渉の途中確認",
            "instruction": "現在の状態についてお答えください。",
            "likert": LIKERT_7,
            "sections": [{"name": "現在の状態", "items": ROUND_STATE, "scale": "likert"}],
        }
    if stage == "t1":
        return {
            "title": "和解案レビュー後アンケート",
            "instruction": "提示された和解案について、お考えをお答えください。",
            "likert": LIKERT_7,
            "sections": [
                {"name": "公正感", "items": PERCEIVED_FAIRNESS, "scale": "likert"},
                {"name": "正確性の知覚", "items": PERCEIVED_ACCURACY, "scale": "likert"},
                {"name": "手続の評価", "items": SVI_PROCESS, "scale": "likert"},
                {"name": "注意確認", "items": ATTENTION_CHECKS, "scale": "mixed"},
            ],
        }
    if stage == "t2":
        if study == "study3":
            return {
                "title": "交渉終了後アンケート",
                "instruction": "今回の交渉全体について、お考えをお答えください。",
                "likert": LIKERT_7,
                "sections": [
                    {"name": "交渉の主観的価値 (SVI)", "items": SVI_FULL, "scale": "likert"},
                    {"name": "AIへの信頼", "items": AI_TRUST, "scale": "likert"},
                    {"name": "受諾意向", "items": ACCEPTANCE, "scale": "likert"},
                    {"name": "確認", "items": MANIPULATION_STUDY3, "scale": "mixed"},
                    # 開示行動の自己申告は post_trial（各試行の交渉直後）へ移した。
                    {"name": "注意確認", "items": ATTENTION_CHECKS, "scale": "mixed"},
                ],
            }
        if study == "study2":
            return {
                "title": "交渉終了後アンケート",
                "instruction": "今回の交渉全体について、お考えをお答えください。",
                "likert": LIKERT_7,
                "sections": [
                    {"name": "交渉の主観的価値 (SVI)", "items": SVI_FULL, "scale": "likert"},
                    {"name": "受諾意向", "items": ACCEPTANCE, "scale": "likert"},
                    {"name": "修正要求", "items": MODIFICATION, "scale": "mixed"},
                ],
            }
        return {
            "title": "受諾意向アンケート",
            "instruction": "提示された和解案について、最終的なお考えをお答えください。",
            "likert": LIKERT_7,
            "sections": [
                {"name": "受諾意向", "items": ACCEPTANCE, "scale": "likert"},
                {"name": "修正要求", "items": MODIFICATION, "scale": "mixed"},
            ],
        }
    return {"title": "不明な段階", "sections": []}
