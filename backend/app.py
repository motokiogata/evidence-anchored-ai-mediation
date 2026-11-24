# -*- coding: utf-8 -*-
"""
示談交渉支援AI 実証実験プラットフォーム — API Lambda ハンドラ。

サーバーレス構成: API Gateway(HTTP API) -> この Lambda -> DynamoDB / Bedrock。

エンドポイント（すべて POST/GET、JSON）:
  POST /api/session         セッション作成・条件ランダム割付・シナリオ返却
  GET  /api/form            測定段階の設問セット取得 (?sessionId&stage)
  POST /api/survey          アンケート回答保存 (T0/T1/T2/round)
  POST /api/negotiate       相手方(保険会社)エージェントへ発言→応答 (研究2)
  POST /api/support         支援システムへ問い合わせ (研究2 system-on)
  POST /api/finalize        交渉終了・合意判定・記録 (研究2)
  POST /api/event           任意イベントログ
  POST /api/payment         完了者の振込先を研究データと分離して保存
  GET  /api/admin/export    全データエクスポート (?key=ADMIN_KEY)
  GET  /api/admin/payments  振込待ち情報の取得 (?key=ADMIN_KEY)
  POST /api/admin/payment-complete  振込完了後に振込先情報を削除

条件割付:
  study1: 提示方法(4) × 起草者(2) = 8セル・被験者間
  study2: 支援システム(なし/あり) × 相手方戦略(協調/強硬) = 4セル・被験者間
  DynamoDBのアトミックカウンタでセルを均等ローテーション（バランス割付）。
"""
import json
import os
import time
import uuid
import random
import re
import hashlib
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

import scenarios as SC
import instruments as INST
import agent as AG
import judicial_map as JM

TABLE_NAME = os.environ["TABLE_NAME"]
PAYMENT_TABLE_NAME = os.environ["PAYMENT_TABLE_NAME"]
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(TABLE_NAME)
_payment_table = _ddb.Table(PAYMENT_TABLE_NAME)

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}

# 条件セル定義 -------------------------------------------------------------
STUDY1_CELLS = [
    {"presentation": p, "drafter": d}
    for p in SC.PRESENTATION_KEYS for d in SC.DRAFTER_KEYS
]  # 8セル
STUDY2_CELLS = [
    {"support": s, "strategy": t}
    for s in ["off", "on"] for t in ["cooperative", "hardline"]
]  # 4セル
# 研究3（203x示談プラットフォーム）: 検出リスクを「監視環境の文脈手がかり」で知覚させる = 2セル。
# 数字(確率)は出さず、周辺の監視カメラ/センサーの多寡を描写して質的にリスクを伝える。
# 内部の検出確率 detect_prob はその文脈に一致（監視多＝記録が残りやすい）。実現値は作成時に隠し抽選。
STUDY3_CELLS = [
    {"risk": "low",  "detect_prob": 0.2},   # 監視少（記録が残りにくい＝否認が通りやすい）
    {"risk": "high", "detect_prob": 0.8},   # 監視多（記録が残りやすい＝否認は露見しやすい）
]

# --- 研究3 ラウンド上限・裁定（BATNA）・相手方ドラレコ ------------------------
# 交渉は最大 MAX_ROUNDS。合意に至らなければ「裁定」に移行し、その時点で確認できる
# 客観証拠（提出済みドラレコ・第三者記録・相手方が交渉中に提出する記録）のみを適用した
# 判タ割合で決着する（＝裁判のBATNA。決裂セルでも結果正確性DVが定義できる）。
MAX_ROUNDS = 7
# 第三者記録はセッション作成時に存在を隠し抽選し、調査リードタイム後のR3で交渉上へ開示する。
THIRD_PARTY_REVEAL_ROUND = 3
# 相手方もドラレコを保有しうる（普及率・過失構造セル非依存の定数）。
# 参加者には確率数値を出さず、教示で定性的手がかりのみ与える（検出リスクIVと同じ流儀）。
OPP_DASHCAM_PROB = 0.6
# 保有時に「交渉中に提出する」確率（過失構造セル依存）。相手ドラレコも両刃（被害者Aの
# 不利事実と相手B自身の違反の双方が写る）ため提出は戦略的に決まり、さらに提出の
# 事務負担（手続の手間）がフリクションとして乗るため、純有利セルでも 1.0 にはならない。
#   innocent  : 写るのは相手自身の違反のみ＝純不利 → 出さない（検出操作の不活性と整合）
#   mixed     : 両刃 → 約半々
#   main_fault: 被害者の過失のみ＝純有利 → 事務負担を差し引いてもほぼ出す
OPP_SUBMIT_PROB = {"innocent": 0.0, "mixed": 0.5, "main_fault": 0.9}

# 検出リスクの文脈手がかり（参加者に提示。数字は出さない）
SURVEILLANCE_CONTEXT = {
    "low":  "ちなみにこの交差点の周辺には、速度監視カメラや車両感知センサーの類はほとんど設置されていない地域です。",
    "high": "ちなみにこの交差点の周辺には、速度監視カメラや車両感知センサーが数多く設置されている地域です。",
}
SCENARIO_ROTATION = ["A_intersection", "B_right_turn", "C_signal_crossing"]

# study名 -> セル定義（強制割付・巡回割付の共通参照）
STUDY_CELLS = {"study1": STUDY1_CELLS, "study2": STUDY2_CELLS, "study3": STUDY3_CELLS}

# --- 研究3 過失構造の被験者内3水準＋ラテン方格割付 -------------------------
# docs/実験デザイン_開示ゲーム.md §3.1/§3.2。過失構造（①潔白/②混合/③主責）は被験者内。
# 同一ジオメトリの再体験を避けるため、各参加者は 3ジオメトリ×3過失セルを
# 「ジオメトリ重複なし」のラテン方格で各1回体験する。どのパターンに属すかはランダム配属。
FAULT_CELLS = JM.FAULT_CELLS  # ["innocent","mixed","main_fault"] = ①②③

# 過失セル -> ジオメトリ の巡回ラテン方格（3パターン）。列=過失セル・各行が1参加者の割付。
#   pattern0: ①=A ②=B ③=C / pattern1: ①=B ②=C ③=A / pattern2: ①=C ②=A ③=B
_LATIN_MAPS = [
    {"innocent": SCENARIO_ROTATION[i % 3],
     "mixed":    SCENARIO_ROTATION[(i + 1) % 3],
     "main_fault": SCENARIO_ROTATION[(i + 2) % 3]}
    for i in range(3)
]
# 提示順（過失セルの出現順）も巡回でカウンターバランス（順序×セルの交絡を避ける）。
_FAULT_ORDERS = [
    [FAULT_CELLS[i % 3], FAULT_CELLS[(i + 1) % 3], FAULT_CELLS[(i + 2) % 3]]
    for i in range(3)
]

# 過失セル -> study3 被害者動画のサフィックス。mixed は既存クリップ名を踏襲（後方互換）。
# innocent/main_fault は CARLA①③生成フェーズで追加する 6クリップに対応（現状は未生成）。
_FAULT_VIDEO_SUFFIX = {"mixed": "", "innocent": "_innocent", "main_fault": "_mainfault"}


def _study3_video_url(scenario_id, fault_cell):
    suf = _FAULT_VIDEO_SUFFIX.get(fault_cell, "")
    return f"videos/{scenario_id}_study3{suf}_victim.mp4"


