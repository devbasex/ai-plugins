"""mvv_candidates.py: プロジェクト MVV の候補の生成の文面・形の検査・人へ示す文面（`project-mvv.py propose`・#1366）。

候補は 2 案以上・各案が Mission / Vision / Value / レッドラインと根拠（材料の出典の ID）を持ち、分かれる点が 1 つ以上ある（I10）。
人へ示す `candidates.md` は経緯・前提・根拠・選択肢の 4 節を先頭に持つ（I19）。
"""

from __future__ import annotations

import project_mvv as pm

MAX_PROMPT = 150_000  # 候補の生成へ渡す材料の上限の文字数

FOUR = (("context", "経緯"), ("premise", "前提"), ("evidence", "根拠"), ("options", "選択肢"))

PROPOSE_SYSTEM = """あなたはプロジェクトの MVV（Mission / Vision / Value）の候補を書く。Tool は無い。
渡された NDF の共通原則と材料だけを根拠にする。共通原則を判断の基準として使い、原則に反する（人を害する・人の発展を妨げる）
案は書かない。共通原則の写し（上位の原則・優先順位・C の番号の操作）を候補に入れない。固有の必ず人の承認が要る操作は P の番号で書く。
候補は 2 案以上で、推奨の 1 案に recommended: true を付ける。各項目の evidence には材料の出典の ID（S<番号>）だけを並べる。
人へ示す文面なので、経緯（なぜ今この判断が要るか・ここまでに何を決めたか）・前提（この答えで何が決まり何が決まらないか）・
根拠・選択肢を先に書き、内部の用語は言い換えるか glossary に説明を書く。
出力は次の JSON の 1 つだけ。前後に文を書かない。
{"context": "経緯", "premise": "前提", "evidence": "根拠の要約", "options": "選択肢の要約",
 "glossary": [{"term": "語", "meaning": "説明"}],
 "candidates": [{"id": "A", "recommended": true, "summary": "1 行",
   "mission": {"text": "...", "evidence": ["S1"]}, "vision": {"text": "...", "evidence": ["S2"]},
   "values": [{"title": "短い名前", "text": "...", "evidence": ["S3"]}],
   "redlines": [{"id": "P1", "text": "操作", "reason": "理由", "evidence": ["S4"]}]}],
 "questions": [{"question": "利用者に決めてもらう点", "options": [{"label": "選択肢", "effect": "選んだときに決まること"}]}]}"""


def _part_problems(label: str, name: str, part, known: set[str]) -> list[str]:
    """項目 1 つ（Mission・Vision・Value・レッドライン）の本文と根拠の誤り。"""
    if not isinstance(part, dict) or not str(part.get("text") or "").strip():
        return [f"{label}: {name} の本文が無い"]
    ev = part.get("evidence")
    if not isinstance(ev, list) or not ev:
        return [f"{label}: {name} の根拠が無い"]
    if any(e not in known for e in ev):
        return [f"{label}: {name} の根拠が材料に無い ID を指す（{', '.join(str(e) for e in ev if e not in known)}）"]
    return []


def _candidate_problems(i: int, c, known: set[str]) -> list[str]:
    """候補 1 案の形の誤り（i は候補の並びの番号。0 始まり）。"""
    w = f"候補 {c.get('id', i + 1) if isinstance(c, dict) else i + 1}"
    if not isinstance(c, dict):
        return [f"{w}: オブジェクトでない"]
    errs = []
    parts = [("Mission", c.get("mission")), ("Vision", c.get("vision"))]
    values = c.get("values")
    if not isinstance(values, list) or not values:
        errs.append(f"{w}: Value が無い")
        values = []
    parts += [(f"Value {j}", v) for j, v in enumerate(values, 1)]
    if not isinstance(c.get("redlines"), list):
        errs.append(f"{w}: レッドライン（redlines）が無い")
    else:
        parts += [(r.get("id", "P") if isinstance(r, dict) else "P", r) for r in c["redlines"]]
    for name, part in parts:
        errs += _part_problems(w, name, part, known)
    return errs


def candidate_problems(data, known: set[str]) -> list[str]:
    """候補の形の誤り（I10・I19）。"""
    if not isinstance(data, dict):
        return ["JSON のオブジェクトでない"]
    errs = [f"{label}（{k}）が無い" for k, label in FOUR if not (isinstance(data.get(k), str) and data[k].strip())]
    cands = data.get("candidates")
    if not isinstance(cands, list) or len(cands) < 2:
        errs.append("候補が 2 案以上ない")
        cands = cands if isinstance(cands, list) else []
    for i, c in enumerate(cands):
        errs += _candidate_problems(i, c, known)
    qs = data.get("questions")
    if not isinstance(qs, list) or not any(isinstance(q, dict) and str(q.get("question") or "").strip() for q in qs):
        errs.append("利用者に決めてもらう点（questions）が無い")
    return errs


