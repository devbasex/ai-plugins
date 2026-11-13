"""開発ループが打つコマンドの組み立て: スプリントの状態の作成と、ループの 1 回の工程の進行表（`progress.py new` と `next`）。"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import jsonio
from sprint_loop_record import PROGRESS, SPRINT_STATE, resume_command, selected_issues


def approved_issues(rec: dict) -> set[int]:
    return {w["number"] for w in rec.get("waiting", []) if w.get("answer") == "approved"}


def design_issues(rec: dict, issues) -> list[int]:
    """設計のステップ（設計承認ゲート）を置く課題: 設計を承認していない課題すべて（#2913 の決定 1）。選定の「新しい承認を生む作業」の
    分類と `new_sprint_argv` の `--design` が同じこれを読む（I2）。"""
    approved = approved_issues(rec)
    return [n for n in issues if n not in approved]


HOLD_ONLY_REFUSAL = "答え待ちの停止の範囲で始めたループの 1 回に設計のステップを置かない"


def sprint_refusal(rec: dict, lap: dict) -> tuple[int, str] | None:
    """`loop sprint` が組まずに断る理由 (終了コード, 1 行)。組んでよければ None。答え待ちの停止の範囲で始めたループの 1 回
    （`hold_only`）は、設計を承認していない課題を含めば断る（#2913 の I1。呼び出しの誤りとして 2）。"""
    if lap.get("issues_kept") is not None:
        return 1, "設計承認ゲートの後のループの 1 回は組み直さない（loop start で残した課題の続きを組む）"
    if lap.get("hold_only") and (left := design_issues(rec, selected_issues(lap))):
        return 2, f"{HOLD_ONLY_REFUSAL}（設計を承認していない課題: {' '.join(f'#{n}' for n in left)}）"
    return None


def sprint_state_init_argv(root: str, rec: dict, lap: dict, issues: list[int]) -> list[str]:
    """sprint-state.py init のコマンド。ループの 1 回のディレクトリに mvv.md があればスプリント MVV を渡す。"""
    init = [sys.executable, str(SPRINT_STATE), "init", lap["state_path"], "--name", lap["sprint"], "--pace", "auto", "--root", root]
    init += [x for n in issues for x in ("--issue", str(n))]
    if lap["versions"]["dev"]:
        init += ["--dev", lap["versions"]["dev"], "--prod", lap["versions"]["prod"]]
    init += ["--goal", resume_command(rec)]
    mvv = Path(lap["dir"]) / "mvv.md"
    if mvv.is_file():
        init += ["--mvv", str(mvv), "--milestone", lap["milestone"]]
    return init


DESIGN_STAGES = ("設計", "詳細設計", "ドキュメント再構成", "ドキュメントレビュー")  # 設計承認ゲートまでの工程


def lap_requirements(lap: dict, issues: list[int]) -> Path:
    """要求と受け入れ条件の工程の出力（`stage-io` の `requirements-out`）。開発ループの選定が受け入れ条件を確かめた課題を選ぶため、
    進行表を作る前に済んだ工程として渡す。"""
    refs = " ".join(f"#{n}" for n in issues)
    doc = {"activities": {}, "summary": f"開発ループ の選定（ループの {lap['n']} 回目）で受け入れ条件を確かめた課題: {refs}"}
    jsonio.write_atomic(Path(lap["dir"]) / "requirements.json", doc, indent=1)
    return Path(lap["dir"]) / "requirements.json"


def lap_carry(rec: dict, lap: dict) -> list[int]:
    """持ち越しの課題: 差分の棚卸しを最後に走らせたループの 1 回から渡す（走らせなかった回の古い値を写さない。#2914 の I6）。"""
    prev = next((x for x in reversed(rec["laps"][: max(lap["n"] - 1, 0)]) if not (x.get("refine") or {}).get("skipped")), None)
    return list(((prev or {}).get("refine") or {}).get("deferred") or [])