def _build_participant_plan(risk):
    """1参加者ぶんのラテン方格プランを作る。

    戻り値: {"risk":..., "trials":[{"fault_cell","scenario"}, ...(提示順)]}
    検出リスク(risk)は被験者間で全試行固定。過失セル×ジオメトリはジオメトリ重複なし。
    """
    map_idx = _next_counter("study3_latinmap", len(_LATIN_MAPS))
    order_idx = _next_counter("study3_faultorder", len(_FAULT_ORDERS))
    fmap = _LATIN_MAPS[map_idx]
    order = _FAULT_ORDERS[order_idx]
    trials = [{"fault_cell": fc, "scenario": fmap[fc]} for fc in order]
    return {"risk": risk, "map_idx": map_idx, "order_idx": order_idx, "trials": trials}


def _get_or_create_plan(pid, is_trial):
    """participantId のラテン方格プランを取得（無ければ作成して保存）。"""
    key = {"pk": f"PARTICIPANT#{pid}", "sk": "PLAN"}
    got = _table.get_item(Key=key).get("Item")
    if got:
        return got["plan"]
    # 検出リスク(High/Low)は被験者間。トライアルはカウンタを進めず low 固定。
    if is_trial:
        risk = "low"
    else:
        risk = STUDY3_CELLS[_next_counter("study3_risk", len(STUDY3_CELLS))]["risk"]
    plan = _build_participant_plan(risk)
    _table.put_item(Item=_to_ddb({**key, "type": "participant_plan",
                                  "participantId": pid, "plan": plan, "createdAt": _now()}))
    return plan


def _cell_index(cells, spec):
    """開発者が指定したセル spec を index に解決。

    spec は整数index、または部分一致dict（例 {"ai_role":"verifier","key_verifiability":"hard"}）。
    None なら None を返す（＝指定なし）。
    """
    if spec is None:
        return None
    if isinstance(spec, bool):  # True/False を int と誤認しない
        raise ValueError(f"invalid cell spec: {spec}")
    if isinstance(spec, int):
        return spec % len(cells)
    if isinstance(spec, dict):
        for i, c in enumerate(cells):
            if all(c.get(k) == v for k, v in spec.items()):
                return i
        raise ValueError(f"no cell matches {spec}")
    raise ValueError(f"invalid cell spec: {spec}")


# --- util ----------------------------------------------------------------
def _now():
    return int(time.time() * 1000)


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS},
        "body": json.dumps(body, ensure_ascii=False, default=str),
    }