def candidate_body(c: dict) -> str:
    """候補 1 案を宣言の本文の形（`.ndf/mvv.md`）にする。"""
    lines = ["## Mission", "", c["mission"]["text"].strip(), "", "## Vision", "", c["vision"]["text"].strip(), "", "## Value", ""]
    for j, v in enumerate(c["values"], 1):
        title = f"**{v['title'].strip()}**: " if str(v.get("title") or "").strip() else ""
        lines.append(f"{j}. {title}{v['text'].strip()}")
    reds = [r for r in c.get("redlines") or [] if isinstance(r, dict)]
    if reds:
        lines += ["", f"## {pm.OPERATIONS_HEADING}", "", "| # | 操作 | 理由 |", "| --- | --- | --- |"]
        for j, r in enumerate(reds, 1):
            lines.append(f"| P{j} | {str(r['text']).strip()} | {str(r.get('reason') or '').strip()} |")
    return "\n".join(lines) + "\n"


def _refs(ev, by_id: dict) -> str:
    return "・".join(f"{e}（{by_id[e]['ref']}）" for e in ev if e in by_id)


def candidates_md(data: dict, mats: dict, kind: str, current: str | None, bodies: dict) -> str:
    by_id = {s["id"]: s for s in mats.get("sources", [])}
    title = "プロジェクト MVV の改訂案" if kind == "revision" else "プロジェクト MVV の候補"
    out = [f"# {title}", ""]
    for k, label in FOUR:
        out += [f"## {label}", "", data[k].strip(), ""]
    gl = [g for g in data.get("glossary") or [] if isinstance(g, dict) and g.get("term")]
    if gl:
        out += ["## 用語", ""] + [f"- **{g['term']}**: {g.get('meaning', '')}" for g in gl] + [""]
    for c in data["candidates"]:
        head = f"## 候補 {c.get('id', '')}" + ("（推奨）" if c.get("recommended") else "")
        out += [head, ""]
        if c.get("summary"):
            out += [str(c["summary"]), ""]
        out += ["### Mission", "", c["mission"]["text"], "", f"根拠: {_refs(c['mission']['evidence'], by_id)}", ""]
        out += ["### Vision", "", c["vision"]["text"], "", f"根拠: {_refs(c['vision']['evidence'], by_id)}", ""]
        out += ["### Value", ""]
        for j, v in enumerate(c["values"], 1):
            out.append(f"{j}. **{v.get('title', '')}**: {v['text']}（根拠: {_refs(v['evidence'], by_id)}）")
        out += ["", "### 必ず人の承認が要る操作（固有のもの）", ""]
        reds = c.get("redlines") or []
        if reds:
            out += ["| # | 操作 | 理由 | 根拠 |", "| --- | --- | --- | --- |"]
            out += [f"| P{j} | {r['text']} | {r.get('reason', '')} | {_refs(r['evidence'], by_id)} |" for j, r in enumerate(reds, 1)]
        else:
            out.append("- 無し（NDF の共通原則の C1〜C8 だけ）")
        out += ["", f"本文の案: `{bodies.get(c.get('id'), '')}`", ""]
    out += ["## 利用者に決めてもらう点", ""]
    for q in data["questions"]:
        if not isinstance(q, dict):
            continue
        out.append(f"- {q.get('question', '')}")
        for o in q.get("options") or []:
            if isinstance(o, dict):
                out.append(f"  - {o.get('label', '')}: {o.get('effect', '')}")
    if kind == "revision" and current is not None:
        rec = next((c for c in data["candidates"] if c.get("recommended")), data["candidates"][0])
        ch = pm.changes(current, candidate_body(rec))
        out += ["", "## 現行との差分（推奨の案）", ""] + ([f"- {c['item']}: {c['kind']}" for c in ch] or ["- 無し"])
    out += [
        "",
        "## 材料",
        "",
        f"- モード: {mats.get('mode')}（コミット {mats.get('counts', {}).get('commits')} 件・課題 {mats.get('counts', {}).get('issues')} 件）",
    ]
    out += [f"- 欠け: {m['what']}（{m['reason']}）" for m in mats.get("missing", [])]
    return "\n".join(out) + "\n"


def propose_prompt(mats: dict, current: str | None, reason: str | None) -> str:
    parts = [pm.principles(), pm.contract(), "# 材料"]
    body, size = [], 0
    for s in mats.get("sources", []):
        piece = f"- {s['id']} [{s['kind']}] {s['ref']}\n  " + s["text"].replace("\n", "\n  ")
        if size + len(piece) > MAX_PROMPT:
            break
        body.append(piece)
        size += len(piece)
    parts.append("\n".join(body) or "（材料が無い）")
    parts.append(
        f"モード: {mats.get('mode')}（{'履歴が少ないため、README・指示書・依頼文の傾向から書く' if mats.get('mode') == 'trend' else '履歴から書く'}）"
    )
    if current is not None:
        parts += ["# 現行のプロジェクト MVV（改訂の対象）", current.strip(), "# 改訂の理由", (reason or "（理由の記述なし）").strip()]
        parts.append("改訂案を書く。候補の 1 つは現行からの差分が最小の案にする。")
    return "\n\n".join(parts) + "\n"
