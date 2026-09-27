#!/usr/bin/env python3
"""project-mvv.py: プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`）の判定・候補・照合・承認・改訂の兆候（#1366）。

    python3 project-mvv.py check    [--root DIR]
    python3 project-mvv.py collect  [--root DIR] [--out-dir DIR] [--request-file F] [--trend-commits N] [--trend-issues N] [--budget 60]
    python3 project-mvv.py propose  --materials F [--current] [--reason-file F] [--out-dir DIR] [--root DIR]
    python3 project-mvv.py vet      --body F --kind candidate|revision|mission [--root DIR]
    python3 project-mvv.py approve  --body F --by NAME [--reason TEXT] [--accept-unknown] [--root DIR]
    python3 project-mvv.py show     [--version N] [--diff M] [--root DIR]
    python3 project-mvv.py context  [--root DIR] [--format text|json]
    python3 project-mvv.py signals  [--root DIR] [--format text|json]
    python3 project-mvv.py schema   [--out FILE]

`development-workflow` の手順 0 が `check` を打つ。手順（対話・承認・改訂）は
`skills/development-workflow/references/project-mvv.md` にある。

- `check`: 0 = 承認済みで一致 / 2 = 無い / 3 = 壊れている / 4 = 承認と一致しない・未承認。LLM も `gh` も呼ばず、書かない
- `collect`: git・`gh`（読み取りだけ）・ファイルから材料を集め、リポジトリの外の状態ディレクトリへ `materials.json` を書く。
  LLM を呼ばない。コミット数と課題数の両方が閾値に満たなければ傾向モード（README・指示書・依頼文だけ）
- `propose`: 最小構成の claude -p を 1 回呼び、候補 2 案以上と分かれる点を `candidates.json`・`candidates.md` へ書く。
  形が足りなければ 1
- `vet`: 本文を NDF の共通原則（と承認済みのプロジェクト MVV）に照らす。0 = 従う / 10 = 反する疑い・判定できない・読めない。
  照合は `~/.local/state/ndf/project-mvv.jsonl` に残す
- `approve`: 利用者の承認の後に打つ。同じ本文への直近の照合が「従う」（か `--accept-unknown` で人が引き受けた「判定できない」）
  のときだけ宣言を書く。書くのはこの副命令だけである
- `show`: 版の本文、または版 M から N への差分
- `context`: 判断の地点へ渡す MVV の節（宣言が無くても共通原則と「MVV なし」を出す）
- `signals`: 現行の版のもとでの覆し・「判定できない」・流出不具合の件数と閾値。超えていれば `items` に改訂の提案

結果は lib/step_result.py の形の 1 行の JSON で、その前に人が読む行を出す。LLM は `supervise_lib/claude.py` の
`call_claude` を Tool なしで呼ぶ（NDF_SUPERVISE_CLAUDE で差し替えられる）。
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
import jsonio  # noqa: E402
import project_mvv as pm  # noqa: E402
from step_result import EXIT_GATE, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, main_with, result, validate_result  # noqa: E402

from project_lib.secret import is_secret_path, redact  # noqa: E402

TOOL = "project-mvv"
EXIT_MISMATCH = 4
MAX_SOURCE = 2000  # 材料 1 件の上限の文字数
MAX_COMMITS = 400  # 材料に入れるコミットの件名の数（新しい順）
MAX_ISSUES = 200  # 材料に入れる課題の数（新しい順）
ISSUE_LIMIT = 5000  # 課題の件数を数える上限
MAX_PROMPT = 150_000  # 候補の生成へ渡す材料の上限の文字数
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md", "GEMINI.md")
README_FILES = ("README.md", "README.rst", "README.txt", "README")
FOUR = (("context", "経緯"), ("premise", "前提"), ("evidence", "根拠"), ("options", "選択肢"))
VET_VERDICTS = ("follow", "suspect", "unknown")

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

VET_SYSTEM = """あなたは MVV の照合者である。Tool は無い。
渡された本文（プロジェクト MVV の候補・改訂案、またはミッション MVV）が、NDF の共通原則と（あれば）承認済みのプロジェクト MVV に
従うかを判定する。
- 共通原則の必ず人の承認が要る操作（C1〜）のどれかを承認なしで行えると書く、または優先順位を変える・入れ替えるなら suspect。
  locations の item に C の番号か priority を書く
