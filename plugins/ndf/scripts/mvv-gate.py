#!/usr/bin/env python3
"""mvv-gate.py: 関門の材料が MVV に従うかを判定し、従えば関門を省いた記録を残す（#1078）。

    python3 mvv-gate.py check --sprint <スプリントの状態> --gate design|release
                              [--material <ファイル>...] [--pr N...] [--mode M]
                              [--log <jsonl>] [--repo OWNER/REPO] [--root DIR] [--note <ファイル>] [--advise]

`pace: fast` と `pace: auto` の関門 1（設計の承認）と関門 2（本番への配布の承認）を、利用者が承認した MVV への事前の
承認として扱うための判定である。判定の前に、次を機械で見る。1 つでも外れれば LLM を呼ばずに関門へ戻す。

1. プロジェクト MVV（`.ndf/mvv.md`・`.ndf/mvv.json`。#1366）が 未承認・承認と一致しない・壊れている のどれでもない
2. スプリントの状態に MVV（`mvv.path`・`mvv.sha256`）と、利用者の承認の記録（関門 `MVV` の `sha256`）があり、今の MVV のファイルの
   ハッシュ・状態の `mvv.sha256`・承認の記録の `sha256` がすべて一致する。スプリント MVV が無いスプリントは、承認済みのプロジェクト MVV と
   状態に残したその参照（`project_mvv.sha256`）が一致すればよい（`lib/project_mvv.approval_refusal`）
3. `--mode` が `operation` でも `documentation` でもない
4. `--pr` の変更したファイルが、進め方の宣言（`.ndf/pace.json`）の `boundary_paths` に当たらない
5. `--pr` がプロジェクト MVV の宣言（`.ndf/mvv.md`・`.ndf/mvv.json`）を変えない（共通原則の C7。宣言では外せない固定の検査）

通れば、MVV の節（NDF の共通原則の本文全体 → プロジェクト MVV → スプリント MVV → 判断の決まり。`lib/project_mvv.block`）と
材料（提示物のファイル・PR の題名と本文と変更したファイル。`--gate design` では PR が変更した `issues/` の設計文書の
PR の先頭のコミットの中身も足す）を最小構成の claude -p に渡し、
「従う / 従わない / 判定できない」と理由と越えない線と根拠の項目（`basis`）を JSON で返させる。

- 従う（越えない線なし）: `sprint-state.py gate` で関門の記録（`by: mvv`・判定・理由・ログ）を書き、
  status ok（終了コード 0）。記録を書けなければ関門へ戻す
- それ以外（従わない・判定できない・越えない線・判定を読めない・材料を取れない）: status gate（終了コード 10）。
  利用者の承認を求める

判定は毎回 --log（既定 ~/.local/state/ndf/mvv-gate.jsonl）へ 1 行で残す（プロジェクト MVV の参照 `project_mvv`・根拠の項目 `basis`・
スプリントの状態の進め方 `pace`・判定に渡したスプリント MVV の `sprint_mvv`（sha256 か null）を含む）。同じプロジェクト MVV のもとで「判定できない」が宣言の回数（`settings.unknown_streak`）続くか、改訂の兆候が閾値を超えると、
結果の `items` に改訂の提案を載せる。--note を渡すと、Pull Request の
コメントに使う判定の記録（判定・理由・ログ）を Markdown で書く。claude は NDF_MVV_CLAUDE で差し替えられる。

`--advise`（助言の MVV 判定。`pace: normal` の承認ゲート 1・2 の前。#1400）: 判定の規則と機械のチェックは同じだが、
承認ゲートの記録（`by: mvv`）を書かず、どの判定でも status ok（終了コード 0）で返す。承認するのは利用者である。

- プロジェクト MVV が承認済みでなければ、LLM を呼ばず、行も --note も書かずに `items[0]` を `{"verdict": "none", "status": ...}` にする
- スプリント MVV の承認の記録（関門 `MVV`）を求めず、ファイルと状態の sha256 の一致だけを見る
- 進め方の宣言（`.ndf/pace.json`）が無ければ越えない線のパスを空とする（壊れていれば今どおり外れ）
- --note は機械のチェックで外れた・読めないときも含めて書き、外れた理由を理由の欄に載せる
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import deps  # noqa: E402

deps.require("schema", "procs")  # schema は supervise_lib.paths → decl、procs は ask() の supervise_lib.claude が使う
from step_result import EXIT_GATE, emit, result  # noqa: E402
import clock  # noqa: E402
import legacy_names  # noqa: E402
import gh_call  # noqa: E402
import gh_rest  # noqa: E402
import jsonio  # noqa: E402
from pace import DECL_NAME, EXCLUDED_MODES, PaceError, matches, read_pace  # noqa: E402
import project_mvv as pm  # noqa: E402
import project_mvv_signals as pms  # noqa: E402

TOOL = "mvv-gate"
GATES = {"design": "関門 1（設計の承認）", "release": "関門 2（本番への配布の承認）"}
GATE_NAMES = {"design": "関門 1", "release": "関門 2"}  # sprint-state.py の関門の記録の名前
MVV_GATE = "MVV"  # 利用者が MVV を承認した記録の名前
VERDICTS = ("follow", "not_follow", "unknown")
MAX_MATERIAL = 60_000  # 材料 1 件の上限の文字数（超えた分は切る）

DECL_PATHS = (f"{pm.DECL_DIR}/{pm.BODY_FILE}", f"{pm.DECL_DIR}/{pm.DECL_FILE}")  # 変える PR は MVV 判定で通さない（I15）

SYSTEM = """あなたは開発の関門の判定者である。利用者はプロジェクト MVV かスプリントの MVV（Mission / Vision / Value）を承認済みで、
MVV に従う変更なら関門での個別の承認を省いてよいと決めている。材料を読み、この変更が NDF の共通原則・プロジェクト MVV・
スプリント MVV に従うかを判定する（優先順位は共通原則のとおり）。

