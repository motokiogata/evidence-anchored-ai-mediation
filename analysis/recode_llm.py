# -*- coding: utf-8 -*-
"""欺瞞コーディングの信頼性：実験で使ったものとは別のLLMで、参加者の主張文を全件コーディングし直す。

事前登録 §5.4.1 は「20%を人手で二重コーディング」と定めていたが、研究者の判断（2026-11-24）で
「独立した2つのLLM（Claude Opus 5.5 と GPT）で全件」に変更した（`docs/プレレジからの具体化と逸脱_study3.md` B-1）。
実験中の抽出器は Claude Sonnet 4.6（`agent.analyze_claims`）。コード表（system プロンプト）は同じものを使う。

  # コーディング（結果は analysis/data/recode_<名前>.json にキャッシュ。途中で止めても再開できる）
  AWS_PROFILE=<your-profile> python analysis/recode_llm.py code DUMP opus55 bedrock:jp.anthropic.claude-opus-5-5
  OPENAI_API_KEY=... python analysis/recode_llm.py code DUMP gpt openai:<モデルID>
  # API を使わずチャット画面のモデルに渡す場合：配布用 zip を作り、返ってきた CSV を取り込む
  python analysis/recode_llm.py export DUMP            # → analysis/data/recode_package.zip（対応表は recode_package_map.json）
  python analysis/recode_llm.py import DUMP gpt 返ってきた.csv [...]
  # 一致度（実験時の抽出 vs 各コーダー、コーダー同士）
  python analysis/recode_llm.py kappa DUMP opus55 [gpt]
  # 感度分析：H1 を各コーダーの欺瞞DVで推定し直す（確認的結果は変えない。κ<.70 のときの事前規定に対応）
  python analysis/recode_llm.py sens DUMP opus55 [gpt]

単位：①争点ごとのスタンス（assert/deny/concede/none）、②そこから決定論で導いた試行ごとの欺瞞あり/なし（H1 のDV）。
"""
import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
import judicial_map as JM  # noqa: E402
from confirmatory import build, exclusions  # noqa: E402

STANCES = ["assert", "deny", "concede", "none"]
# --opponent を付けると、参加者ではなく相手方エージェント（AI）の発言を対象にする（登録本文の「相手方発言も二重コーディング」）。
# 実験中、相手方の発言は LLM で分類していない（露見は証拠の確定から機械的に記録）ので、比較はコーダー同士だけ。
WHO = "opponent" if "--opponent" in sys.argv else "victim"
SUFFIX = "_opp" if WHO == "opponent" else ""
if WHO == "opponent":
    sys.argv.remove("--opponent")