- ミッション MVV がプロジェクト MVV のレッドライン（P の番号）を緩める・Value を打ち消すなら suspect。item にその番号を書く
- 原則に反する（人を害する・人の発展を妨げる）なら suspect
- 反する所が無ければ follow。材料から決められなければ unknown（迷ったら unknown）
- 本文を書き換えない。判定と箇所を返すだけである
出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|suspect|unknown", "locations": [{"item": "C4|priority|Value 3|P1", "reason": "..."}]}"""


# ---------------------------------------------------------------- 共通


def _root(a) -> Path:
    return Path(a.root).resolve() if getattr(a, "root", None) else Path.cwd().resolve()


def _emit_code(obj: dict, code: int) -> None:
    """`emit` の対応表に無い終了コード（4 = 承認と一致しない）で終える。形は同じ検査を通す。"""
    errs = validate_result(obj)
    if errs:
        print("結果の形が誤っている: " + " / ".join(errs), file=sys.stderr)
        raise SystemExit(EXIT_UNREADABLE)
    print(json.dumps(obj, ensure_ascii=False))
    raise SystemExit(code)


def _work_dir(root: Path, out_dir: str | None) -> Path:
    if out_dir:
        d = Path(out_dir).expanduser().resolve()
    else:
        name = re.sub(r"[^0-9A-Za-z._-]+", "-", str(root)).strip("-") or "root"
        d = pm.state_base() / "project-mvv" / name
    try:
        d.relative_to(root)
        raise StepError(f"作業ファイルはリポジトリの外に置く（承認の前に .ndf/ へ何も書かない）: {d}", 1)
    except ValueError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    return d


def _call_llm(system: str, prompt: str, root: Path, kind: str) -> tuple[dict | None, str, dict]:
    """最小構成の claude -p を 1 回呼ぶ（Tool なし）。(JSON, 生の文, 使用量)。"""
    import supervise_lib  # noqa: F401  lib/ を sys.path へ足す
    import usage_ledger
    from supervise_lib.claude import call_claude

    res = call_claude(system, prompt, None, str(root), 900)
    usage = {"cost_usd": res.get("cost"), "seconds": res.get("seconds")}
    usage_ledger.append_safely(
        str(root),
        usage_ledger.UsageRecord(
            source=TOOL,
            kind=kind,
            usage=res.get("usage") or {},
            model_usage=res.get("model_usage"),
            cost_usd=res.get("cost"),
            turns=res.get("turns"),
            seconds=res.get("seconds"),
            session_id=res.get("session"),
        ),
    )
    text = str(res.get("text") or "")
    if not res.get("ok"):
        return None, text[-500:], usage
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start : end + 1]) if start >= 0 else None
    except json.JSONDecodeError:
        data = None
    return (data if isinstance(data, dict) else None), text[-500:], usage


# ---------------------------------------------------------------- check