- 共通原則に反せず、Mission と Vision に沿い、Value のどれにも反しないなら follow
- どれかに反するなら not_follow。材料から読み取れず決められないなら unknown（迷ったら unknown）
- 配布の前に取れない実測（配布した後の効果）は判定の対象にしない。配布の後の測定へ回るものとして扱い、
  それが無いことだけを理由に unknown にしない
- 必ず人の承認が要る操作（共通原則の C の番号・プロジェクト MVV の P の番号・スプリント MVV の越えない線）に当たる変更を
  含むなら、boundary にその中身を書く（boundary が空でなければ関門は省かれない）
- basis に根拠にした項目の番号（Mission / Vision / Value 3 / C4 / P1 / R2 など）を並べる

出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|not_follow|unknown", "reasons": ["MVV のどの項目に照らしてそう判断したか（1〜5 件）"], "boundary": [], "basis": ["Value 1"]}"""


class Back(Exception):
    """関門へ戻す（利用者の承認を求める）理由。"""


def gh_or_back(args: list[str], repo: str | None, cwd: Path) -> str:
    """`gh` を 1 回呼び（`lib/gh_call.py`）、標準出力を返す。失敗は Back。"""
    r = gh_call.gh([*args] + (["--repo", repo] if repo else []), cwd=str(cwd))
    if r.returncode == 127:
        raise Back("gh が無い")
    if r.returncode != 0:
        raise Back(f"gh {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def pr_facts(n: int, repo: str | None, cwd: Path) -> dict:
    """PR の題名・本文・先頭のコミットと、変更したファイルの全件（rename の前のパスを含む）。取れなければ Back。

    REST の `pulls/<n>/files` は全ページでも 3000 件で黙って切れるため、取れた件数が `changedFiles` と
    合わなければ全件を確かめられないとして Back にする。
    """
    r = gh_rest.view_json("pr", n, "title,body,headRefOid,changedFiles", repo, cwd=str(cwd))
    if r.returncode != 0:
        raise Back("gh が無い" if r.returncode == 127 else f"gh pr view {n}: {r.stderr.strip()[:300]}")
    try:
        info = json.loads(r.stdout)
    except ValueError:
        raise Back(f"PR #{n} の出力を読めない")
    f = gh_rest.pr_files(n, repo, cwd=str(cwd))
    if f.returncode != 0:
        raise Back("gh が無い" if f.returncode == 127 else f"PR #{n} の変更したファイルを読めない: {f.stderr.strip()[:300]}")
    files = json.loads(f.stdout)
    changed = info.get("changedFiles") if isinstance(info, dict) else None
    if not isinstance(changed, int) or isinstance(changed, bool) or changed != len(files):
        raise Back(f"PR #{n} の変更したファイルを全件読めない（取れた {len(files)} 件 / changedFiles {changed}）")
    return {**info, "files": files}


def pr_material(n: int, info: dict) -> str:
    files = "\n".join(f"- {f['path']} (+{f.get('additions', 0)} -{f.get('deletions', 0)})" for f in info.get("files", []))
    return f"## Pull Request #{n}: {info.get('title', '')}\n\n{info.get('body') or ''}\n\n### 変更したファイル\n{files}"


def design_docs(info: dict) -> list[str]:
    """PR が足したか直した `issues/` の設計文書のパス（消しただけのものは読めないので除く）。"""
    return [
        f["path"]
        for f in info.get("files", [])
        if f.get("path", "").startswith("issues/")
        and f["path"].endswith(".md")
        and "design" in Path(f["path"]).name
        and not (f.get("additions", 0) == 0 and f.get("deletions", 0) > 0)
    ]


def design_material(n: int, path: str, info: dict, repo: str | None, cwd: Path) -> str:
    """PR の先頭のコミットの設計文書の中身を材料の 1 件にする。取れなければ Back。"""
    ref = info.get("headRefOid") or ""
    if not ref:
        raise Back(f"PR #{n} の先頭のコミットが分からず、設計文書 {path} を読めない")
    endpoint = f"repos/{repo or '{owner}/{repo}'}/contents/{urllib.parse.quote(path)}?ref={ref}"
    try:
        text = gh_or_back(["api", "-H", "Accept: application/vnd.github.raw", endpoint], None, cwd)
    except Back as e:
        raise Back(f"PR #{n} の設計文書 {path} を読めない: {e}")
    return f"## 設計文書 {path}（PR #{n}）\n\n{text}"


def build_prompt(project: pm.ProjectMvv, sprint: str | None, gate: str, materials: list[str]) -> str:
    """MVV の節（共通原則 → プロジェクト MVV → スプリント MVV → 判断の決まり）→ 関門 → 材料。"""
    body = "\n\n".join(m[:MAX_MATERIAL] for m in materials)
    return f"{pm.block(project, sprint)}\n# 関門\n\n{GATES[gate]}\n\n# 材料\n\n{body}\n"


def well_formed(verdict) -> bool:
    """応答が契約の形か。verdict・reasons・boundary の 3 つが揃い、reasons と boundary が文字列の配列であること。

    **契約の外は関門を省かない（fail closed）。** boundary を欠く・空文字・空 object を `[]` と同じに読むと、
    越えない線の申告が無いまま関門が省かれる。"""

    def strings(v) -> bool:
        return isinstance(v, list) and all(isinstance(x, str) for x in v)

    return (
        isinstance(verdict, dict)
        and verdict.get("verdict") in VERDICTS
        and strings(verdict.get("reasons"))
        and strings(verdict.get("boundary"))
    )


def ask(prompt: str) -> tuple[dict | None, str, dict]:
    """(判定, 生の文, 使用量) を返す。読めなければ判定は None。

    呼び出しごとに使用量の帳簿へ 1 行を足す（#1142 の不足 f。source は mvv-gate、プランの外の呼び出し）。
    """
    import usage_ledger  # lib/ は起動の時に sys.path へ足してある
    from supervise_lib.claude import minimal_args

    base = shlex.split(os.environ.get("NDF_MVV_CLAUDE", "claude"))
    cmd = base + minimal_args(SYSTEM) + ["--tools", ""]
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)[-500:], {}
    try:
        outer = json.loads(p.stdout)
    except json.JSONDecodeError:
        outer = None
    if not isinstance(outer, dict):
        usage_ledger.append_safely(os.getcwd(), usage_ledger.UsageRecord(source="mvv-gate", kind="mvv"))
        return None, (p.stdout + p.stderr)[-500:], {}
    text = outer.get("result") or ""
    usage = {"cost_usd": outer.get("total_cost_usd"), "seconds": (outer.get("duration_ms") or 0) / 1000}
    usage_ledger.append_safely(
        os.getcwd(),
        usage_ledger.UsageRecord.from_claude(
            outer, source="mvv-gate", kind="mvv", seconds=usage["seconds"] if outer.get("duration_ms") is not None else None
        ),
    )
    verdict = pm.json_object(text)
    if not well_formed(verdict):
        return None, text[-500:], usage
    return verdict, text, usage


