"""`supervise.py design-results`: 設計の結果を読み、実装のステージのプランを書き換える（#1485 の決定 1〜3）。

承認ゲート 1 の直後のステージが打つ。設計の課題ごとに `design/issue-<番号>` のマージした PR を探し、その PR が
変えた `issues/*.md` を `origin/<ベースブランチ>` から読んで「設計の結果」の表を集める。実装する課題のプランへ
`触るファイル` と `設計文書` を、取り込んだ課題と閉じる課題のプランへ `実行の条件` を書き、取り込み先のプランの `課題` と
実装の指示文へ取り込んだ課題を足す。指示文は雛形の部分だけを組み直し、conductor の補正は残す（#1651）。
同じ入力に対しては同じ内容を書く（打ち直しても `課題` も補正も重ねない）。`gh` / `git` の一時的な失敗は打ち直し、
それでも読めなければ何も書かずに止まる（#1941）。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

import design_results as dr
import gh_call
from step_result import result
from supervise_lib.decl import glossary_ready
from supervise_lib.procedures import DESIGN_RESULTS, absorbed_condition, closed_condition, with_touched
from supervise_lib.templates import impl_prompt

TOOL = "supervise-design-results"
IMPL_STAGE = "実装"

# 一時的な失敗の打ち直し（#1941 の決定 9）: 15 秒おき・1 コマンドの待ちは 30 秒まで（3 回打つ）・1 回の実行の待ちの合計は 120 秒まで
RETRY_INTERVAL = 15.0
RETRY_PER_CALL = 30.0
RETRY_TOTAL = 120.0
TAIL = 300  # 要約へ出す出力の末尾の文字数
SLEEP: Callable[[float], None] = time.sleep  # テストが差し替える


class ReadFailed(Exception):
    """打ち直しても `gh` / `git` が 0 以外で終わった（一時的な失敗が残った）。`issue` はそのとき読んでいた課題、
    `unread` はまだ読んでいない課題。"""

    def __init__(self, command: str, tail: str, issue: int | None = None, unread: list[int] | None = None):
        super().__init__(command)
        self.command, self.tail, self.issue, self.unread = command, tail, issue, list(unread or [])


class Budget:
    """1 回の実行の打ち直しの待ちの残り（秒）。"""

    def __init__(self, total: float = RETRY_TOTAL):
        self.left = total


def _retrying(run: Callable[[], object], command: str, budget: Budget):
    """`run()`（returncode を持つ結果）を、0 以外の間 `RETRY_INTERVAL` 秒おきに打ち直す。待ちは 1 コマンドで
    `RETRY_PER_CALL` 秒・残りの `budget` まで。打ち直しても 0 以外なら ReadFailed。"""
    import waits

    def sleep(seconds: float) -> None:
        budget.left -= seconds
        SLEEP(seconds)

    got = waits.retry_call(
        run,
        lambda p: p.returncode != 0,
        max_wait=max(0.0, min(RETRY_PER_CALL, budget.left)),
        interval=RETRY_INTERVAL,
        sleep=sleep,
    )
    p = got.value
    if p is None or p.returncode != 0:
        tail = ((p.stderr or p.stdout) if p is not None else str(got.error or "")).strip()[-TAIL:]
        raise ReadFailed(command, tail)
    return p


def _gh_retrying(root: str, args: list[str], budget: Budget):
    return _retrying(lambda: gh_call.gh(args, cwd=root), "gh " + " ".join(args), budget)


def _git_retrying(root: str, args: list[str], budget: Budget):
    cmd = ["git", "-C", root, *args]
    return _retrying(lambda: subprocess.run(cmd, capture_output=True, text=True), " ".join(cmd), budget)


def merged_design_pr(root: str, base: str, n: int, budget: Budget | None = None) -> int | None:
    """`design/issue-<n>` から base へマージした PR の番号。`gh` が 0 で空なら None（PR が無い）。
    打ち直しても `gh` が 0 以外なら ReadFailed。"""
    args = ["pr", "list", "--head", f"design/issue-{n}", "--base", base, "--state", "merged", "--json", "number", "--jq", ".[0].number"]
    text = _gh_retrying(root, args, budget or Budget()).stdout.strip()
    return int(text) if text.isdigit() else None


def changed_design_docs(root: str, base: str, pr: int, budget: Budget | None = None) -> list[tuple[str, str]]:
    """PR が変えた `issues/*.md` の、`origin/<base>` でのパスと中身。起点から消えたパスは読み飛ばす（決定 11）。
    打ち直しても `gh` / `git` が 0 以外なら ReadFailed。"""
    budget = budget or Budget()
    p = _gh_retrying(root, ["pr", "view", str(pr), "--json", "files", "--jq", ".files[].path"], budget)
    paths = [f for f in p.stdout.split() if re.fullmatch(r"issues/[^/]+\.md", f)]
    if not paths:
        return []
    _git_retrying(root, ["fetch", "-q", "origin", base], budget)
    present = set(_git_retrying(root, ["ls-tree", "-r", "--name-only", f"origin/{base}", "--", "issues/"], budget).stdout.splitlines())
    return [(f, _git_retrying(root, ["show", f"origin/{base}:{f}"], budget).stdout) for f in paths if f in present]


def read_results(root: str, base: str, design: list[int], budget: Budget | None = None) -> dr.DesignResults:
    """設計の課題ごとに設計の結果を読んで合わせる。PR が無い・表が無い課題は `unread` へ入れる。
    一時的な失敗が打ち直しの後も残れば、その課題で止めて ReadFailed（読んでいない課題を `unread` に持つ。決定 10）。"""
    budget = budget or Budget()
    res = dr.DesignResults()
    for i, n in enumerate(design):
        try:
            pr = merged_design_pr(root, base, n, budget)
            rows = None
            if pr is not None:
                found = [r for r in (dr.parse_results(t, f) for f, t in changed_design_docs(root, base, pr, budget)) if r is not None]
                rows = [row for rs in found for row in rs] if found else None
        except ReadFailed as e:
            raise ReadFailed(e.command, e.tail, n, list(design[i + 1 :])) from e
        if pr is not None:
            res.design_prs.append(pr)
        if rows is None:
            res.unread.append(n)
        else:
            res.add(rows)
    return res


def impl_plans(manifest: Path) -> dict[int, Path]:
    """manifest の実装のステージのプラン（課題の番号 → パス）。ファイル名は `<番号>-impl-<課題>.json`。"""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    out = {}
    for entry in data.get("ステージ") or []:
        if entry.get("name") != IMPL_STAGE:
            continue
        for p in entry.get("plans") or []:
            m = re.search(r"-impl-(\d+)\.json$", p)
            if m:
                out[int(m.group(1))] = Path(p)
    return out


def _prompt_of(issues: list[int], files, docs, glossary: bool) -> str:
    """雛形が組む実装の指示（補正を除く）。"""
    ns = argparse.Namespace(issue=issues, prompt=None, prompt_file=None, files=files, design_docs=docs, glossary_ready=glossary)
    return impl_prompt(ns)


def kept_lines(prompt: str, before: dict, after: dict) -> list[str]:
    """今の `prompt` から補正の行（conductor が足した行）を順のまま取り出す（#1651 の決定 1）。

    書き直しの前の状態（`before`）と後の状態（`after`）から組んだ指示の、空でない行の和を雛形の行とし、
    そこに無い空でない行を補正とする。用語集の行はどちらの判定でも雛形の行とみなす。状態は `課題`・`触るファイル`・`設計文書`。"""
    template: set[str] = set()
    for st in (before, after):
        for glossary in (True, False):
            text = _prompt_of(st.get("課題") or [0], st.get("触るファイル"), st.get("設計文書"), glossary)
            template.update(line for line in text.splitlines() if line.strip())
    return [line for line in prompt.splitlines() if line.strip() and line not in template]


def rewrite(plans: dict[int, dict], res: dr.DesignResults, glossary: bool = False) -> list[dict]:
    """プランへ設計の結果を書く。取り込み先に流れる実装プランが無ければ DesignResultsError（書き換える前に止まる）。
    「閉じる」と「取り込む」の課題のプランは `実行の条件` で飛ばす。実装の指示は雛形の部分（課題・設計文書・触る範囲・
    共通の規則）だけを組み直し、conductor の補正は順のまま残す（`kept_lines`）。書いた内容の一覧を返す。"""
    absorbed: dict[int, list[int]] = {}
    for r in res.rows:
        if r.kind != dr.ABSORB:
            continue
        host_row = res.row_of(r.host)
        if r.host not in plans or (host_row is not None and host_row.kind != dr.IMPLEMENT):
            raise dr.DesignResultsError(
                f"#{r.issue} の取り込み先 #{r.host} に流れる実装プランが無い（スプリントの課題でないか、設計の結果で取り込む・閉じるとした）"
            )
        absorbed.setdefault(r.host, []).append(r.issue)
    items = []
    for n, plan in plans.items():
        row, host = res.row_of(n), res.host_of(n)
        if row is not None and row.kind == dr.CLOSE:
            plan["実行の条件"] = closed_condition(n)
            items.append({"issue": n, "closed": True})
            continue
        if host is not None:
            plan["実行の条件"] = absorbed_condition(n, host)
            items.append({"issue": n, "absorbed_into": host})
            continue
        before = {k: plan.get(k) for k in ("課題", "触るファイル", "設計文書")}
        with_touched(plan, res.files_of(n))
        issues = [n] + [i for i in absorbed.get(n, []) if i != n]
        plan["課題"] = issues
        docs = res.docs_of(issues)
        if docs:
            plan["設計文書"] = docs
        after = {k: plan.get(k) for k in ("課題", "触るファイル", "設計文書")}
        for step in plan.get("steps", []):
            if step.get("id") == "impl":
                kept = kept_lines(str(step.get("prompt") or ""), before, after)
                ns = argparse.Namespace(
                    issue=issues,
                    prompt=None,
                    prompt_file=None,
                    files=after["触るファイル"],
                    design_docs=after["設計文書"],
                    glossary_ready=glossary,
                    kept=kept,
                )
                step["prompt"] = impl_prompt(ns)
        items.append({"issue": n, "issues": issues, "files": plan.get("触るファイル", []), "docs": plan.get("設計文書", [])})
    return items


def cmd_design_results(a, read=read_results) -> tuple[dict, int | None]:
    manifest = Path(a.manifest)
    try:
        paths = impl_plans(manifest)
        plans = {n: json.loads(p.read_text(encoding="utf-8")) for n, p in paths.items()}
    except (OSError, ValueError) as e:
        return result(TOOL, "stopped", f"manifest か実装のプランを読めない: {e}"), 2
    try:
        res = read(a.root, a.base, list(a.design))
        items = rewrite(plans, res, glossary_ready([a.root])) if res.rows else []
    except ReadFailed as e:
        # 一時的な失敗が残った: プランも design-results.json も書かずに止まる（#1941 の I4）
        rest = "。読んでいない課題: " + " ".join(f"#{n}" for n in e.unread) if e.unread else ""
        summary = f"#{e.issue} の設計の結果を打ち直しても読めない（{e.command}: {e.tail}）{rest}。プランを書き換えずに止まる"
        return result(TOOL, "stopped", summary, [], {"failed_issue": e.issue, "not_read": e.unread}), 2
    except dr.DesignResultsError as e:
        return result(TOOL, "stopped", f"設計の結果を書けない: {e}。人へ戻す"), 2
    for n, plan in plans.items():
        paths[n].write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = manifest.parent / DESIGN_RESULTS
    out.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = f"設計の結果を {len(res.rows)} 行読み、実装のプランを {len(items)} 本書いた: {out}"
    if res.unread:
        summary += "。設計の結果を読めなかった設計の課題: " + " ".join(f"#{n}" for n in res.unread) + "（今の振る舞いで流す）"
    return result(TOOL, "ok", summary, items, {"rows": len(res.rows), "unread": res.unread, "design_prs": res.design_prs}), 0