def cmd_check(a):
    root = _root(a)
    mvv = pm.load(root)
    label = pm.STATUS_LABEL[mvv.status]
    guide = {
        "none": "策定の手順（references/project-mvv.md）へ入る: collect → propose → 対話 → vet → 承認 → approve",
        "unapproved": "本文を vet に通し、利用者の承認の後に approve を打つ",
        "mismatch": "本文を承認の版へ戻すか、改訂の手順（propose --current → vet --kind revision → approve --reason）へ入る",
        "unreadable": ".ndf/mvv.json を直す（approve は壊れた宣言に版を足さない）",
    }
    print(f"プロジェクト MVV: {label}" + (f"（版 {mvv.version}）" if mvv.approved else ""))
    if mvv.error:
        print(mvv.error)
    items = [{"kind": "status", "name": mvv.status, "result": mvv.status, **pm.record(mvv)}]
    if mvv.approved:
        emit(result(TOOL, "ok", f"プロジェクト MVV は承認済み（版 {mvv.version}）", items, pm.record(mvv)))
    items.append({"kind": "next", "name": guide[mvv.status], "result": "guide"})
    out = result(TOOL, "stopped", label + (f": {mvv.error}" if mvv.error else ""), items, pm.record(mvv), next=guide[mvv.status])
    if mvv.status == "none":
        emit(out, EXIT_UNREADABLE)
    if mvv.status == "unreadable":
        emit(out, EXIT_PRECONDITION)
    _emit_code(out, EXIT_MISMATCH)


# ---------------------------------------------------------------- collect


class Deadline:
    def __init__(self, budget: float):
        self.end = time.monotonic() + budget

    def left(self) -> float:
        return self.end - time.monotonic()

    def timeout(self) -> float:
        return max(1.0, min(30.0, self.left()))


def _run(cmd: list[str], root: Path, dl: Deadline) -> subprocess.CompletedProcess | str:
    """1 回のコマンド。失敗の理由は文字列で返す。"""
    if dl.left() <= 0:
        return "時間切れ"
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=dl.timeout())
    except FileNotFoundError:
        return f"{cmd[0]} が無い"
    except subprocess.TimeoutExpired:
        return f"{' '.join(cmd[:3])} が打ち切られた"
    if p.returncode != 0:
        return f"{' '.join(cmd[:3])}: {(p.stderr or p.stdout).strip()[:200]}"
    return p


def _clean(text: str) -> str:
    return "\n".join(redact(ln) for ln in (text or "").splitlines())[:MAX_SOURCE]


def _doc_sources(root: Path, rel: str, kind: str) -> list[dict]:
    """ファイルを `## ` の節ごとの出典に分ける。ref は `パス:行`。"""
    if is_secret_path(rel):
        return []
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = text.splitlines()
    starts = [0] + [i for i, ln in enumerate(lines) if ln.startswith("## ") and i > 0]
    out = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[s:e]).strip()
        if chunk:
            out.append({"kind": kind, "ref": f"{rel}:{s + 1}", "text": _clean(chunk)})
    return out


def _decision_docs(root: Path) -> list[str]:
    d = root / "docs"
    if not d.is_dir():
        return []
    found = sorted(str(p.relative_to(root)) for p in d.rglob("*.md") if "decision" in p.name.lower())
    return found[:5]


def _issue_repo(root: Path) -> str | None:
    import repo

    return repo.owner_repo(root)


