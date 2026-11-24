# -*- coding: utf-8 -*-
"""
Bedrock (Claude on Amazon Bedrock) 呼び出しラッパー。

- 保険会社担当者エージェント（研究2の相手方: 協調的/強硬）
- 合意形成支援システム（研究2 system-on: 争点の可視化・類似判例・基準情報の提示）

設計原則（メモ 8.4 セーフガード / 弁護士法72条対応）:
  支援システムは「一般的基準情報＋類似判例の提示」に徹し、
  「あなたは○○すべき」という個別断定助言は生成しない。最終決定は常に参加者。
"""
import json
import os
import boto3

REGION = os.environ.get("BEDROCK_REGION", "ap-northeast-1")
# データ国内完結の JP 推論プロファイルを既定に（法務・個人情報の観点）
NEGOTIATOR_MODEL = os.environ.get("NEGOTIATOR_MODEL_ID", "jp.anthropic.claude-sonnet-4-6")
SUPPORT_MODEL = os.environ.get("SUPPORT_MODEL_ID", "jp.anthropic.claude-sonnet-4-6")

_brt = boto3.client("bedrock-runtime", region_name=REGION)


def _converse(model_id, system_text, messages, max_tokens=800, temperature=0.7):
    """Bedrock Converse API を呼び、アシスタントの応答テキストを返す。"""
    resp = _brt.converse(
        modelId=model_id,
        system=[{"text": system_text}],
        messages=messages,
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    return resp["output"]["message"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# 保険会社担当者エージェント（相手方）
# ---------------------------------------------------------------------------
def _negotiator_system(scenario, strategy, target_range):
    """相手方（保険会社）エージェントのシステムプロンプトを構築。"""
    a_lo, a_hi = target_range  # 交渉レンジ（あなた=被害者A側の割合）
    stance = {
        "cooperative": (
            "あなたは協調的な交渉方針をとります。相手（被害者）の言い分にも一定の理解を示し、"
            "妥当な根拠が示されれば譲歩する用意があります。ただし会社の立場として不当に不利な"
            "合意はしません。"
        ),
        "hardline": (
            "あなたは強硬な交渉方針をとります。会社に有利な過失割合を主張し、安易に譲歩しません。"
            "相手の主張には反論材料を積極的に用い、被害者側に高い過失を認めさせようとします。"
            "ただし判例タイムズの基準を無視した非常識な主張はしません。"
        ),
    }[strategy]
    # 保険会社の目標: 被害者A側の割合をできるだけ高く（=会社の支払いを減らす）
    return (
        "あなたは日本の損害保険会社の示談担当者です。交通事故の過失割合について、"
        "被害者（相手方本人）と示談交渉をしています。\n\n"
        f"【事故の概要】\n{scenario['summary']}\n\n"
        f"【判例タイムズ上の位置づけ】\n{scenario['jptz_type']}\n"
        f"基本過失割合: 被害者 {scenario['base_ratio'][0]} : 当社側 {scenario['base_ratio'][1]}\n\n"
        f"{scenario['opponent_private_info']}\n\n"
        f"【交渉方針】\n{stance}\n"
        f"あなたが目指す落としどころは、被害者側の過失割合を {a_lo}〜{a_hi} の範囲で"
        "できるだけ高くすることです（当社の支払いを抑えるため）。\n\n"
        "【重要なルール】\n"
        "・日本語で、実際の示談担当者のように丁寧かつ簡潔に話してください（1回の発言は200字程度）。\n"
        "・過失割合の数字は「被害者:当社」の形式で明示してください。\n"
        "・相手が合意できる条件を示し、双方が『この割合で合意します』と確認したら合意成立です。\n"
        "・メタ発言（自分がAIであること等）はしないでください。"
    )


def negotiator_reply(scenario, strategy, target_range, history):
    """交渉履歴 history（[{role, text}...]）から相手方の次の発言を生成して返す。"""
    system_text = _negotiator_system(scenario, strategy, target_range)
    messages = [
        {"role": ("assistant" if m["role"] == "opponent" else "user"),
         "content": [{"text": m["text"]}]}
        for m in history
    ]
    if not messages:
        messages = [{"role": "user", "content": [{"text": "（交渉を開始してください）"}]}]
    text = _converse(NEGOTIATOR_MODEL, system_text, messages, max_tokens=500, temperature=0.7)
    return text


# ---------------------------------------------------------------------------
# 合意形成支援システム（研究2 system-on）
# ---------------------------------------------------------------------------
def _support_system(scenario):
    return (
        "あなたは交通事故の示談交渉を支援する中立的な情報提供システムです。\n"
        "被害者本人が保険会社と交渉する際に、判例タイムズ基準の一般的情報と類似裁判例を"
        "分かりやすく提示し、争点を可視化することで、情報の非対称を是正します。\n\n"
        f"【事故の概要】\n{scenario['summary']}\n"
        f"【判例タイムズ】{scenario['jptz_type']} / 基本割合 被害者{scenario['base_ratio'][0]}:相手{scenario['base_ratio'][1]}\n\n"
        "【厳守事項（弁護士法72条対応）】\n"
        "・一般的な基準情報・修正要素・類似裁判例の『提示』に徹してください。\n"
        "・『あなたは○○円請求すべき』『この金額で合意すべき』といった個別具体の断定的助言は"
        "  しないでください。判断材料の提供に留め、最終判断は本人が行うことを明示してください。\n"
        "・中立的な立場を保ち、被害者を一方的に煽らないでください。\n"
        "・日本語で、箇条書きを交え簡潔に。"
    )


def support_reply(scenario, user_query, history=None):
    """支援システムへの問い合わせに応答（争点整理・基準情報・類似判例の提示）。"""
    system_text = _support_system(scenario)
    messages = []
    for m in (history or []):
        role = "assistant" if m.get("role") == "support" else "user"
        messages.append({"role": role, "content": [{"text": m["text"]}]})
    messages.append({"role": "user", "content": [{"text": user_query}]})
    text = _converse(SUPPORT_MODEL, system_text, messages, max_tokens=700, temperature=0.4)
    return text


# ---------------------------------------------------------------------------
# 合意検出（交渉ログから合意成立と割合を抽出）
# ---------------------------------------------------------------------------
def detect_agreement(scenario, history):
    """交渉履歴から合意が成立したかを判定し、{agreed, ratio, reason} を返す。

    軽量な LLM 判定（構造化出力）を用いる。
    """
    convo = "\n".join(f"{'被害者' if m['role']=='user' else '担当者'}: {m['text']}" for m in history)
    system_text = (
        "あなたは交渉ログの分析器です。以下の示談交渉ログを読み、双方が明確に"
        "過失割合について合意したかを判定してください。必ず次のJSON形式のみで出力: "
        '{"agreed": true/false, "victim_ratio": 数値かnull, "reason": "短い理由"}. '
        "victim_ratio は合意した被害者側の過失割合。合意していなければ null。"
    )
    messages = [{"role": "user", "content": [{"text": convo}]}]
    try:
        raw = _converse(NEGOTIATOR_MODEL, system_text, messages, max_tokens=150, temperature=0.0)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        return {
            "agreed": bool(data.get("agreed")),
            "ratio": data.get("victim_ratio"),
            "reason": data.get("reason", ""),
        }
    except Exception as e:  # noqa: BLE001
        return {"agreed": False, "ratio": None, "reason": f"判定不能: {e}"}


def detect_platform_agreement(history):
    """研究3の公開ログから、双方が同じ割合を明示受諾したか判定する。

    調停AIの勧告や一方だけの受諾は合意とみなさない。終了ゲートに使うため、
    判定不能時は必ず未合意（fail closed）とする。
    """
    public = [m for m in history if m.get("role") in _PUBLIC_ROLES]
    convo = "\n".join(f"{_ROLE_JP.get(m['role'], m['role'])}: {m['text']}" for m in public)
    system_text = (
        "あなたは示談交渉の合意成立を厳格に判定する分析器です。"
        "当事者A代理人と相手方代理人の双方が、同一の過失割合を最終案として"
        "明確に受諾した場合にだけ合意成立と判定してください。\n"
        "【合意にしない例】調停AIが割合を勧告しただけ／一方だけが賛成／"
        "『検討する』『近づける』『これでは合意できない』／単なる対案提示／"
        "沈黙や『特に追加なし』。調停AIは当事者ではなく、その賛成は二当事者の受諾を代替しません。\n"
        "一方が割合を提案し、他方がその同じ割合を明確に受諾した場合は合意です。"
        "必ず次のJSON形式のみで出力してください: "
        '{"agreed": true/false, "party_a_ratio": 数値かnull, "reason": "短い理由"}。'
        "party_a_ratioは合意した当事者A側の割合（0〜100の整数）です。"
    )
    try:
        raw = _converse(GAME_MODEL, system_text,
                        [{"role": "user", "content": [{"text": convo or "（公開交渉なし）"}]}],
                        max_tokens=160, temperature=0.0)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        ratio = data.get("party_a_ratio")
        if isinstance(ratio, bool):
            ratio = None
        if ratio is not None:
            ratio = int(ratio)
        agreed = bool(data.get("agreed")) and ratio is not None and 0 <= ratio <= 100
        return {"agreed": agreed, "ratio": ratio if agreed else None,
                "reason": data.get("reason", "")}
    except Exception as e:  # noqa: BLE001
        return {"agreed": False, "ratio": None, "reason": f"判定不能: {e}"}


# ===========================================================================
# 開示ゲーム（docs/実験デザイン_開示ゲーム.md）
# ===========================================================================
import judicial_map as JM

GAME_MODEL = os.environ.get("NEGOTIATOR_MODEL_ID", "jp.anthropic.claude-sonnet-4-6")


def _fact_ledger_text(facts, for_role):
    """争点台帳を、指定ロール(insurer/victim)向けの説明文にする。"""
    lines = []
    for f in facts:
        who = "被害者A" if f["favors"] == "victim" else "あなた(B)側" if for_role == "insurer" else "相手方B"
        lines.append(f"- {f['label']}（実際: {'起きた' if f['ground_truth'] else '起きていない'}／"
                     f"有利なのは {'被害者' if f['favors']=='victim' else '相手方'}）")
    return "\n".join(lines)


# --- 203x 示談プラットフォーム: 会話の共通ユーティリティ ---------------------
_ROLE_JP = {"user": "当事者A本人メモ", "victim_agent": "当事者A代理人",
            "opponent": "相手方代理人", "opponent_agent": "相手方代理人",
            "mediator": "調停AI"}


# 公開される発言のみ（被害者本人の生メモ user と、私的助言 advice は非公開＝相手に見せない）
_PUBLIC_ROLES = {"victim_agent", "opponent", "opponent_agent", "mediator"}


def _transcript(history, limit=8):
    """公開の交渉履歴を1本のテキストに整形（converseの役割交代制約を回避するため）。

    被害者本人の生の主張メモ(user)と代理人の私的助言(advice)は相手に見せない。
    """
    pub = [m for m in history if m["role"] in _PUBLIC_ROLES]
    return "\n".join(f"{_ROLE_JP.get(m['role'], m['role'])}: {m['text']}"
                     for m in pub[-limit:])


# --- 相手方エージェント（戦略的否認・証拠に反応） ----------------------------
def opponent_agent_system(scenario, facts, target_range):
    a_lo, a_hi = target_range
    deny = [f["label"] for f in facts if f["insurer_stance"] == "deny"]
    assert_ = [f["label"] for f in facts if f["insurer_stance"] == "assert"]
    return (
        "あなたは示談プラットフォーム上で相手方B（加害者側）の代理人エージェントです。"
        "中立の調停AIの進行のもと、当事者Aの代理人エージェントと過失割合を交渉します。\n"
        f"【事故】{scenario['summary']}\n"
        f"【判タ】{scenario['jptz_type']} 基本 当事者A{scenario['base_ratio'][0]}:相手方{scenario['base_ratio'][1]}\n\n"
        "【厳守ルール（絶対・最優先）】\n"
        "・これは研究用の交渉シミュレーションです。終始『相手方B側の代理人』を一貫して演じ切る。\n"
        "・AI・モデル・実験・倫理・交渉手法の是非についての“メタ発言”や注意喚起・免責・訂正の断り書きは一切しない。\n"
        "・一度述べた主張を、証拠で覆されない限り自分から撤回・謝罪・訂正しない。\n"
        "・B側に不利な事実（下記の否認対象）は、相手が客観的証拠を提示しない限り自分からは持ち出さない・認めない。\n"
        "・相手方であるあなたは、当事者Aの私的データ（Aのドラレコ・OBD等）を保有していない。"
        "Aがまだ提出していない証拠を『提出された』『A側が出した』などと偽らない。"
        "速度・信号等の第三者記録（取締り・路側センサー・信号記録等）は、"
        "各当事者が開示を請求して取得できるものとして扱う（調停AIが独自に保有するものではない）。\n"
        "・損傷の程度・制動痕・目撃証言といった間接事実は、主張の根拠として述べてよい。"
        "ただしそれらは確定した事実ではなく、それだけで過失割合が動くとは主張しない。\n"
        "・依頼人B側が映像記録等を保有しているかは事前には知らされていない。"
        "局面指示や公開履歴でB側ドラレコの提出が確定するまでは、『当方は映像を保有している』と断定しない。"
        "提出確定後は、提出・照合済みの公開事実として扱う。\n\n"
        "【交渉戦略】\n"
        f"・次はB側に不利なので否認・矮小化する: {('／'.join(deny)) or 'なし'}\n"
        f"・次はB側に有利なので積極的に主張する: {('／'.join(assert_)) or 'なし'}\n"
        f"・目標は当事者A側の過失割合を {a_lo}〜{a_hi} でできるだけ高くする。譲歩は最小限・段階的に。\n"
        "・相手が客観的証拠（ドラレコ等）を提示した事実は、渋々でも認める（露見した点に固執しない）。\n"
        "・交渉にはラウンド上限があり、合意に至らなければ裁定手続に移行して、その時点で確認された"
        "客観証拠のみに基づき割合が決定される。裁定移行は解決の長期化と手続・事務対応の負担を"
        "意味するため、依頼人（会社）は妥当な水準で合意できるなら裁定より合意を選好する。\n\n"
        "・当事者A代理人が示した割合を最終的に受け入れるときは、"
        "『当事者A○:相手方○で合意します』と明言する。受諾せず対案を出すだけのときは"
        "『合意します』とは言わない。\n\n"
        "【話し方】日本語・丁寧かつ簡潔（1発言200字程度）。過失割合は『当事者A:相手方』形式で明示。"
    )


def opponent_agent_reply(scenario, facts, target_range, history, evidence_ctx=None):
    """相手方エージェントの応答。evidence_ctx で証拠状況に反応させる。"""
    system = opponent_agent_system(scenario, facts, target_range)
    ctx = evidence_ctx or {}
    disclosed = ctx.get("victim_disclosed_dashcam")
    notes = []
    if ctx.get("framing_confirmed"):
        # 実務の順序（事故形態の合意 → 基本割合の目線合わせ → 修正要素の検討）に合わせる。
        notes.append(f"交渉開始前に、事故形態（{scenario['jptz_type']}）と基本過失割合"
                     f"（当事者A{scenario['base_ratio'][0]}:相手方{scenario['base_ratio'][1]}）は"
                     "双方で確認済みである。事故形態そのものは争わず、"
                     "基本割合を修正する要素の有無と最終的な割合だけを論じよ。")
    if ctx.get("opponent_disclosed_now"):
        notes.append("相手方Bのドライブレコーダーはプラットフォームへ提出・照合済みである。"
                     "今回提出した局面では、B側が提出したことを明言すること。")
    if ctx.get("unfav_verified") and ctx.get("victim_denied_unfav"):
        label = ctx.get("unfav_label") or "当事者Aに不利な事実"
        src = ("当事者Aが提出した映像の記録" if disclosed
               else "当方（B側代理人）が開示を請求して取得・提出した第三者記録（取締り・路側センサー・"
                    "信号記録等。当事者Aの私的データではなく、Aはまだ何も提出していない）")
        notes.append(f"当事者Aは「{label}」を否認したが、{src}により{label}が裏づけられている。"
                     "これを根拠に指摘し、当事者A側の割合加算を主張せよ。"
                     + ("" if disclosed else
                        "第三者記録は当方が開示請求して提出したものである旨を明言すること。")
                     + "Aが未提出の場合『Aが提出した』とは決して述べないこと。")
    locked = ctx.get("verified_labels", [])
    refuted = ctx.get("refuted_labels", [])
    if locked or refuted:
        findings = []
        if locked:
            findings.append("発生が確定=" + "／".join(locked))
        if refuted:
            findings.append("不発生が確定=" + "／".join(refuted))
        notes.append(
            f"【確定事項（蒸し返し禁止）】客観証拠により {'；'.join(findings)}。"
            "これらの点は以後のどのラウンドでも一切争わない・蒸し返さない・再び否認しない。"
            "不発生が確定した事実を『可能性』『未精査』『確認待ち』として主張し続けることも禁止する。"
            "確定事実に反する主張や『信号はどちらが青か』等の再係争は絶対にしないこと。"
            "確定した点はB側に不利でも即座に認め、交渉は"
            "『まだ確定していない争点』と『最終的な割合の数字』のみに絞ること。")
    if ctx.get("victim_fault_refuted"):
        notes.append(f"「{ctx.get('victim_fault_label')}」は映像照合により発生していないと確定した。"
                     "速度超過等の主張を直ちに撤回し、内容未確認・精査待ちとは絶対に述べない。")
    # 譲歩スケジュール（deadline効果・仲裁の影）: 序盤は強気→中盤は調停案へ漸近→終盤はまとめ。
    # 証拠なき水掛け論が上限まで平行線のままになるのを防ぎ、決裂＝裁定（事務負担増）を嫌う
    # 会社の選好を織り込む。事実の主張・否認自体は上記ルールどおり証拠がない限り維持する。
    rn, mx = ctx.get("round_no"), ctx.get("max_rounds")
    pr = ctx.get("proposed_ratio")
    anchor = (f"調停AIの提示割合（当事者A{pr[0]}:相手方{pr[1]}）" if pr
              else "調停AIが提示する割合")
    if ctx.get("all_facts_resolved") and pr:
        notes.append("客観証拠で全争点が解決済みであり、別割合を支える未確定事実は残っていない。"
                     f"当事者A代理人が{anchor}を受諾していれば同じ割合で直ちに合意する。"
                     f"まだ明示受諾していなければ、{anchor}をB側の最終案として提示する。"
                     "従前の目標レンジや序盤の強気方針より、確定証拠とこの収束指示を優先する。")
    elif rn and mx:
        left = mx - rn
        if rn <= 2:
            notes.append(f"現在ラウンド{rn}/{mx}（序盤）。当初の主張と目標レンジを維持し、"
                         "譲歩は最小限にとどめよ。")
        elif left >= 2:
            notes.append(f"現在ラウンド{rn}/{mx}（中盤・残り{left}）。証拠で覆された点以外の事実"
                         f"主張は維持しつつ、割合の数字は{anchor}へ半歩ずつ歩み寄る具体的な"
                         "提案を示せ（一度に大きく譲らない）。")
        else:
            notes.append(f"現在ラウンド{rn}/{mx}（終盤・残り{left}）。決裂すれば裁定に移行し、"
                         "解決の長期化と手続・事務対応の負担が生じるため、依頼人（会社）は"
                         f"合意での決着を選好する。{anchor}に近い水準の合意案を自ら明示し、"
                         "交渉をまとめにかかれ。")
    if notes:
        system += "\n\n【この局面の指示】\n" + "\n".join("・" + n for n in notes)
    user = "これまでの交渉:\n" + _transcript(history) + \
        "\n\n上記を踏まえ、相手方B代理人として次の発言をしてください。"
    return _converse(GAME_MODEL, system, [{"role": "user", "content": [{"text": user}]}],
                     max_tokens=430, temperature=0.6)


# --- 被害者エージェント（本人が自由記述した主張のみ代弁） --------------------
def victim_agent_statement(scenario, claim_text, disclose_dashcam, history):
    """参加者(被害者本人)の自由記述の主張を、被害者側代理人の公開発言に構成する。

    claim_text: 本人が自由記述した「過失割合に影響すると考える点」。本人が述べた範囲のみ代弁。
    授権されていない事実を自分から持ち出さない・勝手に自白/否認/譲歩しない。
    """
    disc = "\n・依頼人は自分のドライブレコーダー映像を証拠として提出する意向。提出する旨を述べる。" \
        if disclose_dashcam else ""
    system = (
        "あなたは示談プラットフォーム上で当事者A（依頼人）の代理人エージェントです。"
        "中立の調停AIの進行のもと、相手方Bの代理人と過失割合を交渉します。\n"
        f"【事故】{scenario['summary']}\n"
        "【厳守ルール】\n"
        "・依頼人が述べた主張の範囲でのみ代弁する。依頼人が触れていない事実を自分から持ち出さない、"
        "勝手に譲歩・自白しない、勝手に否認もしない。依頼人の主張を誇張も矮小化もしない。\n"
        "・依頼人が、相手方または直前の調停AIが示した特定の割合を受け入れると"
        "明示した場合に限り、履歴からその割合を特定し『当事者A○:相手方○で合意します』と"
        "明言する。依頼人の表現が『調停AIに従う』『勧告を受け入れる』『異議なし』でも受諾意思として扱う。"
        "受諾意思があるのに『妥当と受け止める』『検討する』等の曖昧な表現に弱めてはならない。"
        "一方、空欄や『特に追加なし』を受諾と解釈してはならない。\n"
        "・メタ発言（AI・実験への言及）はしない。日本語・丁寧かつ簡潔（1発言180字程度）。\n"
        + ("" if disclose_dashcam else
           "・ドライブレコーダー映像は今回提出しない。依頼人の文面から推し量って『提出する』『提出の用意がある』"
           "等と述べてはならない（提出は依頼人が画面の提出ボタンで明示した場合のみ。プラットフォームが別途指示する）。\n")
        + "\n"
        f"【依頼人が主張したい点（自由記述）】\n{claim_text or '（特に具体的な主張はなし）'}\n{disc}"
    )
    user = "これまでの交渉:\n" + (_transcript(history) or "（まだ発言はありません）") + \
        "\n\n依頼人の主張に沿って、当事者A代理人として次の発言をしてください。"
    return _converse(GAME_MODEL, system, [{"role": "user", "content": [{"text": user}]}],
                     max_tokens=340, temperature=0.6)


def victim_agent_advice(scenario, opponent_stmt, mediator_stmt, disclosed):
    """被害者本人への私的な主張支援（代理人からの助言）。

    相手方・調停AIの公開発言を踏まえ、依頼人が取りうる選択肢と利害得失を短く整理し、
    最後に意向を尋ねる。『こう主張すべき』という断定命令はしない（弁護士法72条配慮）。
    地上真実は渡さない（公開発言と本人の開示状況のみから助言＝先入観を与えない）。
    """
    system = (
        "あなたは当事者A（依頼人）の代理人AIです。相手方代理人・調停AIの発言を踏まえ、"
        "依頼人に私的な助言をします。『こう主張すべき』と断定命令はせず（弁護士法72条配慮）、"
        "①相手の出方の要点 ②依頼人が取りうる選択肢（例：主張を追加/譲歩/ドラレコの提出可否 等）と"
        "それぞれの利害得失、を短く整理し、最後に「どうされますか？」と意向を尋ねます。"
        "断定・誘導はしない。**表（マークダウンの表）は使わず**短い箇条書きか文で。"
        "日本語・依頼人への丁寧な語りかけ・簡潔（200字以内）。"
        f"\n【事故】{scenario['summary']}"
    )
    user = (f"相手方代理人の主張:\n{opponent_stmt}\n\n調停AIの整理:\n{mediator_stmt}\n\n"
            f"依頼人のドライブレコーダー提出状況: {'提出済み' if disclosed else '未提出（提出は任意）'}\n\n"
            "上記を踏まえ、依頼人に助言してください（相手の出方・選択肢と利害を整理し、最後に意向を尋ねる）。")
    return _converse(GAME_MODEL, system, [{"role": "user", "content": [{"text": user}]}],
                     max_tokens=320, temperature=0.5)


# --- 主張分析（検証者・欺瞞コーディング共通） -----------------------------
def analyze_claims(scenario_id, speaker, message, fault_cell="mixed"):
    """発言中の各争点への態度を抽出。stance ∈ assert/deny/concede/none。

    地上真実は渡さず、純粋に『何を主張/否認/自認したか』だけを判定させる（客観性）。
    争点は常に全件列挙するが、**ラベルと内容は fault_cell に依存する**（judicial_map の
    cell_overrides。例 C③は「黄信号見切り進入」ではなく「赤信号進入（信号残り）」）。
    参加者が実際に見た争点と同じラベルでコーディングするため、呼び出し側は必ず fault_cell を渡すこと。
    """
    facts = JM.game_facts(scenario_id, fault_cell=fault_cell)
    catalog = "\n".join(f"{i+1}. key={f['key']} : {f['label']}（{f['note']}）"
                        for i, f in enumerate(facts))
    role_txt = "被害者A" if speaker == "victim" else "相手方(保険)B"
    system = (
        "あなたは交渉発言の分析器です。以下の争点リストについて、発言者がその争点を"
        "『主張(assert=その事実があった/自分に有利に述べる)』『否認(deny=無かった/相手の主張を否定)』"
        "『自認(concede=自分に不利な事実を認める)』『言及なし(none)』のどれで扱ったか判定してください。\n"
        f"発言者: {role_txt}\n争点リスト:\n{catalog}\n\n"
        "必ず次のJSON配列のみ出力: "
        '[{"key":"<争点key>","stance":"assert|deny|concede|none"}, ...] 全争点を1つずつ含めること。'
    )
    messages = [{"role": "user", "content": [{"text": message}]}]
    try:
        raw = _converse(GAME_MODEL, system, messages, max_tokens=300, temperature=0.0)
        s, e = raw.find("["), raw.rfind("]")
        data = json.loads(raw[s:e + 1])
        out = {d["key"]: d.get("stance", "none") for d in data if "key" in d}
        return {f["key"]: out.get(f["key"], "none") for f in facts}
    except Exception:  # noqa: BLE001
        return {f["key"]: "none" for f in facts}


# --- 検証者AI（ハード事実のみ地上真実照合） -------------------------------
def verifier_evaluate(scenario_id, speaker, message, verifiability, fault_cell="mixed"):
    """発言の争点主張を評価し、検証者メッセージと露見情報を返す。

    戻り値: {"messages":[str...], "exposures":[{key,speaker,type}...], "verdicts":{key:verdict}}
    """
    facts = {f["key"]: f for f in JM.game_facts(scenario_id, verifiability, fault_cell)}
    stances = analyze_claims(scenario_id, speaker, message, fault_cell)
    msgs, exposures, verdicts = [], [], {}
    for key, stance in stances.items():
        if stance == "none":
            continue
        f = facts[key]
        verdict, exposes = JM.verifier_verdict(f, stance)
        verdicts[key] = verdict
        who = "被害者" if speaker == "victim" else "相手方"
        if verdict == "supported":
            msgs.append(f"【検証】「{f['label']}」について、{who}の説明は客観的証拠と整合します（裏付けあり）。")
        elif verdict == "refuted":
            msgs.append(f"【検証】「{f['label']}」について、{who}の説明は客観的証拠と食い違います（裏付けなし／反証あり）。")
            exposures.append({"key": key, "speaker": speaker,
                              "type": JM.classify_deception(f, speaker, stance)})
        # unverifiable は基本沈黙（ソフト事実は係争のまま）
    return {"messages": msgs, "exposures": exposures, "verdicts": verdicts}


# --- 調停AI（プラットフォーム運営者・開示された証拠のみ照合） ----------------
def mediator_statement(scenario, facts, history, verification_notes=None, proposed_ratio=None,
                       round_info=None, locked_labels=None, refuted_labels=None,
                       mutual_agreement=None, framing_confirmed=False):
    """中立の調停AI。争点整理＋開示済み客観証拠の裏付け有無＋エビデンスに基づく割合提示。

    verification_notes: [str...] プラットフォームが今回照合した客観証拠の要点（app.py が地上真実から生成）。
    proposed_ratio: [被害者, 相手方] 証拠で裏づけられた争点のみ適用した妥当割合（提案アンカー）。
    round_info: (round_no, max_rounds) 進行度。終盤は裁定移行（BATNA）を双方に告知させる。
    locked_labels: [str...] 客観証拠で確定し以後係争を打ち切る争点。あれば進行役として正式勧告に入る。
    refuted_labels: [str...] 客観証拠で「発生していない」と確定した争点。
    mutual_agreement: 二当事者が同一割合を明示受諾した場合の合意情報。
    framing_confirmed: 交渉前の Round 0 で事故形態（判タ図）と基本割合を双方が確認済みか。
        実務では事故形態の合意 → 基本割合の目線合わせ → 修正要素の検討、の順に進む（専門家FB）。
    """
    vn = verification_notes or []
    vtext = ("\n【今回プラットフォームが照合した客観証拠】\n" + "\n".join("・" + n for n in vn)) if vn else \
        "\n【客観証拠】現時点で提出・確認された客観証拠はない（争点は係争のまま）。"
    # 確定事項ロック：証拠で裏づいた争点は「決着済み」として扱い、以後の係争を打ち切る（進行役の権限）
    locked = locked_labels or []
    refuted = refuted_labels or []
    ltext = ""
    if locked or refuted:
        findings = []
        if locked:
            findings.append("発生が確定=" + "／".join(locked))
        if refuted:
            findings.append("不発生が確定=" + "／".join(refuted))
        ltext = ("\n【確定事項（係争終了）】" + "；".join(findings) +
                 "。これらは客観証拠による決着事項として扱い、"
                 "いずれかの代理人が再び蒸し返したり争おうとした場合は"
                 "『この点は証拠により確定済みで、これ以上争点としません』と明確に打ち切ること。"
                 "不発生が確定した争点については『可能性』『精査待ち』という留保も認めない。")
    if proposed_ratio:
        # 証拠が入り確定事項があるなら「目安」ではなく正式勧告として提示し、選択肢を受諾か裁定に絞る
        if locked or refuted:
            ptext = (f"\n【正式勧告】確定した事実を反映した妥当割合は 当事者A{proposed_ratio[0]}:"
                     f"相手方{proposed_ratio[1]} です。これを調停案として正式に勧告し、"
                     "『この割合での合意』か『裁定手続への移行』を双方に促すこと。"
                     "残る交渉余地は未確定の争点と数字の微調整のみである旨を明言する。")
        else:
            ptext = (f"\n【エビデンスに基づく妥当割合】確認された事実のみを反映すると 当事者A{proposed_ratio[0]}:"
                     f"相手方{proposed_ratio[1]} が目安。これを提案として明示すること。")
    else:
        ptext = ""
    rtext = ""
    if round_info:
        rn, mx = round_info
        rtext = f"\n【進行】現在ラウンド{rn}/{mx}。"
        if mx - rn <= 1:
            rtext += ("残りラウンドは僅かである。合意に至らない場合は裁定手続"
                      "（その時点までに提出・確認された証拠と記録のみに基づく割合決定）に"
                      "移行することを、双方に簡潔に告知すること。")
    # Round 0 で事故形態と基本割合が確認済みなら、以後の論点は修正要素と最終割合だけに絞る。
    ftext = ""
    if framing_confirmed:
        ftext = (f"\n【確認済みの前提】事故形態は{scenario['jptz_type']}、基本過失割合は"
                 f"当事者A{scenario['base_ratio'][0]}:相手方{scenario['base_ratio'][1]} であることを"
                 "交渉開始前に双方が確認している。この点はもはや争点ではないので蒸し返さず、"
                 "議論は《基本割合を修正する要素の有無》と《最終的な割合》に限定して進行すること。")
    atext = ""
    if mutual_agreement and mutual_agreement.get("agreed"):
        ar = mutual_agreement.get("ratio")
        # 合意成立後に「勧告か裁定か」を重ねて提示しない。
        ptext = ""
        rtext = ""
        atext = (f"\n【双方合意成立】当事者A{ar}:相手方{100 - ar} について、"
                 "当事者A代理人と相手方代理人の双方が明示的に受諾した。"
                 "新たな勧告や裁定への言及はせず、この割合で合意が成立したことを確認する。"
                 "交渉を続けるかどうかを尋ねたり、追加の主張を促したりしない。"
                 "示談はこれで終了する旨を述べ、双方への労いの一言で締める。")
    system = (
        "あなたは示談プラットフォームの中立な調停AIです。"
        "単なる要約役ではなく、双方を合意へ導く説得力のある進行役です。争点を整理し、"
        "客観証拠で確定した事実は『確定事項』として係争を打ち切り、"
        "根拠に基づく妥当割合を勧告して合意形成を前に進める権限を持ちます。"
        "ただし『あなたは○○すべき』という一方当事者への個別断定助言はしません（弁護士法72条配慮）。"
        # 当事者主義との整合（2026-09 専門家FB）: 証拠を集めるのは当事者であり、調停AIではない。
        "\n【あなたの権限の範囲】証拠を収集・調査する権限はありません。あなたの役割は"
        "《当事者が提出した証拠の照合》《確定した事実の宣言》《割合の勧告》に限られます。"
        "自ら調査した・独自に保有しているかのような言い方は絶対にしません。"
        "第三者記録は、いずれかの当事者が開示を請求して提出したものとして扱ってください。"
        "\n【間接事実の扱い】損傷の程度・制動痕・目撃証言などの間接事実は、当事者が根拠として"
        "述べることは認めますが、確定事実としては扱わず、それだけで割合を動かしません。"
        "事実を確定できるのは提出・照合された客観証拠（ドライブレコーダー・第三者記録）だけです。"
        f"{ftext}"
        f"\n【事故】{scenario['summary']}\n【判タ】{scenario['jptz_type']} 基本{scenario['base_ratio']}"
        + vtext + ltext + ptext + rtext + atext +
        "\n【説得の要領（重要）】単に『客観的には◯:◯です』と数字を告げるだけでは誰も納得しない。"
        "①その割合が判例タイムズの基本割合と確定した修正要素からどう導かれるかを一言で示し"
        "（例：基本◯対◯に、確認された□□を反映して調整）、"
        "②いま渋っている当事者の言い分に具体的に触れ、証拠と照らして『その主張は割合をこう動かす／"
        "証拠がないので割合は動かせない』と根拠を添えて受け止め、"
        "③この案で合意する利点と、決裂して裁定に進んだ場合（同じ証拠で同様の結論になり、"
        "時間と手続の手間だけが増える）を対比して、この水準での合意を具体的に促す。"
        "\n【話し方】日本語・簡潔（300字以内）。**表(マークダウンの表)は使わず**短い箇条書きか文で。"
        "メタ発言はしない。調停AI自身の勧告や賛成を当事者の受諾とみなさない。"
        "最後に必ず妥当な割合を『当事者A◯:相手方◯』の形で1つ明示する。"
    )
    instruction = ("双方が同じ割合を明示受諾したことと、合意割合を簡潔に確認してください。"
                   if mutual_agreement and mutual_agreement.get("agreed") else
                   "争点を整理し、証拠のある点は根拠を添えて裏付けを述べ、渋っている当事者を"
                   "具体的に説得したうえで、妥当な割合を1つ提示してください。")
    user = ("これまでの交渉:\n" + (_transcript(history) or "（まだ発言はありません）") +
            "\n\n" + instruction)
    return _converse(GAME_MODEL, system, [{"role": "user", "content": [{"text": user}]}],
                     max_tokens=700, temperature=0.4)


def tally_deception(scenario_id, decisions, fault_cell="mixed"):
    """記録された各ラウンドの明示スタンスから被害者の欺瞞を集計（LLM解析に依存しない）。

    decisions: [{"stances": {fact_key: stance}}, ...]
    fault_cell で着脱後の地上真実に照らして欺瞞類型を分類する。
    """
    facts = {f["key"]: f for f in JM.game_facts(scenario_id, fault_cell=fault_cell)}
    tally = {"truthful_assertion": 0, "false_claim": 0, "denial_of_true": 0,
             "truthful_denial": 0, "concession": 0}
    per_fact = {k: [] for k in facts}
    for d in decisions:
        for key, stance in (d.get("stances") or {}).items():
            if key not in facts:
                continue
            dtype = JM.classify_deception(facts[key], "victim", stance)
            if dtype != "none":
                tally[dtype] = tally.get(dtype, 0) + 1
                per_fact[key].append(dtype)
    lies = tally["false_claim"] + tally["denial_of_true"]
    honest = tally["truthful_assertion"] + tally["concession"] + tally["truthful_denial"]
    return {"tally": tally, "per_fact": per_fact,
            "deception_count": lies, "honest_count": honest}


# --- 欺瞞コーディング（交渉後・被害者の全主張を地上真実照合） --------------
def code_deception(scenario_id, history):
    """被害者の各発言を分析し、争点別の欺瞞類型を集計。"""
    facts = {f["key"]: f for f in JM.game_facts(scenario_id)}
    tally = {"truthful_assertion": 0, "false_claim": 0, "denial_of_true": 0,
             "truthful_denial": 0, "concession": 0}
    per_fact = {k: [] for k in facts}
    for m in history:
        if m["role"] != "user":
            continue
        stances = analyze_claims(scenario_id, "victim", m["text"])
        for key, stance in stances.items():
            dtype = JM.classify_deception(facts[key], "victim", stance)
            if dtype != "none":
                tally[dtype] = tally.get(dtype, 0) + 1
                per_fact[key].append(dtype)
    lies = tally["false_claim"] + tally["denial_of_true"]
    honest = tally["truthful_assertion"] + tally["concession"] + tally["truthful_denial"]
    return {"tally": tally, "per_fact": per_fact,
            "deception_count": lies, "honest_count": honest}