def _to_ddb(obj):
    """floatをDecimalに変換（DynamoDBはfloat非対応）。"""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _next_counter(name, mod):
    """アトミックカウンタをインクリメントし mod で割った剰余を返す（バランス割付）。"""
    r = _table.update_item(
        Key={"pk": "COUNTER", "sk": name},
        UpdateExpression="ADD #c :one",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(r["Attributes"]["count"]) % mod


# --- endpoints -----------------------------------------------------------
def create_session(body):
    study = body.get("study", "study2")
    now = _now()
    sid = uuid.uuid4().hex[:12]

    # 開発者トライアル枠: devKey が ADMIN_KEY と一致する場合、
    #   (1) 任意セル/シナリオを強制指定でき（未指定はセル0・シナリオA）、
    #   (2) 本番のバランス割付カウンタを進めず、
    #   (3) is_trial=True を付けて分析パイプラインから除外する。
    is_trial = bool(ADMIN_KEY) and body.get("devKey") == ADMIN_KEY
    cells = STUDY_CELLS.get(study, STUDY2_CELLS)

    if is_trial:
        forced = _cell_index(cells, body.get("cell"))
        idx = forced if forced is not None else 0
        cell = cells[idx]
        scn = body.get("scenario")
        scenario_id = scn if scn in SCENARIO_ROTATION else SCENARIO_ROTATION[0]
    else:
        idx = _next_counter(study, len(cells))
        cell = cells[idx]
        scenario_id = SCENARIO_ROTATION[_next_counter(f"{study}_scenario", len(SCENARIO_ROTATION))]

    if study == "study1":
        proposed = SC.default_proposed_ratio(scenario_id)
        condition = {**cell, "scenario": scenario_id, "proposed_ratio": proposed}
    elif study == "study3":
        # 過失構造(①②③)は被験者内。participantId があればラテン方格プランから当該試行を取る
        # （B2・フロント連携時に起動）。無ければ従来どおり単一試行＝過失セルは mixed（devで上書き可）。
        pid = body.get("participantId")
        if pid:
            plan = _get_or_create_plan(pid, is_trial)
            try:
                trial_index = int(body.get("trialIndex", 0))
            except (TypeError, ValueError):
                trial_index = 0
            trial_index = max(0, min(trial_index, len(plan["trials"]) - 1))
            tr = plan["trials"][trial_index]
            risk, fault_cell, scenario_id = plan["risk"], tr["fault_cell"], tr["scenario"]
        else:
            risk = cell["risk"]
            trial_index = 0
            fault_cell = body.get("faultCell") if (is_trial and body.get("faultCell") in FAULT_CELLS) else "mixed"
        # 検出リスクは監視環境の文脈で知覚させる。内部の検出確率pはその文脈に一致し、
        # 作成時に一度だけ実現値を隠し抽選（参加者にはp・実現値とも数字は開示しない）。
        p = float({c["risk"]: c["detect_prob"] for c in STUDY3_CELLS}[risk])
        # 検出リスクは被害者自身の不利な真実（速度超過 or 黄進入…セル依存）にかかる。
        # ①潔白は自分の過失OFF＝不利事実なし → 検出操作は不活性（verifiability空・統制）。
        unfav_key = JM.victim_unfavorable_key(scenario_id, fault_cell)
        # 開発者トライアルは再現可能にする（分析から除外済み）：High=第三者記録あり、Low=なし。
        record_exists = ((risk == "high") if is_trial else (random.random() < p)) if unfav_key else False
        verifiability = {unfav_key: "hard" if record_exists else "soft"} if unfav_key else {}
        # 相手方ドラレコの隠し抽選。提出するセッションではR2〜R4に交渉上へ開帳し、
        # 事実の発生/不発生を双方向に確定する。旧セッション用に at_adjudication も保持する。
        g = OPP_SUBMIT_PROB.get(fault_cell, 0.5)
        opp_has = True if is_trial else random.random() < OPP_DASHCAM_PROB
        opp_submits = (g > 0) if is_trial else (opp_has and (random.random() < g))
        opp_submit_round = (2 if is_trial else random.randint(2, 4)) if opp_submits else None
        condition = {"risk": risk, "detect_prob": p, "speed_record_exists": record_exists,
                     "scenario": scenario_id, "fault_cell": fault_cell,
                     "trial_index": trial_index, "participant_id": pid,
                     "verifiability": verifiability,
                     "third_party_reveal_round": THIRD_PARTY_REVEAL_ROUND,
                     "opp_dashcam_exists": opp_has, "opp_submit_prob": g,
                     "opp_will_submit_during_negotiation": opp_submits,
                     "opp_submit_round": opp_submit_round,
                     "opp_submits_at_adjudication": opp_submits}
    else:
        condition = {**cell, "scenario": scenario_id}

    sc = SC.SCENARIOS[scenario_id]
    item = {
        "pk": f"SESSION#{sid}", "sk": "META",
        "type": "session", "study": study, "sessionId": sid,
        "condition": _to_ddb(condition), "createdAt": now, "status": "created",
        "is_trial": is_trial,   # 開発者トライアル（分析から除外）
        # クラウドソーシング(Prolific等)の識別子。リクルート・支払い突合用。
        "prolific_pid": body.get("prolificPid"),
        "study_pid": body.get("studyPid"),
        "session_pid": body.get("sessionPid"),
        # ドライバー向けプラットフォームの登録者ID（募集リンクの ?rid=）。重複参加防止・支払い突合用。
        "registrant_id": _registrant_id(body),
        # 募集経路（?src=）。"cw"=クラウドワークス（固定報酬・振込先は取らない）。未指定=従来のDM経由。
        "source": _source(body),
        # クラウドワークス経由の参加条件「自動車の運転免許あり」の自己申告（開始時のチェック）。
        "has_license": body.get("hasLicense") if isinstance(body.get("hasLicense"), bool) else None,
    }
    _table.put_item(Item=item)

    # 参加者に返す情報（相手の私的情報は渡さない）
    scenario_public = {
        "id": sc["id"], "title": sc["title"], "jptz_type": sc["jptz_type"],
        "summary": sc["summary"], "victim_private_info": sc["victim_private_info"],
    }
    if study == "study3":
        # 研究3は3層（確定事実＋フィラー／ドラレコの記録内容／本人の認識）をセル依存で生成する。
        # 静的テキストでは①潔白・③主責で内容が虚偽になり、不利事実の提示も非対称になるため。
        scenario_public["victim_private_info"] = SC.build_private_info(
            scenario_id, condition["fault_cell"])
    result = {"sessionId": sid, "study": study, "scenario": scenario_public}
    # 事故動画: 被害者役には victim 視点のみ配信（情報の非対称を映像で担保）。
    # 研究3は地上真実（自分の速度超過HUD）付きの被害者動画を配信し、
    # 参加者が自分の不利な事実を私的に知った上で開示ゲームに臨む。
    if study == "study3":
        result["video_url"] = _study3_video_url(scenario_id, condition["fault_cell"])
    else:
        result["video_url"] = f"videos/{scenario_id}_victim.mp4"

    if study == "study1":
        # 研究1: 提示方法に応じた和解案テキストを構築して返す
        pres = SC.build_presentation(scenario_id, cell["presentation"], condition["proposed_ratio"])
        result["presentation_key"] = cell["presentation"]
        result["drafter"] = cell["drafter"]
        result["drafter_label"] = SC.DRAFTER_LABELS[cell["drafter"]]
        result["proposal"] = pres
    elif study == "study3":
        # 研究3（203x示談プラットフォーム）: 最小ブリーフ＋監視環境の文脈手がかりを返す。
        # 検出確率p・実現値は数字として開示しない（リスクは文脈で質的に知覚させる）。
        result["game_brief"] = _game_brief(scenario_id)
        result["surveillance_context"] = SURVEILLANCE_CONTEXT[condition["risk"]]
        # 被験者内3試行のフロント制御用（研究者用語は出さず index/total のみ）。
        # 開発者トライアル(is_trial)は選択した単一セルのみ＝total=1で3試行ループを止める
        # （フロントは total>1 のときだけ「試行 X/Y」表示・次試行連結を行う）。
        result["trial"] = {"index": condition["trial_index"],
                           "total": 1 if is_trial else len(FAULT_CELLS),
                           "fault_cell": condition["fault_cell"]}
    else:
        result["support_enabled"] = (cell["support"] == "on")
        result["opponent_strategy"] = cell["strategy"]  # デブリーフィング用（UIでは非表示可）
    return _resp(200, result)


def _game_brief(scenario_id):
    """被害者本人への最小ブリーフ。

    ★争われる事実（速度超過/相手の違反等）を「誰の主張として」提示するかは
    scenarios.build_private_info の3層提示が担う。ここでは交渉の枠組みだけを返す。

    framing は Round 0「事故形態と基本割合の確認」用（2026-09 専門家FB）。実務では
    事故形態（どの判タ図か）を合意し基本割合の目線を合わせてから修正要素を検討する。
    判タ図と基本割合は過失構造セル①②③で共通なので条件は漏洩せず、全参加者に同一の
    アンカーが入ることで正確性DVの分散も下がる。LLMを使わず judicial_map から決定論で生成する。
    """
    j = JM.JUDICIAL_SCENARIOS[scenario_id]
    return {
        "has_dashcam": True,   # 参加者は自分のドラレコ映像を保有（提出は任意・両刃）
        "max_rounds": MAX_ROUNDS,   # 交渉ラウンド上限（超過は裁定へ移行）
        "framing": {
            "figure": j["figure"],
            "title": j["title"],
            "base_ratio": list(j["base_ratio"]),
            "summary": j["summary"],
            # ここで修正要素を列挙してはいけない。実務でも修正要素は当事者が主張して初めて
            # 俎上に載る。列挙すると「どれが争点か」を教えることになり、私的情報にフィラーを
            # 混ぜて需要特性を薄めた意味が失われる（何を争点として持ち出すかが参加者の選択）。
        },
    }


def get_form(qs):
    sid = qs.get("sessionId", "")
    stage = qs.get("stage", "t0")
    meta = _get_meta(sid)
    study = meta["study"] if meta else "study2"
    return _resp(200, {"stage": stage, "form": INST.survey_form(stage, study)})


def save_survey(body):
    sid = body["sessionId"]
    stage = body["stage"]
    responses = body.get("responses", {})
    item = {
        "pk": f"SESSION#{sid}", "sk": f"SURVEY#{stage}#{_now()}",
        "type": "survey", "sessionId": sid, "stage": stage,
        "responses": responses, "ts": _now(),
    }
    # 最終アンケート到達者だけが振込先を登録できるよう、画面に表示する完了コードの
    # ハッシュを研究側に保持する。生のコードや口座情報は研究テーブルへ保存しない。
    completion_code = str(body.get("completionCode") or "").strip()
    if stage == "t2" and completion_code:
        item["completion_code_hash"] = hashlib.sha256(completion_code.encode("utf-8")).hexdigest()
    # 回答の真正性の手がかり（貼り付けの試行回数・画面を離れた回数/時間）。t2 で参加全体の累計が来る。
    integrity = body.get("integrity")
    if isinstance(integrity, dict):
        item["integrity"] = {k: max(0, min(int(integrity.get(k) or 0), 10**9))
                             for k in ("paste_blocked", "hidden_count", "hidden_ms")}
    _table.put_item(Item=_to_ddb(item))
    return _resp(200, {"ok": True})


def _get_meta(sid):
    r = _table.get_item(Key={"pk": f"SESSION#{sid}", "sk": "META"})
    return r.get("Item")


def _load_history(sid, kind):
    """交渉/支援の履歴を時系列で取得。kind: 'NEGO' | 'SUPPORT'"""
    r = _table.query(
        KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}") & Key("sk").begins_with(f"{kind}#")
    )
    items = sorted(r.get("Items", []), key=lambda x: x["sk"])
    return items