def collect_materials(root: Path, a) -> dict:
    started = time.monotonic()
    dl = Deadline(a.budget)
    settings, err = pm.read_settings(root)
    if err:
        raise StepError(err, EXIT_PRECONDITION)
    th = {
        "commits": a.trend_commits if a.trend_commits is not None else settings["trend_commits"],
        "issues": a.trend_issues if a.trend_issues is not None else settings["trend_issues"],
    }
    missing: list[dict] = []
    commits: list[str] = []
    n_commits = 0
    p = _run(["git", "rev-list", "--count", "HEAD"], root, dl)
    if isinstance(p, str):
        missing.append({"what": "git の履歴", "reason": p})
    else:
        n_commits = int(p.stdout.strip() or 0)
        q = _run(["git", "log", "--no-merges", f"-{MAX_COMMITS}", "--format=%h %s"], root, dl)
        if isinstance(q, str):
            missing.append({"what": "コミットの件名", "reason": q})
        else:
            commits = [ln for ln in q.stdout.splitlines() if ln.strip()]
    issues: list[dict] = []
    slug = _issue_repo(root)
    if not slug:
        missing.append({"what": "課題", "reason": "origin が GitHub でない（課題を読まない）"})
    else:
        q = _run(
            ["gh", "issue", "list", "--repo", slug, "--state", "all", "--limit", str(ISSUE_LIMIT), "--json", "number,title,body"], root, dl
        )
        if isinstance(q, str):
            missing.append({"what": "課題", "reason": q})
        else:
            try:
                issues = json.loads(q.stdout or "[]")
            except ValueError:
                missing.append({"what": "課題", "reason": "gh の出力を読めない"})
    counts = {"commits": n_commits, "issues": len(issues)}
    mode = "trend" if counts["commits"] < th["commits"] and counts["issues"] < th["issues"] else "history"
    raw: list[dict] = []
    if a.request_file:
        try:
            raw.append({"kind": "request", "ref": a.request_file, "text": _clean(Path(a.request_file).read_text(encoding="utf-8"))})
        except OSError as e:
            missing.append({"what": "依頼文", "reason": str(e)})
    for rel in README_FILES:
        if (root / rel).is_file():
            raw += _doc_sources(root, rel, "readme")
            break
    for rel in INSTRUCTION_FILES:
        if (root / rel).is_file():
            raw += _doc_sources(root, rel, "instructions")
    if mode == "history":
        for rel in _decision_docs(root):
            raw += _doc_sources(root, rel, "doc")
        for it in sorted(issues, key=lambda x: -int(x.get("number") or 0))[:MAX_ISSUES]:
            raw.append({"kind": "issue", "ref": f"#{it.get('number')}", "text": _clean(f"{it.get('title', '')}\n\n{it.get('body') or ''}")})
        raw += [{"kind": "commit", "ref": redact(ln), "text": redact(ln)} for ln in commits]
    sources = [{"id": f"S{i}", **s} for i, s in enumerate(raw, 1)]
    return {
        "root": str(root),
        "mode": mode,
        "counts": counts,
        "thresholds": th,
        "sources": sources,
        "missing": missing,
        "seconds": round(time.monotonic() - started, 2),
    }


def cmd_collect(a):
    root = _root(a)
    data = collect_materials(root, a)
    out = _work_dir(root, a.out_dir) / "materials.json"
    jsonio.write_atomic(out, data)
    print(f"材料: {out}（{data['mode']}・出典 {len(data['sources'])} 件・{data['seconds']} 秒）")
    for m in data["missing"]:
        print(f"欠け: {m['what']}（{m['reason']}）")
    emit(
        result(
            TOOL,
            "ok",
            f"材料を集めた（{'傾向モード' if data['mode'] == 'trend' else '履歴モード'}・出典 {len(data['sources'])} 件）: {out}",
            [{"kind": "missing", "name": m["what"], "result": "missing", "reason": m["reason"]} for m in data["missing"]],
            {"materials": str(out), "mode": data["mode"], **data["counts"], "sources": len(data["sources"]), "seconds": data["seconds"]},
        )
    )


# ---------------------------------------------------------------- propose


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
        w = f"候補 {c.get('id', i + 1) if isinstance(c, dict) else i + 1}"
        if not isinstance(c, dict):
            errs.append(f"{w}: オブジェクトでない")
            continue
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
            if not isinstance(part, dict) or not str(part.get("text") or "").strip():
                errs.append(f"{w}: {name} の本文が無い")
                continue
            ev = part.get("evidence")
            if not isinstance(ev, list) or not ev:
                errs.append(f"{w}: {name} の根拠が無い")
            elif any(e not in known for e in ev):
                errs.append(f"{w}: {name} の根拠が材料に無い ID を指す（{', '.join(str(e) for e in ev if e not in known)}）")
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