def units(dump_path):
    """分析対象（完了・注意チェック通過の cw 参加者）の全試行について、空でない主張文を返す。"""
    with open(dump_path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    T, _, _ = exclusions(*build(items), mc_exclude=False, aware_exclude=False)
    trial = {r.sid: (r.scenario, r.cell) for r in T.itertuples()}
    out = []
    for it in items:
        sid = it.get("pk", "")[8:]
        if WHO == "opponent":
            if it.get("sk", "").startswith("NEGO#") and it.get("role") == "opponent" and sid in trial:
                sc, cell = trial[sid]
                out.append({"id": f"{sid}#{it['sk']}", "sid": sid, "scenario": sc, "cell": cell,
                            "text": (it.get("text") or "").strip(), "stances": {}})
        elif it.get("sk", "").startswith("DECISION#") and sid in trial:
            sc, cell = trial[sid]
            out.append({"id": f"{sid}#{it['sk']}", "sid": sid, "scenario": sc, "cell": cell,
                        "text": (it.get("claim_text") or "").strip(), "stances": it.get("stances") or {}})
    return out


def system_prompt(scenario, cell):
    """agent.analyze_claims と同じコード表（地上真実は渡さない）。"""
    facts = JM.game_facts(scenario, fault_cell=cell)
    catalog = "\n".join(f"{i+1}. key={f['key']} : {f['label']}（{f['note']}）" for i, f in enumerate(facts))
    return (
        "あなたは交渉発言の分析器です。以下の争点リストについて、発言者がその争点を"
        "『主張(assert=その事実があった/自分に有利に述べる)』『否認(deny=無かった/相手の主張を否定)』"
        "『自認(concede=自分に不利な事実を認める)』『言及なし(none)』のどれで扱ったか判定してください。\n"
        f"発言者: {'被害者A' if WHO == 'victim' else '相手方(保険)B'}\n争点リスト:\n{catalog}\n\n"
        "必ず次のJSON配列のみ出力: "
        '[{"key":"<争点key>","stance":"assert|deny|concede|none"}, ...] 全争点を1つずつ含めること。'
    ), [f["key"] for f in facts]


def make_coder(spec):
    kind, model = spec.split(":", 1)
    if kind == "bedrock":
        import boto3
        brt = boto3.client("bedrock-runtime", region_name="ap-northeast-1")

        def call(system, text):
            r = brt.converse(modelId=model, system=[{"text": system}],
                             messages=[{"role": "user", "content": [{"text": text}]}],
                             inferenceConfig={"maxTokens": 400})
            return next(c["text"] for c in r["output"]["message"]["content"] if "text" in c)
    elif kind == "openai":
        from openai import OpenAI
        cli = OpenAI()

        def call(system, text):
            r = cli.chat.completions.create(model=model, messages=[
                {"role": "system", "content": system}, {"role": "user", "content": text}])
            return r.choices[0].message.content
    else:
        sys.exit(f"unknown coder {spec}")
    return call


def code(dump_path, name, spec):
    path = os.path.join(HERE, "data", f"recode_{name}.json")
    done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    call = make_coder(spec)
    todo = [u for u in units(dump_path) if u["text"] and u["id"] not in done]
    print(f"{name}: 残り {len(todo)} 件（済み {len(done)}）")

    def one(u):
        system, keys = system_prompt(u["scenario"], u["cell"])
        for _ in range(3):
            try:
                raw = call(system, u["text"])
                data = json.loads(raw[raw.find("["):raw.rfind("]") + 1])
                got = {d["key"]: d["stance"] for d in data}
                if all(got.get(k) in STANCES for k in keys):
                    return u["id"], {k: got[k] for k in keys}
            except Exception as e:  # noqa: BLE001
                err = e
        return u["id"], None            # 失敗は none に倒さず欠測として残す（一致度を汚さない）

    with ThreadPoolExecutor(6) as ex:
        for n, (uid, st) in enumerate(ex.map(one, todo), 1):
            if st:
                done[uid] = st
            if n % 50 == 0 or n == len(todo):
                json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False)
                print(f"  {n}/{len(todo)}", flush=True)
    print(f"保存: {path}（{len(done)} 件）")