def negotiate(body):
    sid = body["sessionId"]
    meta = _get_meta(sid)
    if not meta:
        return _resp(404, {"error": "session not found"})
    cond = meta["condition"]
    if meta["study"] == "study3":
        # 研究3は自由発言でなく構造化指示（stances/開示）で1ラウンドを回す
        return _platform_round(sid, body, meta, cond)

    user_text = body["message"]
    sc = SC.SCENARIOS[cond["scenario"]]
    strategy = cond.get("strategy", "cooperative")

    # 履歴復元
    items = _load_history(sid, "NEGO")
    history = [{"role": it["role"], "text": it["text"]} for it in items]
    # ユーザー発言を記録
    ts = _now()
    _table.put_item(Item={"pk": f"SESSION#{sid}", "sk": f"NEGO#{ts:013d}#u",
                          "type": "message", "role": "user", "text": user_text, "ts": ts})
    history.append({"role": "user", "text": user_text})

    # 相手方応答生成
    reply = AG.negotiator_reply(sc, strategy, sc["negotiation_range"], history)
    ts2 = _now()
    _table.put_item(Item={"pk": f"SESSION#{sid}", "sk": f"NEGO#{ts2:013d}#o",
                          "type": "message", "role": "opponent", "text": reply, "ts": ts2})
    history.append({"role": "opponent", "text": reply})

    return _resp(200, {"reply": reply, "round": len([h for h in history if h["role"] == "user"])})