def cmd_propose(a):
    root = _root(a)
    mpath = Path(a.materials).resolve()
    try:
        mats = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"材料を読めない: {mpath}: {e}", EXIT_UNREADABLE) from None
    current = None
    kind = "new"
    if a.current:
        mvv = pm.load(root)
        if not mvv.approved:
            raise StepError(f"--current には承認済みのプロジェクト MVV が要る（今は {pm.STATUS_LABEL[mvv.status]}）", EXIT_PRECONDITION)
        current, kind = mvv.body, "revision"
    reason = Path(a.reason_file).read_text(encoding="utf-8") if a.reason_file else None
    data, raw, usage = _call_llm(PROPOSE_SYSTEM, propose_prompt(mats, current, reason), root, "mvv-propose")
    known = {s["id"] for s in mats.get("sources", [])}
    errs = ["LLM が候補を返さない"] if data is None else candidate_problems(data, known)
    if errs:
        emit(
            result(
                TOOL,
                "stopped",
                "候補の形が足りない: " + "／".join(errs[:10]),
                [{"kind": "materials", "name": str(mpath), "result": "ask_user", "reason": "材料を示して利用者に直接問う", "raw": raw}],
                usage,
                next="材料を示して利用者に直接問う",
            ),
            1,
        )
    out_dir = _work_dir(root, a.out_dir or str(mpath.parent))
    bodies = {}
    for c in data["candidates"]:
        p = out_dir / f"candidate-{re.sub(r'[^0-9A-Za-z_-]+', '', str(c.get('id') or len(bodies) + 1))}.md"
        p.write_text(candidate_body(c), encoding="utf-8")
        bodies[c.get("id")] = str(p)
    cj = out_dir / "candidates.json"
    jsonio.write_atomic(cj, {"materials": str(mpath), "kind": kind, **data, "bodies": bodies})
    cm = out_dir / "candidates.md"
    cm.write_text(candidates_md(data, mats, kind, current, bodies), encoding="utf-8")
    print(f"候補: {cm}（{len(data['candidates'])} 案）")
    emit(
        result(
            TOOL,
            "ok",
            f"候補を {len(data['candidates'])} 案書いた: {cm}",
            [
                {
                    "kind": "candidate",
                    "name": str(c.get("id")),
                    "result": "recommended" if c.get("recommended") else "option",
                    "body": bodies[c.get("id")],
                }
                for c in data["candidates"]
            ],
            {**usage, "candidates_json": str(cj), "candidates_md": str(cm)},
            presentation_path=str(cm),
            next="candidates.md を示し、分かれる点を AskUserQuestion で問う。承認の前に .ndf/ へ書かない",
        )
    )


# ---------------------------------------------------------------- vet


def vet_prompt(kind: str, body: str, mvv: pm.ProjectMvv) -> str:
    what = {"candidate": "プロジェクト MVV の候補", "revision": "プロジェクト MVV の改訂案", "mission": "ミッション MVV"}[kind]
    parts = [pm.principles()]
    if kind in ("mission", "revision"):
        parts.append(pm.project_part(mvv))
    parts += [pm.contract(), f"# 照合する本文（{what}）", body.strip()]
    return "\n\n".join(parts) + "\n"


def vet_body(root: Path, body: str, kind: str) -> tuple[dict, dict]:
    """(照合の記録, 使用量)。記録は project-mvv.jsonl へ足す。本文は書き換えない（I14）。"""
    mvv = pm.load(root)
    rec = {
        "at": pm.now_iso(),
        "repo": pm.repo_key(root),
        "kind": kind,
        "sha256": pm.sha256_text(body),
        "project_sha256": mvv.sha256 if mvv.approved else "",
        "project_mvv": pm.record(mvv),
        "verdict": "unreadable",
        "locations": [],
    }
    usage: dict = {}
    if kind in ("candidate", "revision"):
        probs = pm.shape_problems(body)
        if probs:
            rec.update(verdict="suspect", locations=probs, machine=True)
    if kind == "revision" and not mvv.approved and rec["verdict"] == "unreadable":
        rec["locations"] = [
            {"item": "project_mvv", "reason": f"改訂には承認済みのプロジェクト MVV が要る（今は {pm.STATUS_LABEL[mvv.status]}）"}
        ]
    elif rec["verdict"] == "unreadable":
        data, raw, usage = _call_llm(VET_SYSTEM, vet_prompt(kind, body, mvv), root, "mvv-vet")
        if data is None or data.get("verdict") not in VET_VERDICTS:
            rec.update(raw=raw)
        else:
            locs = [x for x in data.get("locations") or [] if isinstance(x, dict)]
            rec.update(verdict=data["verdict"], locations=locs)
        rec.update(cost_usd=usage.get("cost_usd"), seconds=usage.get("seconds"))
    pm.append_jsonl(pm.vet_log_path(), rec)
    return rec, usage