def sprint_text(state: dict) -> str | None:
    """スプリント MVV の本文。無ければ None（照合は `pm.approval_refusal` が済ませている）。"""
    path = (state.get("mvv") or {}).get("path")
    return Path(path).read_text(encoding="utf-8") if path else None


def decl_changes(infos: dict[int, dict]) -> list[str]:
    return [f"#{n} {f['path']}" for n, info in infos.items() for f in info.get("files", []) if f.get("path") in DECL_PATHS]


def revise_items(a, root: Path, project: pm.ProjectMvv) -> list[dict]:
    """「判定できない」の連続（I13）と改訂の兆候の閾値（I18）から、改訂の提案を返す。"""
    if not project.approved:
        return []
    key = pm.mvv_repo_key(root)
    rows = [r for r in pms.read_jsonl(Path(a.log).expanduser()) if r.get("repo") == key]
    streak = pms.unknown_streak(rows, project.sha256)
    limit = project.setting("unknown_streak")
    out = []
    if limit and streak >= limit:
        out.append(
            {
                "kind": "revise",
                "result": "suggest",
                "name": "MVV の改訂を提案する",
                "reason": f"版 {project.version} のもとで「判定できない」が {streak} 回続いた（閾値 {limit}）。"
                "`references/project-mvv.md` の改訂の手順へ入る",
            }
        )
    sig = pms.signals(root, project, gate_log=Path(a.log).expanduser())
    sig["over"] = [k for k in sig["over"] if k != "unknown_streak"]
    sug = pms.revise_suggestion(sig)
    return out + ([sug] if sug else [])