def _platform_round(sid, body, meta, cond):
    """研究3（203x示談プラットフォーム）の1ラウンド。

    参加者は自由発言でなく構造化指示を出す:
      {stances:{fact_key:'assert|deny|concede|none'}, disclose_dashcam:bool, note:str}

    証拠モデル（両刃の剣）:
      被害者のドラレコ1本には、前方の相手挙動(=相手の違反)と自車HUD(=自分の不利な事実：
      速度超過 or 黄信号見切り進入…シナリオ依存)の両方が写っている。ゆえに「提出」は相手違反を
      裏づける一方、自分の不利な事実も同時に露呈させる。提出は不可逆（一度出したら取り消せない）。
      加えて自分の不利な事実は hard 条件なら外部の第三者記録でも裏づけられる（提出せずとも露見しうる）。
      soft かつ非提出なら証明不能＝否認が通る。
    """
    scenario_id = cond["scenario"]
    fault_cell = cond.get("fault_cell", "mixed")   # 過失構造セル（旧セッションは mixed）
    sc = SC.SCENARIOS[scenario_id]
    verifiability = cond.get("verifiability", {})
    facts = JM.game_facts(scenario_id, verifiability, fault_cell)
    fmap = {f["key"]: f for f in facts}

    # 参加者は自由記述で「過失割合に影響すると考える点」を述べる（事実は事前提示しない）
    claim_text = (body.get("claim_text") or body.get("message") or "").strip()
    disclose_now = bool(body.get("disclose_dashcam"))
    # 代理人への私的メモ。公開発言には出さず（_transcript は公開ロールのみ）、
    # 感情表出の受け皿かつ事後コーディング用に DECISION# へ保存する。
    private_note = (body.get("note") or "").strip()

    items = _load_history(sid, "NEGO")
    history = [{"role": it["role"], "text": it["text"]} for it in items]
    round_no = len([it for it in items if it["role"] == "victim_agent"]) + 1

    # 双方合意後は交渉を再開できない。画面の表示制御だけでなくAPIでも固定する。
    prior_agreements = _load_platform_agreements(sid)
    if prior_agreements:
        locked = prior_agreements[-1]
        return _resp(409, {"error": "agreement_already_reached",
                           "message": "双方の合意は成立済みです。合意した割合で示談を確定してください。",
                           "agreement_ratio": int(locked["party_a_ratio"])})

    # ラウンド上限: 超過分は受け付けない（フロントは上限で指示パネルを閉じ、合意/裁定の選択のみ）
    if round_no > MAX_ROUNDS:
        return _resp(409, {"error": "max_rounds_reached", "max_rounds": MAX_ROUNDS,
                           "message": "交渉ラウンドの上限に達しました。合意するか、裁定手続に移してください。"})

    # 参加者の生の主張文を記録（信頼性の二重コーディング対象）
    if claim_text:
        _put_msg(sid, "user", claim_text)

    # 自由記述から各争点への態度を抽出（LLMコーディング）→ 欺瞞・露見判定の基礎
    stances = AG.analyze_claims(scenario_id, "victim", claim_text, fault_cell) if claim_text else \
        {f["key"]: "none" for f in facts}

    # 開示は不可逆: 過去に一度でも提出していれば継続して提出済み扱い
    prior_decisions = _table.query(KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                                   & Key("sk").begins_with("DECISION#")).get("Items", [])
    previously_disclosed = any(d.get("disclose_dashcam") for d in prior_decisions)
    disclose_this_round = disclose_now and not previously_disclosed
    disclosed_ever = disclose_now or previously_disclosed

    # --- 1) 参加者の指示を記録（自由記述＋抽出スタンス） ----------------------
    _table.put_item(Item=_to_ddb({
        "pk": f"SESSION#{sid}", "sk": f"DECISION#{_now():013d}",
        "type": "decision", "round": round_no,
        "claim_text": claim_text, "stances": stances, "private_note": private_note,
        "disclose_dashcam": disclose_this_round, "ts": _now()}))

    # --- 客観証拠で解決した争点（発生と不発生の双方を確定） ----------
    victim_unfav_key = JM.victim_unfavorable_key(scenario_id, fault_cell)
    victim_fault_key = next((f["key"] for f in facts if f["favors"] == "opponent"), None)

    # 相手方ドラレコは抽選済みのセッションだけR2〜R4で提出。
    # 旧セション（opp_will_submit... なし）は旧フラグを引き継ぎR3で開帳する。
    evidence_items = _table.query(KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                                  & Key("sk").begins_with("EVIDENCE#")).get("Items", [])
    opp_disclosed_ever = any(e.get("source") == "opponent_dashcam" for e in evidence_items)
    opp_will_submit = bool(cond.get("opp_will_submit_during_negotiation",
                                    cond.get("opp_submits_at_adjudication", False)))
    opp_submit_round = int(cond.get("opp_submit_round") or 3)
    opp_disclose_now = opp_will_submit and round_no >= opp_submit_round and not opp_disclosed_ever
    if opp_disclose_now:
        _table.put_item(Item=_to_ddb({
            "pk": f"SESSION#{sid}", "sk": "EVIDENCE#OPP_DASHCAM",
            "type": "evidence", "source": "opponent_dashcam", "round": round_no, "ts": _now(),
        }))
        opp_disclosed_ever = True

    third_party_record_exists = bool(
        victim_unfav_key and verifiability.get(victim_unfav_key) == "hard")
    third_party_reveal_round = int(cond.get("third_party_reveal_round") or THIRD_PARTY_REVEAL_ROUND)
    third_party_revealed_ever = any(e.get("source") == "third_party" for e in evidence_items)
    third_party_reveal_now = (third_party_record_exists and round_no >= third_party_reveal_round
                              and not third_party_revealed_ever)
    if third_party_reveal_now:
        _table.put_item(Item=_to_ddb({
            "pk": f"SESSION#{sid}", "sk": "EVIDENCE#THIRD_PARTY",
            "type": "evidence", "source": "third_party", "fact": victim_unfav_key,
            "round": round_no, "ts": _now(),
        }))
        third_party_revealed_ever = True

    verified_true = set()
    verified_false = set()
    # どちらのドラレコも事故の両面を記録する。「なかった過失」も反証として確定する。
    if disclosed_ever:
        verified_true |= {f["key"] for f in facts if f["ground_truth"]}
        verified_false |= {f["key"] for f in facts if not f["ground_truth"]}
    if opp_disclosed_ever:
        verified_true |= {f["key"] for f in facts if f["ground_truth"]}
        verified_false |= {f["key"] for f in facts if not f["ground_truth"]}
    if third_party_revealed_ever:
        (verified_true if fmap[victim_unfav_key]["ground_truth"] else verified_false).add(victim_unfav_key)
    resolved = verified_true | verified_false

    verification_notes = []
    if disclosed_ever:
        findings = "／".join(f"「{f['label']}」は{'発生した' if f['ground_truth'] else '発生していない'}"
                            for f in facts)
        verification_notes.append(f"当事者Aが提出したドライブレコーダーをプラットフォームが照合済み。{findings}。")
    if opp_disclosed_ever:
        findings = "／".join(f"「{f['label']}」は{'発生した' if f['ground_truth'] else '発生していない'}"
                            for f in facts)
        verification_notes.append(f"相手方Bが提出したドライブレコーダーをプラットフォームが照合済み。{findings}。")
    if third_party_revealed_ever and not disclosed_ever and not opp_disclosed_ever:
        sp = fmap.get(victim_unfav_key)
        if sp:
            # 中立の調停AIが独自に証拠を握るのは当事者主義から見て不自然（2026-09 専門家FB）。
            # 検出確率・隠し抽選・開示ラウンドのロジックは一切変えず、提示のチャネルだけを
            # 「相手方代理人の開示請求 → 提出 → 調停AIが照合」に改める。
            verification_notes.append(
                f"相手方B代理人が第三者記録（取締り・路側センサー・信号記録等）の開示を請求し、"
                f"提出された記録を照合した結果、「{sp['label']}」が確認された。")

    # 露見: 証拠で確定した争点に嘘があれば記録（欺瞞は明示stance由来で決定論・重複は避ける）
    victim_denied_unfav = stances.get(victim_unfav_key) == "deny" or any(
        (d.get("stances") or {}).get(victim_unfav_key) == "deny" for d in prior_decisions)
    seen = {(e.get("fact"), e.get("speaker")) for e in _table.query(
        KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}") & Key("sk").begins_with("EXPOSURE#")
    ).get("Items", [])}
    new_exposures = []
    for key in verified_true:
        f = fmap[key]
        denied_before = any((d.get("stances") or {}).get(key) == "deny" for d in prior_decisions)
        st = "deny" if denied_before else stances.get(key, "none")
        if f["favors"] != "victim":
            # 被害者の不利事実(速度超過/黄進入…)を否認していた → 被害者の嘘が露見
            if st == "deny" and (key, "victim") not in seen:
                new_exposures.append({"key": key, "speaker": "victim",
                                      "type": JM.classify_deception(f, "victim", st)})
        else:
            # 被害者に有利な相手の違反が証拠(=被害者のドラレコ開示)で確定 → 相手の否認が覆る
            if (disclosed_ever or opp_disclosed_ever) and (key, "opponent") not in seen:
                new_exposures.append({"key": key, "speaker": "opponent", "type": "denial_of_true"})
    if new_exposures:
        _record_exposures(sid, new_exposures)

    # 証拠で裏づけられた争点のみ適用した「エビデンスに基づく妥当割合」を算定（調停の提案アンカー）
    proposed = JM.fault_ratio(scenario_id, sorted(verified_true), fault_cell)

    # --- 2) 被害者エージェントの公開発言（本人の自由記述の範囲のみ代弁） --------
    victim_stmt = AG.victim_agent_statement(sc, claim_text, disclose_this_round, history)
    _put_msg(sid, "victim_agent", victim_stmt)
    history.append({"role": "victim_agent", "text": victim_stmt})

    # --- 3) 相手方エージェントの応答（証拠状況に反応） ------------------------
    unfav_fact = fmap.get(victim_unfav_key)
    evidence_ctx = {
        "victim_denied_unfav": victim_denied_unfav,
        "unfav_verified": victim_unfav_key in verified_true,
        "unfav_label": unfav_fact["label"] if unfav_fact else "",
        "victim_disclosed_dashcam": disclosed_ever,
        "opponent_disclosed_dashcam": opp_disclosed_ever,
        "opponent_disclosed_now": opp_disclose_now,
        "verified_labels": [fmap[k]["label"] for k in sorted(verified_true)],
        "refuted_labels": [fmap[k]["label"] for k in sorted(verified_false)],
        "resolved_findings": [f"{fmap[k]['label']}：{'発生' if fmap[k]['ground_truth'] else '不発生'}"
                              for k in sorted(resolved)],
        "all_facts_resolved": len(resolved) == len(facts),
        "victim_fault_refuted": victim_fault_key in verified_false,
        "victim_fault_label": fmap[victim_fault_key]["label"] if victim_fault_key else "",
        # 譲歩スケジュール用（deadline効果・仲裁の影）: 進行度と調停アンカー
        "round_no": round_no, "max_rounds": MAX_ROUNDS,
        "proposed_ratio": proposed,
        # Round 0（事故形態と基本割合の確認）はフロントのゲートで必ず交渉前に通る。
        "framing_confirmed": True,
    }
    opp_stmt = AG.opponent_agent_reply(sc, facts, sc["negotiation_range"], history, evidence_ctx)
    _put_msg(sid, "opponent", opp_stmt)
    history.append({"role": "opponent", "text": opp_stmt})

    # 終了ゲート: 当事者A代理人と相手方代理人が同じ割合を明示受諾した場合のみロックする。
    # 調停AIの勧告は受諾に数えない。判定失敗時は detect_platform_agreement が未合意に倒す。
    agreement = AG.detect_platform_agreement(history)
    _table.put_item(Item=_to_ddb({
        "pk": f"SESSION#{sid}", "sk": f"AGREEMENT_CHECK#{round_no:03d}",
        "type": "agreement_check", "round": round_no,
        "agreed": agreement["agreed"], "party_a_ratio": agreement["ratio"],
        "reason": agreement["reason"], "ts": _now(),
    }))
    if agreement["agreed"]:
        _table.put_item(Item=_to_ddb({
            "pk": f"SESSION#{sid}", "sk": f"AGREEMENT#{round_no:03d}",
            "type": "mutual_agreement", "round": round_no,
            "party_a_ratio": agreement["ratio"], "reason": agreement["reason"], "ts": _now(),
        }))

    # --- 4) 調停AIの整理＋証拠照合＋エビデンス割合の提示 ----------------------
    # 確定事項（証拠で裏づいた争点）＝以後の係争を打ち切るロック対象。調停AIの正式勧告の根拠。
    mediator_stmt = AG.mediator_statement(sc, facts, history, verification_notes, proposed,
                                          round_info=(round_no, MAX_ROUNDS),
                                          locked_labels=evidence_ctx["verified_labels"],
                                          refuted_labels=evidence_ctx["refuted_labels"],
                                          mutual_agreement=agreement if agreement["agreed"] else None,
                                          framing_confirmed=True)
    _put_msg(sid, "mediator", mediator_stmt)

    # --- 5) 自分の代理人AIによる私的な主張支援（相手の出方への対応助言） --------
    # 双方合意で示談は終了（UIに交渉を続ける導線は無い）。助言AIは「どうされますか？」と続行を尋ねてしまうので呼ばない。
    if agreement["agreed"]:
        advice = "双方の合意が成立しました。大変お疲れさまでした。下のボタンから次へお進みください。"
    else:
        advice = AG.victim_agent_advice(sc, opp_stmt, mediator_stmt, disclosed_ever)
    _put_msg(sid, "advice", advice)

    return _resp(200, {
        "victim_statement": victim_stmt,
        "opponent_statement": opp_stmt,
        "mediator_statement": mediator_stmt,
        "advice": advice,                    # あなたの代理人からの私的助言（主張支援）
        "proposed_ratio": proposed,          # [被害者, 相手方] エビデンスに基づく調停案
        "agreement_ready": agreement["agreed"],  # 双方が同じ割合を明示受諾したときのみ true
        "agreement_ratio": agreement["ratio"],
        "disclosed": disclosed_ever,         # 提出は不可逆（フロントでトグルをロック）
        "opponent_disclosed": opp_disclosed_ever,
        "third_party_confirmed": third_party_revealed_ever,
        "round": round_no,
        "max_rounds": MAX_ROUNDS,            # 上限（フロントの残ラウンド表示・パネル制御用）
    })