def cmd_vet(a):
    root = _root(a)
    try:
        body = Path(a.body).read_text(encoding="utf-8")
    except OSError as e:
        raise StepError(f"本文を読めない: {e}", EXIT_UNREADABLE) from None
    rec, usage = vet_body(root, body, a.kind)
    items = [
        {"kind": "location", "name": str(x.get("item", "")), "result": rec["verdict"], "reason": str(x.get("reason", ""))}
        for x in rec["locations"]
    ]
    for it in items:
        print(f"{it['name']}: {it['reason']}")
    if rec["verdict"] == "follow":
        emit(result(TOOL, "ok", f"照合: 従う（{a.kind}・sha256 {rec['sha256'][:12]}）", items, usage))
    label = {"suspect": "反する疑い", "unknown": "判定できない", "unreadable": "読めない"}[rec["verdict"]]
    nxt = (
        "箇所を示して本文を直し、照合をやり直す"
        if rec["verdict"] == "suspect"
        else "人へ戻す（判定できないを引き受けるなら approve --accept-unknown）"
    )
    emit(result(TOOL, "gate", f"照合: {label}（{a.kind}）", items, usage, next=nxt), EXIT_GATE)


# ---------------------------------------------------------------- approve


def last_vet(root: Path, sha: str) -> dict | None:
    key = pm.repo_key(root)
    rows = [
        r
        for r in pm.read_jsonl(pm.vet_log_path())
        if r.get("repo") == key and r.get("sha256") == sha and r.get("kind") in ("candidate", "revision")
    ]
    return rows[-1] if rows else None