def export(dump_path):
    """チャット画面のモデルに渡す配布物。実験時・他コーダーの判定も地上真実も入れない。IDは連番に置き換える。"""
    import csv
    import io
    import random
    import zipfile
    us = [u for u in units(dump_path) if u["text"]]
    random.Random(20261124).shuffle(us)
    books, rows, idmap = {}, [], {}
    for n, u in enumerate(us, 1):
        cb = f"{u['scenario'][0]}_{u['cell']}"
        if cb not in books:
            system, keys = system_prompt(u["scenario"], u["cell"])
            books[cb] = {"system_prompt": system, "keys": keys}
        idmap[f"T{n:04d}"] = u["id"]
        rows.append([f"T{n:04d}", cb, u["text"]])
    readme = (
        "# 交渉発言のコーディング依頼\n\n"
        "交通事故の過失割合をめぐる交渉実験で、" + ("参加者（被害者A）が書いた" if WHO == "victim" else "相手方B側の代理人AIが述べた") + "発言が " + str(len(rows)) + " 件あります。\n"
        "各発言について、争点ごとの態度（stance）を判定してください。\n\n"
        "## ファイル\n"
        "- `items.csv`：`item_id, codebook_id, text`（1行＝1発言。text に改行を含むものがあります）\n"
        "- `codebooks.json`：`codebook_id` ごとの判定基準（`system_prompt`）と、判定する争点キー（`keys`、各2個）\n\n"
        "## やること\n"
        "1. 各行について、その `codebook_id` の `system_prompt` を判定基準として `text` を読む。\n"
        "2. `keys` の争点それぞれに `assert` / `deny` / `concede` / `none` のどれか1つを付ける。\n"
        "3. 結果を CSV で返す。列は `item_id,key,stance`。全 item_id × 全キー（" + str(len(rows) * 2) + " 行）を漏れなく。\n\n"
        "## 守ってほしいこと\n"
        "- **1件ずつ文を読んで判定する**こと。キーワード検索・正規表現・スクリプトによる機械的な分類はしない。\n"
        "- 判定基準は `system_prompt` の文言だけ。事故の実際の事実がどうだったかは推測しない（与えていません）。\n"
        "- 発言はその1件だけで判定する（前後の文脈はありません）。\n"
        "- 件数が多いので、100件程度ずつに分けて処理してかまいません。最後に全件を1つの CSV にまとめてください。\n"
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["item_id", "codebook_id", "text"])
    w.writerows(rows)
    out = os.path.join(HERE, "data", f"recode_package{SUFFIX}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.md", readme)
        z.writestr("items.csv", "\ufeff" + buf.getvalue())
        z.writestr("codebooks.json", json.dumps(books, ensure_ascii=False, indent=1))
    json.dump(idmap, open(os.path.join(HERE, "data", f"recode_package_map{SUFFIX}.json"), "w", encoding="utf-8"))
    print(f"{out}（{len(rows)} 件、コード表 {len(books)} 種）")


def import_csv(dump_path, name, paths):
    """export の形式（item_id,key,stance）で返ってきた CSV を recode_<name>.json に取り込む。"""
    import csv
    idmap = json.load(open(os.path.join(HERE, "data", f"recode_package_map{SUFFIX}.json"), encoding="utf-8"))
    keys = {u["id"]: system_prompt(u["scenario"], u["cell"])[1] for u in units(dump_path) if u["text"]}
    got = defaultdict(dict)
    for p in paths:
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                got[r["item_id"].strip()][r["key"].strip()] = r["stance"].strip().lower()
    done, bad = {}, []
    for tid, uid in idmap.items():
        st = got.get(tid, {})
        if all(st.get(k) in STANCES for k in keys[uid]):
            done[uid] = {k: st[k] for k in keys[uid]}
        else:
            bad.append(tid)
    unknown = sorted(set(got) - set(idmap))
    path = os.path.join(HERE, "data", f"recode_{name}.json")
    json.dump(done, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"保存: {path}（{len(done)}/{len(idmap)} 件）  欠け・不正 {len(bad)} 件 {bad[:15]}  未知のID {len(unknown)} 件")


def kappa(a, b, cats):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else float("nan"), po


def trial_deception(us, stance_of):
    """試行ごとの欺瞞あり(1)/なし(0)。tally_deception と同じ決定論の規則。"""
    lied = defaultdict(int)
    for u in us:
        facts = {f["key"]: f for f in JM.game_facts(u["scenario"], fault_cell=u["cell"])}
        for k, st in (stance_of(u) or {}).items():
            if k in facts and JM.classify_deception(facts[k], WHO, st) in ("false_claim", "denial_of_true"):
                lied[u["sid"]] = 1
        lied.setdefault(u["sid"], 0)
    return lied