def lap_context(rec: dict, lap: dict, carry: list[int]) -> Path:
    """まとめの工程が読む開発ループの文脈（ループの名前・回・マイルストーン・持ち越し・版）。今の `new close --pace auto` の
    `--loop`・`--lap`・`--milestone`・`--carry` と、`new sprint` の `--version` にあたる。"""
    doc = {"loop": rec["name"], "lap": lap["n"], "milestone": lap.get("milestone"), "carry": list(carry), "versions": lap["versions"]}
    doc["extra"] = list(lap.get("sprint_extra") or [])
    if lap.get("lane"):  # レーンで動くループ: デプロイの工程がリリースの依頼を送る先
        doc["lane"] = lap["lane"]  # `loop sprint ... --` の後の引数（工程のプランへ渡す）
    jsonio.write_atomic(Path(lap["dir"]) / "context.json", doc, indent=1)
    return Path(lap["dir"]) / "context.json"


def new_sprint_argv(root: str, rec: dict, lap: dict, notes: list[str], carry: list[int] = (), issues: list[int] | None = None) -> list[str]:
    """`progress.py new --pace auto` のコマンド（#3092）。ループの 1 回の進行表を作り、要求と受け入れ条件の工程を済んだ工程として渡す。
    設計を承認済みの課題は、設計の工程を通さない（`--disable 設計=…`）。`notes` は出し直しの指摘（`--revise-note`）。
    版（`--dev` を渡したときだけ）と `loop sprint ... --` の後の引数は文脈（`--context`）で工程のプランへ渡す（#2164）。"""
    issues = list(issues) if issues is not None else selected_issues(lap)  # 作り直し（モードを下げた続き）は残った課題で
    new = [sys.executable, str(PROGRESS), "new", lap["sprint"], "--mode", lap["mode"], "--issues", *map(str, issues), "--pace", "auto"]
    new += ["--state", lap["state_path"], "--work", str(Path(lap["dir"]) / "work")]
    new += ["--requirements", str(lap_requirements(lap, issues)), "--context", str(lap_context(rec, lap, list(carry)))]
    if lap.get("judgments_path"):
        new += ["--judgment", lap["judgments_path"]]
    if lap.get("designed") or not design_issues(rec, issues):  # 設計を通した（作り直し）か承認済み: 設計の工程を流さない（今の --designed）
        new += [x for st in DESIGN_STAGES for x in ("--disable", f"{st}=設計承認ゲートで承認済み")]
    if rec.get("outside_plan"):  # 例外として始めたループは、内側の起動の判定でも同じ理由で通す（#2733）
        new += ["--outside-plan", rec["outside_plan"]]
    new += list(notes)
    return new


def table_of(stdout: str) -> str | None:
    """`progress.py new` の出力（統一 JSON レポート）から進行表の ID を読む。"""
    for line in reversed((stdout or "").strip().splitlines()):
        try:
            got = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(got, dict):
            return (got.get("metrics") or {}).get("table")
    return None


def next_pipeline(rec: dict, table: str, done: Path) -> str:
    """ループの 1 回を流す 1 行: 進行表を次の工程から流し（答え待ちでも抜ける）、出力を done へ置いてから loop lap で締める。"""
    q = shlex.quote
    part = done.with_suffix(".part")
    return (
        f"python3 {q(str(PROGRESS))} next {q(table)} > {q(str(part))}; mv {q(str(part))} {q(str(done))}; "
        f"python3 {q(str(SPRINT_STATE))} loop lap {rec['name']} --done {q(str(done))}"
    )


def sprint_guidance(rec: dict, chosen: list[int], lap_dir: str) -> str:
    """選定の後の案内: 材料を集めて判定表の行を選び、ループの 1 回のディレクトリの judgments.json（判定の根拠のファイル）を
    渡して loop sprint を打つ（#2923）。"""
    judgments = Path(lap_dir) / "judgments.json"
    q = shlex.quote
    materials = SPRINT_STATE.parent / "mode-materials.py"
    return (
        f"python3 {q(str(materials))} --issue {' '.join(map(str, chosen))} --out {q(str(judgments))} で材料を集め、"
        "課題ごとに判定表の行（row・mode・condition・files）を埋め、対象外は loop exclude で外し、"
        f"python3 {q(str(SPRINT_STATE))} loop sprint {rec['name']} --judgments {q(str(judgments))} を打つ"
    )