def cmd_approve(a):
    root = _root(a)
    try:
        body = Path(a.body).read_text(encoding="utf-8")
    except OSError as e:
        raise StepError(f"本文を読めない: {e}", 1) from None
    probs = pm.shape_problems(body)
    if probs:
        raise StepError("本文の形が足りない: " + "／".join(f"{p['item']}: {p['reason']}" for p in probs), 1)
    mvv = pm.load(root)
    if mvv.status == "unreadable":
        raise StepError(f"宣言が壊れているため版を足さない: {mvv.error}", 1)
    sha = pm.sha256_text(body)
    vet = last_vet(root, sha)
    if vet is None:
        raise StepError("この本文への照合の記録が無い（vet を打ってから利用者の承認を取る）", 1)
    if vet["verdict"] == "unknown" and not a.accept_unknown:
        raise StepError("照合が「判定できない」。人が引き受けると決めたときだけ --accept-unknown で承認する", 1)
    if vet["verdict"] not in ("follow", "unknown"):
        where = "／".join(f"{x.get('item')}: {x.get('reason')}" for x in vet.get("locations") or [])
        raise StepError(f"照合が「{vet['verdict']}」のため承認できない（{where}）。本文を直して照合をやり直す", 1)
    body_path, decl_path = pm.decl_paths(root)
    versions: list = []
    settings: dict = json.loads(json.dumps(pm.DEFAULTS))
    if decl_path.is_file():
        data = json.loads(decl_path.read_text(encoding="utf-8"))
        versions, settings = list(data["versions"]), data.get("settings") or {}
    reason = (a.reason or "").strip()
    if versions:
        prev = versions[-1]
        if not reason:
            raise StepError("版 2 以降は改訂の理由（--reason）が要る", 1)
        if prev["sha256"] == sha:
            raise StepError(f"本文が現行の版 {prev['version']} と同じ", 1)
        diff = pm.changes(prev["body"], body)
    else:
        diff = []
        reason = reason or "初版"
    v = {
        "version": len(versions) + 1,
        "sha256": sha,
        "approved_at": pm.now_iso(),
        "approved_by": a.by,
        "reason": reason,
        "vet": {"verdict": vet["verdict"], "at": vet["at"], "accepted_unknown": vet["verdict"] == "unknown"},
        "changes": diff,
        "body": body,
    }
    decl = {"version": pm.DECL_VERSION, "body": pm.BODY_FILE, "settings": settings, "versions": [*versions, v]}
    errs = pm.decl_problems(decl)
    if errs:
        raise StepError("宣言の形が合わない: " + "／".join(errs), 1)
    try:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_text(body, encoding="utf-8")
        jsonio.write_atomic(decl_path, decl)
    except OSError as e:
        raise StepError(f"宣言を書けない: {e}。本文を利用者へ示す", 1) from None
    print(f"プロジェクト MVV: 版 {v['version']} を書いた（{body_path}・{decl_path}）")
    emit(
        result(
            TOOL,
            "ok",
            f"プロジェクト MVV の版 {v['version']} を書いた（sha256 {sha[:12]}）",
            [{"kind": "change", "name": c["item"], "result": c["kind"]} for c in diff],
            {"version": v["version"], "sha256": sha, "body": str(body_path), "decl": str(decl_path)},
        )
    )


# ---------------------------------------------------------------- show