def report(dump_path, names):
    us = [u for u in units(dump_path) if u["text"]]
    coders = {} if WHO == "opponent" else {"実験時(Sonnet4.6)": lambda u: u["stances"]}
    for nm in names:
        rec = json.load(open(os.path.join(HERE, "data", f"recode_{nm}.json"), encoding="utf-8"))
        coders[nm] = (lambda r: lambda u: r.get(u["id"]))(rec)
        print(f"{nm}: {sum(1 for u in us if u['id'] in rec)}/{len(us)} 件をコーディング済み")
    keys = list(coders)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            fa, fb = coders[keys[i]], coders[keys[j]]
            both = [u for u in us if fa(u) and fb(u)]
            a = [fa(u).get(k, "none") for u in both for k in fb(u)]
            b = [fb(u)[k] for u in both for k in fb(u)]
            k1, p1 = kappa(a, b, STANCES)
            # 欺瞞DVは ②③ の試行のみ（H1 と同じ）。相手方は全セルで嘘をつきうるので全試行
            nz = [u for u in both if u["cell"] != "innocent" or WHO == "opponent"]
            da, db = trial_deception(nz, fa), trial_deception(nz, fb)
            sids = sorted(da)
            k2, p2 = kappa([da[s] for s in sids], [db[s] for s in sids], [0, 1])
            print(f"{keys[i]} vs {keys[j]}: スタンス κ={k1:.3f}（一致 {p1:.1%}, n={len(a)}）／"
                  f"試行の欺瞞あり・なし κ={k2:.3f}（一致 {p2:.1%}, n={len(sids)}）")


def sens(dump_path, names):
    """H1 と同じモデルを、各コーダーのスタンスから導いた欺瞞DVで推定し直す（除外は主分析と同じ）。"""
    from confirmatory import h1
    with open(dump_path, encoding="utf-8") as f:
        T, P, _ = exclusions(*build(json.load(f)["items"]))
    us = [u for u in units(dump_path) if u["text"] and u["sid"] in set(T.sid)]
    recs = {nm: json.load(open(os.path.join(HERE, "data", f"recode_{nm}.json"), encoding="utf-8")) for nm in names}
    coders = {"実験時(Sonnet4.6)": lambda u: u["stances"]}
    coders.update({nm: (lambda r: lambda u: r.get(u["id"]))(rec) for nm, rec in recs.items()})
    if len(names) == 2:   # 2コーダーが一致して欺瞞としたものだけ
        a, b = (recs[n] for n in names)
        coders["2コーダー一致"] = lambda u: {k: v for k, v in (a.get(u["id"]) or {}).items()
                                       if (b.get(u["id"]) or {}).get(k) == v}
    for nm, fn in coders.items():
        cnt = defaultdict(int)
        for u in us:
            facts = {f["key"]: f for f in JM.game_facts(u["scenario"], fault_cell=u["cell"])}
            cnt[u["sid"]] += sum(JM.classify_deception(facts[k], "victim", st) in ("false_claim", "denial_of_true")
                                 for k, st in (fn(u) or {}).items() if k in facts)
        T2 = T.assign(dec_count=T.sid.map(cnt).fillna(0).astype(int))
        T2["deceive"] = (T2.dec_count > 0).astype(int)
        nz = T2[T2.cell != "innocent"]
        r = h1(T2, P)
        print(f"{nm}: 欺瞞率 {nz.deceive.mean():.1%}（{nz.deceive.sum()}/{len(nz)}）  High {nz[nz.high==1].deceive.mean():.1%} / "
              f"Low {nz[nz.high==0].deceive.mean():.1%}  OR={r['OR']:.2f} [{r['CI'][0]:.2f}, {r['CI'][1]:.2f}], p={r['p']:.3f}"
              f"（収束 {r['converged']}）  参加者レベル OR={r['robust_participant'][0]:.2f}, p={r['robust_participant'][1]:.3f}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "export":
        export(sys.argv[2])
    elif len(sys.argv) < 4:
        sys.exit(__doc__)
    elif sys.argv[1] == "import":
        import_csv(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif sys.argv[1] == "code":
        code(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == "sens":
        sens(sys.argv[2], sys.argv[3:])
    else:
        report(sys.argv[2], sys.argv[3:])