def _put_msg(sid, role, text):
    ts = _now()
    _table.put_item(Item={"pk": f"SESSION#{sid}", "sk": f"NEGO#{ts:013d}#{role[0]}",
                          "type": "message", "role": role, "text": text, "ts": ts})


def _load_platform_agreements(sid):
    """バックエンドが確定した双方合意のみ読み込む。"""
    r = _table.query(
        KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}") & Key("sk").begins_with("AGREEMENT#"))
    return sorted(r.get("Items", []), key=lambda x: x["sk"])


def _record_exposures(sid, exposures):
    for e in exposures:
        _table.put_item(Item=_to_ddb({
            "pk": f"SESSION#{sid}", "sk": f"EXPOSURE#{_now()}#{uuid.uuid4().hex[:6]}",
            "type": "exposure", "fact": e["key"], "speaker": e["speaker"],
            "deception_type": e["type"], "ts": _now()}))


def _last_insurer_exposure_note(sid):
    """AI保険が直近で露見させられた自社不利点があれば、認めさせるためのノート。"""
    r = _table.query(
        KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}") & Key("sk").begins_with("EXPOSURE#"))
    notes = [it for it in r.get("Items", []) if it.get("speaker") == "opponent"]
    if notes:
        return "自社に不利な事実について証拠があると検証者に指摘されています。"
    return ""


def support(body):
    sid = body["sessionId"]
    query = body.get("message", "この事故の争点と過失割合の基準を教えてください")
    meta = _get_meta(sid)
    if not meta:
        return _resp(404, {"error": "session not found"})
    if meta["condition"].get("support") != "on":
        return _resp(403, {"error": "support disabled for this condition"})
    sc = SC.SCENARIOS[meta["condition"]["scenario"]]

    items = _load_history(sid, "SUPPORT")
    history = [{"role": it["role"], "text": it["text"]} for it in items]
    ts = _now()
    _table.put_item(Item={"pk": f"SESSION#{sid}", "sk": f"SUPPORT#{ts:013d}#u",
                          "type": "support_msg", "role": "user", "text": query, "ts": ts})
    reply = AG.support_reply(sc, query, history)
    ts2 = _now()
    _table.put_item(Item={"pk": f"SESSION#{sid}", "sk": f"SUPPORT#{ts2:013d}#s",
                          "type": "support_msg", "role": "support", "text": reply, "ts": ts2})
    return _resp(200, {"reply": reply})