def boundary_hits(root: Path, infos: dict[int, dict], advise: bool = False) -> list[str]:
    """越えない線のパスに当たる変更。`advise` では進め方の宣言が無いことを線が無いとして扱う（#1400 の決定 7）。"""
    if advise and not (root / ".ndf" / DECL_NAME).exists():
        return []
    try:
        patterns = read_pace(root)["boundary_paths"]
    except PaceError as e:
        raise Back(str(e))
    hits = []
    for n, info in infos.items():
        for f in info.get("files", []):
            old = f.get("previous_path")
            if f.get("status") == "renamed" and not old:
                hits.append(f"#{n} {f['path']}（rename の前のパスが分からない）")
            elif matches(f.get("path", ""), patterns) or (old and matches(old, patterns)):
                hits.append(f"#{n} {old} → {f['path']}" if old else f"#{n} {f['path']}")
    return hits


def write_log(path: str, record: dict) -> None:
    log = Path(path).expanduser()
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_gate(a, record: dict) -> str | None:
    """関門の記録をスプリントの状態へ書く。書けなければ理由を返す。"""
    what = "MVV 判定: " + " / ".join([*(f"#{n}" for n in a.pr), *a.material]) if (a.pr or a.material) else "MVV 判定"
    cmd = [
        sys.executable,
        str(HERE / "sprint-state.py"),
        "gate",
        a.sprint,
        GATE_NAMES[a.gate],
        "--what",
        what,
        "--by",
        "mvv",
        "--verdict",
        record["verdict"],
        "--reasons",
        json.dumps(record["reasons"], ensure_ascii=False),
        "--log",
        str(Path(a.log).expanduser()),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return None if p.returncode == 0 else (p.stdout + p.stderr).strip()[-300:] or f"終了コード {p.returncode}"


def write_note(path: str, a, record: dict) -> None:
    reasons = "\n".join(f"- {r}" for r in record["reasons"]) or "- （無し）"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if a.advise:
        Path(path).write_text(advise_note(a, record, reasons), encoding="utf-8")
        return
    Path(path).write_text(
        f"## MVV 判定（{GATES[a.gate]}）\n\n"
        f"- 判定: {record['verdict']}（関門を省いた。利用者が承認した MVV を事前の許可として扱う）\n"
        f"- 根拠: {' / '.join(record.get('basis') or [])}（プロジェクト MVV: {record['project_mvv']['status']}"
        f"{'・版 ' + str(record['project_mvv']['version']) if record['project_mvv'].get('version') else ''}）\n"
        f"- 時刻: {record['at']}\n- ログ: `{Path(a.log).expanduser()}` の {record['at']} の行\n\n"
        f"### 理由\n\n{reasons}\n",
        encoding="utf-8",
    )


def advise_note(a, record: dict, reasons: str) -> str:
    """助言の MVV 判定の記録（承認資料に載せる）。判定・根拠・時刻・ログ・理由。"""
    project = pm.from_record(record["project_mvv"])
    sha = (record.get("sprint_mvv") or {}).get("sha256")
    verdict = record["verdict"]
    return (
        f"## MVV 判定（{GATES[a.gate]}・助言）\n\n"
        f"- 判定: {pm.VERDICT_LABEL.get(verdict, verdict)}（{verdict}。助言であり、承認は利用者が行う）\n"
        f"- {pm.basis_phrase(record.get('basis'), project, sha)}\n"
        f"- 時刻: {record['at']}\n- ログ: `{Path(a.log).expanduser()}` の {record['at']} の行\n\n"
        f"### 理由\n\n{reasons}\n"
    )


def advise_none(a, project: pm.ProjectMvv) -> tuple[dict, int | None]:
    """助言の MVV 判定で、プロジェクト MVV が承認済みでないとき（#1400 の I3）。LLM も行も記録も出さない。"""
    item = {"verdict": "none", "status": project.status}
    return result(TOOL, "ok", f"{GATES[a.gate]} の MVV 判定（助言）: {pm.NO_MVV}（{pm.STATUS_LABEL[project.status]}）", [item]), None


def machine_checks(a, root: Path, project: pm.ProjectMvv, record: dict) -> tuple[str | None, list[str]]:
    """機械のチェック（状態・承認・モード・宣言・越えない線・材料）。スプリント MVV の本文と材料を返し、外れは `Back` を投げる。"""
    state = json.loads(Path(a.sprint).read_text(encoding="utf-8"))
    if project.status in ("unapproved", "mismatch", "unreadable"):
        raise Back(f"プロジェクト MVV が{pm.STATUS_LABEL[project.status]}（{project.error or ''}）")
    if why := pm.approval_refusal(state, root, project, advise=a.advise):
        raise Back(why)
    sprint = sprint_text(state)
    if sprint is not None:
        record["sprint_mvv"] = {"sha256": state["mvv"]["sha256"]}
    if a.mode in EXCLUDED_MODES:
        raise Back(f"モード {a.mode} は関門を省かない")
    infos = {n: pr_facts(n, a.repo, root) for n in a.pr}
    if changed := decl_changes(infos):
        raise Back("プロジェクト MVV の宣言を変える（共通原則の C7。MVV 判定で通さない）: " + " / ".join(changed))
    if hits := boundary_hits(root, infos, a.advise):
        raise Back("越えない線のパスに当たる: " + " / ".join(hits))
    materials = []
    for m in a.material:
        if not Path(m).is_file():
            raise Back(f"材料のファイルが無い: {m}")
        materials.append(f"## {m}\n\n{Path(m).read_text(encoding='utf-8')}")
    materials += [pr_material(n, info) for n, info in infos.items()]
    if a.gate == "design":
        for n, info in infos.items():
            for path in design_docs(info):
                materials.append(design_material(n, path, info, a.repo, root))
                record["material"].append(f"#{n} {path}")
    if not materials:
        raise Back("材料が無い（--material か --pr を渡す）")
    return sprint, materials


def new_record(a, root: Path, project) -> dict:
    """判定の記録の初期値。"""
    return {
        "at": clock.now_iso("utc"),
        "gate": a.gate,
        "sprint": a.sprint,
        "material": list(a.material),
        "pr": a.pr,
        "mode": a.mode or "",
        "repo": pm.mvv_repo_key(root),
        "project_mvv": pm.record(project),
        "pace": str(jsonio.read(a.sprint, missing={}, broken={}, want=dict).get("pace") or ""),  # 読めなければ判定の中で外れにする
        "sprint_mvv": None,
    }


def passed(a, root: Path, project, record: dict, usage: dict) -> tuple[dict, int | None]:
    """MVV に従うときの関門の記録と結果。記録を書けなければ関門へ落とす。"""
    record.update(verdict="follow", passed=True, **usage)
    write_log(a.log, record)
    err = record_gate(a, record)
    if err:
        record["passed"] = False
        return result(
            TOOL,
            "gate",
            f"{GATES[a.gate]}: MVV に従うが、関門の記録を書けない（{err}）。利用者の承認を求める",
            [record],
            usage,
            next="利用者の承認を求める",
        ), EXIT_GATE
    if a.note:
        write_note(a.note, a, record)
    return result(
        TOOL, "ok", f"{GATES[a.gate]}: MVV に従う。関門を省いて進めてよい", [record, *revise_items(a, root, project)], usage
    ), None


def cmd_check(a) -> tuple[dict, int | None]:
    root = Path(a.root or ".").resolve()
    project = pm.load_mvv(root)
    if a.advise and not project.approved:
        return advise_none(a, project)
    record = new_record(a, root, project)

    def advised(summary: str, usage: dict | None = None, extra: dict | None = None):
        """助言の MVV 判定の結果。行と記録を書き、判定によらず 0 で返す（#1400 の決定 2）。"""
        record["passed"] = False
        write_log(a.log, record)
        if a.note:
            write_note(a.note, a, record)
        suggest = revise_items(a, root, project) if record["verdict"] != "machine" else []
        label = pm.VERDICT_LABEL.get(record["verdict"], record["verdict"])
        return result(
            TOOL,
            "ok",
            f"{GATES[a.gate]} の MVV 判定（助言）: {label}（{summary}）。承認は利用者が行う",
            [{**record, **(extra or {})}, *suggest],
            usage or {},
            next="判定を承認資料に載せて利用者の承認を求める",
        ), None

    def back(why: str, verdict: str, extra: dict | None = None, usage: dict | None = None):
        # 機械のチェックの外れ（と助言の判定を読めないとき）は、外れた理由を理由の欄に載せる
        listed = verdict == "machine" or (a.advise and verdict == "unreadable")
        record.update(verdict=verdict, reasons=[why] if listed else record.get("reasons", []), passed=False, **(usage or {}))
        record.setdefault("basis", pm.basis(None, project))
        if a.advise:
            return advised(why, usage, extra)
        write_log(a.log, record)
        suggest = revise_items(a, root, project) if verdict != "machine" else []
        return result(
            TOOL,
            "gate",
            f"{GATES[a.gate]}: {why}。利用者の承認を求める",
            [{**record, **(extra or {})}, *suggest],
            usage or {},
            next="利用者の承認を求める",
        ), EXIT_GATE

    try:
        sprint, materials = machine_checks(a, root, project, record)
    except (OSError, ValueError) as e:
        return back(f"スプリントの状態を読めない: {e}", "machine")
    except Back as e:
        return back(str(e), "machine")

    verdict, raw, usage = ask(build_prompt(project, sprint, a.gate, materials))
    if verdict is None:
        return back("判定を読めない", "unreadable", {"raw": raw}, usage)
    boundary = verdict["boundary"]
    record.update(reasons=verdict["reasons"], boundary=boundary, basis=pm.basis(verdict.get("basis"), project, sprint=sprint))
    if a.advise:
        record.update(verdict=verdict["verdict"], **usage)
        return advised(pm.verdict_reason(verdict), usage)
    if verdict["verdict"] != "follow" or boundary:
        return back(pm.verdict_reason(verdict), verdict["verdict"], usage=usage)
    return passed(a, root, project, record, usage)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--sprint", required=True, help="スプリントの状態（sprint-state.py のファイル）")
    c.add_argument("--gate", required=True, choices=sorted(GATES))
    c.add_argument("--material", nargs="+", default=[])
    c.add_argument("--pr", nargs="+", type=int, default=[])
    c.add_argument("--mode", default="")
    c.add_argument("--log", default=str(pm.gate_log_path()))
    c.add_argument("--repo")
    c.add_argument("--root", help="進め方の宣言を読むリポジトリの根（既定はカレント）")
    c.add_argument("--note", help="従うとき（--advise ではプロジェクト MVV が承認済みのすべての判定）に、判定の記録を Markdown で書く所")
    c.add_argument(
        "--advise", action="store_true", help="助言の MVV 判定: 承認ゲートの記録を書かず、どの判定でも 0 で返す（pace: normal。#1400）"
    )
    a = ap.parse_args(legacy_names.rewrite_argv("mvv-gate.py", sys.argv[1:]))
    out, code = cmd_check(a)
    emit(out, code)


if __name__ == "__main__":
    main()