def _versions(root: Path) -> list[dict]:
    _, decl_path = pm.decl_paths(root)
    try:
        data = json.loads(decl_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise StepError(f"宣言を読めない: {e}", EXIT_UNREADABLE) from None
    if pm.decl_problems(data):
        raise StepError(f"宣言の形が違う: {decl_path}", EXIT_UNREADABLE)
    return data["versions"]


def cmd_show(a):
    root = _root(a)
    vs = _versions(root)
    n = a.version or len(vs)
    if not 1 <= n <= len(vs):
        raise StepError(f"版 {n} が無い（1〜{len(vs)}）", EXIT_UNREADABLE)
    v = vs[n - 1]
    if a.diff:
        if not 1 <= a.diff <= len(vs):
            raise StepError(f"版 {a.diff} が無い（1〜{len(vs)}）", EXIT_UNREADABLE)
        old = vs[a.diff - 1]
        text = "".join(difflib.unified_diff(old["body"].splitlines(True), v["body"].splitlines(True), f"版 {a.diff}", f"版 {n}"))
        print(text)
        ch = pm.changes(old["body"], v["body"])
        emit(
            result(
                TOOL,
                "ok",
                f"版 {a.diff} → 版 {n} の差分（{len(ch)} 項目）",
                [{"kind": "change", "name": c["item"], "result": c["kind"]} for c in ch],
                {"from": a.diff, "to": n},
            )
        )
    print(v["body"])
    emit(
        result(
            TOOL,
            "ok",
            f"版 {n}（{v['approved_at']}・{v['approved_by']}・{v.get('reason') or ''}）",
            [{"kind": "version", "name": str(n), "result": "shown", "sha256": v["sha256"], "reason": v.get("reason") or ""}],
            {"version": n, "versions": len(vs)},
        )
    )


# ---------------------------------------------------------------- context / signals


def cmd_context(a):
    root = _root(a)
    mvv = pm.load(root)
    text = pm.block(mvv)
    if a.format == "text":
        sys.stdout.write(text)
        return 0
    emit(
        result(
            TOOL,
            "ok",
            f"MVV の節（{pm.STATUS_LABEL[mvv.status]}）",
            [{"kind": "context", "name": mvv.status, "result": mvv.status, "project_mvv": pm.record(mvv), "block": text}],
            pm.record(mvv),
        )
    )


def escape_events(root: Path) -> list[dict]:
    """流出不具合の記録（check-trigger.py の `kind: escape`）。読めなければ空。"""
    try:
        spec = importlib.util.spec_from_file_location("ndf_check_trigger", HERE / "check-trigger.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [e for e in mod.read_events(root) if e.get("kind") == "escape"]
    except Exception:  # noqa: BLE001  集計の材料が欠けても止めない
        return []


def cmd_signals(a):
    root = _root(a)
    mvv = pm.load(root)
    if mvv.status == "unreadable":
        raise StepError(f"宣言が壊れている: {mvv.error}", EXIT_PRECONDITION)
    sig = pm.signals(root, mvv, escapes=escape_events(root))
    sug = pm.revise_suggestion(sig) if mvv.approved else None
    c, t = sig["counts"], sig["thresholds"]
    head = f"版 {mvv.version}" if mvv.approved else pm.NO_MVV
    lines = [
        f"改訂の兆候（{head}）",
        f"  覆し: {c['overrides']} 件（従うを退けた {c['override_reject']}・反する疑いか判定できないを通した {c['override_pass']}。閾値 {t['overrides']}）",
        f"  判定できない: {c['unknowns']} 件（閾値 {t['unknowns']}）・続けて {c['unknown_streak']} 回（閾値 {t['unknown_streak']}）",
        f"  流出不具合: {c['escapes']} 件（閾値 {t['escapes']}）",
    ]
    if a.format == "text":
        print("\n".join(lines + ([f"  → {sug['reason']}"] if sug else [])))
    emit(result(TOOL, "ok", "；".join(ln.strip() for ln in lines), [sug] if sug else [], {**c, "version": mvv.version}))


def cmd_schema(a):
    import deps

    deps.require("schema")
    text = pm.json_schema()
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        emit(result(TOOL, "ok", f"schema を書いた: {a.out}"))
    sys.stdout.write(text)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="project-mvv.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--root", help="リポジトリの根（既定はカレント）")
        p.set_defaults(func=func)
        return p

    add("check", cmd_check, "宣言の有無と承認との一致を判定する")
    p = add("collect", cmd_collect, "候補の材料を集める")
    p.add_argument("--out-dir")
    p.add_argument("--request-file", help="依頼文のファイル（傾向モードの材料）")
    p.add_argument("--trend-commits", type=int)
    p.add_argument("--trend-issues", type=int)
    p.add_argument("--budget", type=float, default=60.0, help="締め切りの秒（既定 60）")
    p = add("propose", cmd_propose, "候補を書く")
    p.add_argument("--materials", required=True)
    p.add_argument("--current", action="store_true", help="改訂案を書く（現行の本文を材料に足す）")
    p.add_argument("--reason-file")
    p.add_argument("--out-dir")
    p = add("vet", cmd_vet, "本文を NDF の共通原則とプロジェクト MVV に照らす")
    p.add_argument("--body", required=True)
    p.add_argument("--kind", required=True, choices=("candidate", "revision", "mission"))
    p = add("approve", cmd_approve, "利用者の承認の後に宣言を書く")
    p.add_argument("--body", required=True)
    p.add_argument("--by", required=True, help="承認者")
    p.add_argument("--reason")
    p.add_argument("--accept-unknown", action="store_true")
    p = add("show", cmd_show, "版の本文か差分を出す")
    p.add_argument("--version", type=int)
    p.add_argument("--diff", type=int, help="この版から --version（既定は現行）への差分")
    p = add("context", cmd_context, "MVV の節を出す")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p = add("signals", cmd_signals, "改訂の兆候を集計する")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p = sub.add_parser("schema", help="宣言の JSON Schema を出す")
    p.add_argument("--out")
    p.set_defaults(func=cmd_schema)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