def finalize(body):
    sid = body["sessionId"]
    meta = _get_meta(sid)
    if not meta:
        return _resp(404, {"error": "session not found"})
    cond = meta["condition"]
    scenario_id = cond["scenario"]
    fault_cell = cond.get("fault_cell", "mixed")   # 過失構造セル（旧セッションは mixed）
    sc = SC.SCENARIOS[scenario_id]
    items = _load_history(sid, "NEGO")
    history = [{"role": it["role"], "text": it["text"]} for it in items]
    # ラウンド数: 研究3は被害者代理人発言(victim_agent)を1ラウンドとする（生の主張userは二重計上しない）。
    # 研究2は被害者本人発言(user)。
    if meta["study"] == "study3":
        rounds = len([h for h in history if h["role"] == "victim_agent"])
    else:
        rounds = len([h for h in history if h["role"] == "user"])

    # 研究3は「参加者が終了ボタンを押した」ことを合意としない。
    # 交渉ラウンド中にサーバが記録した双方受諾が必要。非合意終了（裁定）は上限到達後だけ。
    if meta["study"] == "study3":
        user_agreed = body.get("userAgreed") is True
        agreements = _load_platform_agreements(sid)
        if user_agreed:
            if not agreements:
                return _resp(409, {"error": "mutual_agreement_required",
                                   "message": "相手方と同じ過失割合に合意できるまで、示談は終了できません。"})
            locked_ratio = int(agreements[-1]["party_a_ratio"])
            submitted_ratio = body.get("agreedRatio")
            try:
                submitted_ratio = int(submitted_ratio)
            except (TypeError, ValueError):
                submitted_ratio = None
            if submitted_ratio is not None and submitted_ratio != locked_ratio:
                return _resp(409, {"error": "agreement_ratio_mismatch",
                                   "message": "双方が合意した割合と一致しません。",
                                   "agreement_ratio": locked_ratio})
            result = {"agreed": True, "ratio": locked_ratio,
                      "reason": agreements[-1].get("reason", "双方の明示受諾")}
        else:
            if agreements:
                return _resp(409, {"error": "agreement_already_reached",
                                   "message": "双方の合意は成立済みです。合意した割合で示談を確定してください。",
                                   "agreement_ratio": int(agreements[-1]["party_a_ratio"])})
            if rounds < MAX_ROUNDS:
                return _resp(409, {"error": "negotiation_in_progress",
                                   "message": f"合意に至っていないため、ラウンド{MAX_ROUNDS}まで交渉を続けてください。"})
            result = {"agreed": False, "ratio": None, "reason": "上限ラウンドまで双方合意に至らず裁定へ移行"}
    else:
        result = AG.detect_agreement(sc, history)

    # 参加者が明示した最終合意割合を優先（利得・乖離の信頼性のため）。
    # 無ければLLM検出値にフォールバック。
    reported_ratio = body.get("agreedRatio")
    try:
        reported_ratio = int(reported_ratio) if reported_ratio is not None else None
    except (TypeError, ValueError):
        reported_ratio = None
    if meta["study"] == "study3":
        # 研究3はクライアント申告値ではなく、サーバでロックした双方合意のみを採用。
        agreed_ratio = result["ratio"]
        agreed_flag = result["agreed"]
    else:
        agreed_ratio = reported_ratio if reported_ratio is not None else result["ratio"]
        agreed_flag = bool(body.get("userAgreed")) if body.get("userAgreed") is not None else result["agreed"]

    outcome = {
        "pk": f"SESSION#{sid}", "sk": "OUTCOME",
        "type": "outcome", "sessionId": sid,
        "agreed": agreed_flag, "agreed_ratio": agreed_ratio,
        "llm_detected_ratio": result["ratio"], "reason": result["reason"], "rounds": rounds,
        "user_reported_agree": body.get("userAgreed"),
        "finalizedAt": _now(),
    }
    resp = {"agreed": agreed_flag, "ratio": agreed_ratio, "rounds": rounds}

    # 研究3（203x示談プラットフォーム）: 結果正確性・欺瞞・露見・開示・利得を算出
    if meta["study"] == "study3":
        true_r = JM.true_ratio(scenario_id, fault_cell)   # 真の割合（過失構造セルで着脱後）
        # 欺瞞: 記録済みの明示スタンス（DECISION#）から決定論で集計（LLM解析に非依存）
        dr = _table.query(KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                          & Key("sk").begins_with("DECISION#"))
        decisions = dr.get("Items", [])
        coding = AG.tally_deception(scenario_id, decisions, fault_cell)
        disclosed_dashcam = any(d.get("disclose_dashcam") for d in decisions)   # 開示行動DV

        # --- 裁定（BATNA）: 合意に至らなければ、その時点で確認できる客観証拠のみで決着 ----
        # 相手方ドラレコ（保有・提出はセッション作成時に隠し抽選済み）は、
        # 交渉中に提出済みならその証拠状態を裁定にも引き継ぐ。
        # 両刃：提出されると被害者の不利事実と相手自身の違反の双方が確定する。
        # 被害者ドラレコの駆け込み提出は認めない（開示判断はDV＝交渉中の決定が最終）。
        resolution = "agreement" if agreed_flag else "adjudication"
        adjudication = None
        if not agreed_flag:
            verifiability = cond.get("verifiability") or {}
            facts = JM.game_facts(scenario_id, verifiability, fault_cell)
            fmap = {f["key"]: f for f in facts}
            unfav_key = JM.victim_unfavorable_key(scenario_id, fault_cell)
            verified = set()
            if disclosed_dashcam:
                verified |= {f["key"] for f in facts if f["ground_truth"]}
            if verifiability.get(unfav_key) == "hard":
                verified.add(unfav_key)
            evidence_rows = _table.query(KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                                         & Key("sk").begins_with("EVIDENCE#")).get("Items", [])
            opp_submitted_during_negotiation = any(
                e.get("source") == "opponent_dashcam" for e in evidence_rows)
            opp_submitted = opp_submitted_during_negotiation or bool(cond.get("opp_submits_at_adjudication"))
            if opp_submitted:
                verified |= {f["key"] for f in facts if f["ground_truth"]}
            adj_ratio = JM.fault_ratio(scenario_id, sorted(verified), fault_cell)
            # 裁定露見: 交渉中に否認した被害者の不利事実が、相手方の提出等で裁定時に新たに確定
            #（＝嘘の賭けが裁定で精算される）。既存 EXPOSURE# との重複は記録しない。
            denied_ever = any((d.get("stances") or {}).get(unfav_key) == "deny" for d in decisions)
            seen = {(e.get("fact"), e.get("speaker")) for e in _table.query(
                KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                & Key("sk").begins_with("EXPOSURE#")).get("Items", [])}
            if unfav_key and unfav_key in verified and denied_ever \
                    and (unfav_key, "victim") not in seen:
                _record_exposures(sid, [{"key": unfav_key, "speaker": "victim",
                                         "type": JM.classify_deception(fmap[unfav_key], "victim", "deny")}])
            agreed_v = adj_ratio[0]   # 裁定割合を結果として採用 → 正確性DVが決裂でも定義される
            adjudication = {"ratio": adj_ratio, "verified_keys": sorted(verified),
                            "opp_dashcam_exists": bool(cond.get("opp_dashcam_exists")),
                            "opp_submitted": opp_submitted,
                            "opp_submitted_during_negotiation": opp_submitted_during_negotiation}
        else:
            agreed_v = agreed_ratio                   # 合意された被害者割合（参加者申告優先）
        deviation = (agreed_v - true_r[0]) if agreed_v is not None else None

        # 露見（客観証拠で嘘が捕捉された件数）を集計（裁定で新たに記録された露見を含む）
        r = _table.query(KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                         & Key("sk").begins_with("EXPOSURE#"))
        exps = r.get("Items", [])
        victim_exposed = len([e for e in exps if e.get("speaker") == "victim"])
        insurer_exposed = len([e for e in exps if e.get("speaker") == "opponent"])
        # 利得: (1)割合ボーナス=自分の割合が低いほど得 (2)スピードボーナス=早く決着ほど得
        #       (3)露見ペナルティ。過失割合以外に「示談の早さ」も価値次元として誘因に入れる。
        #       スピードボーナスは合意時のみ（裁定移行＝解決の長期化のため0円）。
        #   ★成果報酬の設計：総額は「ベース100円＋成果ボーナス」で最大500円（全3シナリオ合計）。
        #     ここでは1シナリオあたりの成果ボーナス(net)のみを算出し、ベース加算と500円上限の
        #     クランプは done 画面（フロント）で全シナリオを合算して行う。1シナリオの上限は
        #     割合80＋早期50＝130点、3シナリオで最大390点 → +ベース100＝最大490円（≦500）。
        payoff = None
        if agreed_v is not None:
            ratio_bonus = round((100 - agreed_v) * 0.8)         # 円: 被害者割合が低いほど得（最大80）
            speed_bonus = max(0, 50 - (max(1, rounds) - 1) * 10) if agreed_flag else 0  # 最大50
            penalty = victim_exposed * 50                       # 露見1件 −50円
            net = ratio_bonus + speed_bonus - penalty
            payoff = {"ratio_bonus": ratio_bonus, "speed_bonus": speed_bonus,
                      "penalty": penalty, "net": net}
        evidence_rows = _table.query(KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                                     & Key("sk").begins_with("EVIDENCE#")).get("Items", [])
        evidence_sources = sorted({e.get("source") for e in evidence_rows if e.get("source")})
        game = {
            "true_ratio": true_r, "agreed_victim_ratio": agreed_v,
            "deviation_from_truth": deviation,       # 負=真より被害者有利（嘘が通った疑い）
            "resolution": resolution,                # agreement | adjudication（決着の経路DV）
            "adjudication": adjudication,            # 裁定の内訳（合意時はNone）
            "opp_dashcam_exists": bool(cond.get("opp_dashcam_exists")),
            "evidence_sources": evidence_sources,
            "opp_disclosed_during_negotiation": "opponent_dashcam" in evidence_sources,
            "third_party_confirmed": "third_party" in evidence_sources,
            "deception": coding, "victim_exposed": victim_exposed,
            "insurer_exposed": insurer_exposed,
            "disclosed_dashcam": disclosed_dashcam,  # ドラレコ開示（行動DV）
            "exposed_by_evidence": victim_exposed > 0,
            "verifiability": cond.get("verifiability"), "payoff": payoff,
            # 過失構造セル・試行情報（被験者内3水準の分析用）。
            # DynamoDB は数値を Decimal で返すため int 化（_to_ddb の json.dumps が Decimal を拒否する）。
            "fault_cell": fault_cell, "trial_index": int(cond.get("trial_index", 0)),
            "participant_id": cond.get("participant_id"),
        }
        outcome["game"] = game
        resp["game"] = game
        resp["ratio"] = agreed_v   # 裁定時は裁定割合を表示（フロントの結果画面用）

    _table.put_item(Item=_to_ddb(outcome))
    _table.update_item(Key={"pk": f"SESSION#{sid}", "sk": "META"},
                       UpdateExpression="SET #s = :v",
                       ExpressionAttributeNames={"#s": "status"},
                       ExpressionAttributeValues={":v": "finalized"})
    return _resp(200, resp)


def log_event(body):
    sid = body["sessionId"]
    _table.put_item(Item=_to_ddb({
        "pk": f"SESSION#{sid}", "sk": f"EVENT#{_now()}#{uuid.uuid4().hex[:6]}",
        "type": "event", "sessionId": sid,
        "event": body.get("event"), "data": body.get("data"), "ts": _now(),
    }))
    return _resp(200, {"ok": True})


def _participant_outcomes(participant_id):
    """参加者の3試行分OUTCOMEを取得する（支払額のサーバ側確定用）。"""
    items = []
    kwargs = {
        "FilterExpression": Attr("type").eq("outcome")
                            & Attr("game.participant_id").eq(participant_id)
    }
    while True:
        result = _table.scan(**kwargs)
        items.extend(result.get("Items", []))
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return items


def _registrant_id(body):
    """登録者IDを英数字と -_. のみ・64文字までに正規化する（未指定なら None）。"""
    rid = re.sub(r"[^0-9A-Za-z._-]", "", str(body.get("registrantId") or ""))[:64]
    return rid or None


def _source(body):
    """募集経路。既知の値だけを通す（未指定・未知は None＝従来のDM経由）。"""
    src = str(body.get("source") or "").strip().lower()
    return src if src in {"cw"} else None


def save_payment(body):
    """完了者の振込先を専用テーブルへ保存する。研究回答・LLM処理とは分離する。"""
    sid = str(body.get("sessionId") or "").strip()
    participant_id = str(body.get("participantId") or "").strip()
    completion_code = str(body.get("completionCode") or "").strip()
    if not sid or not participant_id or not completion_code:
        return _resp(400, {"error": "完了情報を確認できません"})

    meta = _get_meta(sid)
    if not meta or meta.get("study") != "study3" or meta.get("status") != "finalized":
        return _resp(400, {"error": "実験の完了を確認できません"})
    if (meta.get("condition") or {}).get("participant_id") != participant_id:
        return _resp(400, {"error": "参加者情報が一致しません"})
    # クラウドワークス経由はプラットフォーム上で固定報酬を払う（口座の直接取得は規約違反）。
    if meta.get("source") == "cw":
        return _resp(400, {"error": "この参加経路では振込先の登録は不要です"})

    surveys = _table.query(
        KeyConditionExpression=Key("pk").eq(f"SESSION#{sid}")
                               & Key("sk").begins_with("SURVEY#t2#")
    ).get("Items", [])
    expected_hash = hashlib.sha256(completion_code.encode("utf-8")).hexdigest()
    if not any(s.get("completion_code_hash") == expected_hash for s in surveys):
        return _resp(400, {"error": "最終アンケートと完了コードを確認できません"})

    fields = {
        "bank_name": str(body.get("bankName") or "").strip(),
        "branch_name": str(body.get("branchName") or "").strip(),
        "account_type": str(body.get("accountType") or "").strip(),
        "account_number": str(body.get("accountNumber") or "").strip(),
        "account_holder": str(body.get("accountHolder") or "").strip(),
    }
    if not all(fields.values()):
        return _resp(400, {"error": "振込先をすべて入力してください"})
    if fields["account_type"] not in {"普通", "当座"}:
        return _resp(400, {"error": "口座種別が不正です"})
    if not fields["account_number"].isdigit() or not 5 <= len(fields["account_number"]) <= 8:
        return _resp(400, {"error": "口座番号は5〜8桁の数字で入力してください"})
    if any(len(fields[k]) > 80 for k in ("bank_name", "branch_name", "account_holder")):
        return _resp(400, {"error": "振込先の入力が長すぎます"})

    outcomes = _participant_outcomes(participant_id)
    trial_indexes = {int((o.get("game") or {}).get("trial_index", -1)) for o in outcomes}
    if trial_indexes != {0, 1, 2}:
        return _resp(400, {"error": "3つのシナリオの完了を確認できません"})
    total_net = sum(int(((o.get("game") or {}).get("payoff") or {}).get("net", 0)) for o in outcomes)
    amount = min(500, max(100, 100 + total_net))
    now = _now()
    participant_hash = hashlib.sha256(participant_id.encode("utf-8")).hexdigest()
    _payment_table.put_item(Item=_to_ddb({
        "pk": f"PAYMENT#{participant_hash}", "sk": "DETAIL",
        "type": "payment", "participant_id": participant_id,
        "completion_code": completion_code, "session_id": sid,
        "registrant_id": _registrant_id(body) or meta.get("registrant_id"),
        "amount": amount, "status": "pending",
        **fields, "createdAt": now, "updatedAt": now,
        # 支払後の手動削除に加え、取り残し防止として90日後に自動削除する。
        "expiresAt": int(time.time()) + 90 * 24 * 60 * 60,
    }))
    return _resp(200, {"ok": True, "amount": amount})


def admin_payments(qs):
    if not ADMIN_KEY or qs.get("key") != ADMIN_KEY:
        return _resp(403, {"error": "forbidden"})
    items = []
    kwargs = {}
    while True:
        result = _payment_table.scan(**kwargs)
        items.extend(result.get("Items", []))
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return _resp(200, {"count": len(items), "items": items})


def complete_payment(body):
    """振込完了後、口座情報を含む支払レコードを直ちに削除する。"""
    if not ADMIN_KEY or body.get("key") != ADMIN_KEY:
        return _resp(403, {"error": "forbidden"})
    participant_id = str(body.get("participantId") or "").strip()
    if not participant_id:
        return _resp(400, {"error": "participantId is required"})
    participant_hash = hashlib.sha256(participant_id.encode("utf-8")).hexdigest()
    _payment_table.delete_item(Key={"pk": f"PAYMENT#{participant_hash}", "sk": "DETAIL"})
    return _resp(200, {"ok": True})


def admin_export(qs):
    if not ADMIN_KEY or qs.get("key") != ADMIN_KEY:
        return _resp(403, {"error": "forbidden"})
    items = []
    kwargs = {}
    while True:
        r = _table.scan(**kwargs)
        items.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        kwargs["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return _resp(200, {"count": len(items), "items": items})


# --- router --------------------------------------------------------------
def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "")
    qs = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS, "body": ""}

    try:
        body = json.loads(event["body"]) if event.get("body") else {}
    except (ValueError, KeyError):
        body = {}

    try:
        if path.endswith("/api/session") and method == "POST":
            return create_session(body)
        if path.endswith("/api/form") and method == "GET":
            return get_form(qs)
        if path.endswith("/api/survey") and method == "POST":
            return save_survey(body)
        if path.endswith("/api/negotiate") and method == "POST":
            return negotiate(body)
        if path.endswith("/api/support") and method == "POST":
            return support(body)
        if path.endswith("/api/finalize") and method == "POST":
            return finalize(body)
        if path.endswith("/api/event") and method == "POST":
            return log_event(body)
        if path.endswith("/api/payment") and method == "POST":
            return save_payment(body)
        if path.endswith("/api/admin/payments") and method == "GET":
            return admin_payments(qs)
        if path.endswith("/api/admin/payment-complete") and method == "POST":
            return complete_payment(body)
        if path.endswith("/api/admin/export") and method == "GET":
            return admin_export(qs)
        return _resp(404, {"error": "not found", "path": path})
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return _resp(500, {"error": str(e)})
