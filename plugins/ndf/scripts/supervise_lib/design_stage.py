"""`supervise.py design-results`: 設計の結果を読み、実装のステージのプランを書き換える（#1485 の決定 1〜3）。

設計承認ゲートの直後のステージが打つ。設計の課題ごとに `design/issue-<番号>` のマージした PR を探し、その PR が
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
import sys
import time
from pathlib import Path
from typing import Callable

import design_results as dr
import gh_call
from design_docs import connected_docs, name_numbers
from step_result import result
from supervise_lib.decl import glossary_ready
from supervise_lib.paths import is_dir_entry
from supervise_lib.procedures import DESIGN_RESULTS, absorbed_condition, closed_condition, with_touched
from supervise_lib.templates import impl_prompt

TOOL = "supervise-design-results"
IMPL_STAGE = "実装"

# 一時的な失敗の打ち直し（#1941 の決定 9）: 間隔・1 コマンドの待ち・1 回の実行の待ちの合計は上限の宣言の方針
# supervise.design-stage-read（#2343）
POLICY = "supervise.design-stage-read"
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

    def __init__(self, total: float | None = None):
        import limits

        self.left = limits.seconds(POLICY) if total is None else total


def _retrying(run: Callable[[], object], command: str, budget: Budget):
    """`run()`（returncode を持つ結果）を、0 以外の間、方針 `POLICY` の間隔で打ち直す。待ちは 1 コマンドで
    方針の `per_call` 秒・残りの `budget` まで。打ち直しても 0 以外なら ReadFailed。"""
    import limits
    import waits

    def sleep(seconds: float) -> None:
        budget.left -= seconds
        SLEEP(seconds)

    policy = limits.policy(POLICY)
    got = waits.retry_call(
        run,
        lambda p: p.returncode != 0,
        policy=policy._replace(seconds=max(0.0, min(policy.per_call, budget.left))),
        reason=lambda p: p.stderr or p.stdout,
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


def changed_design_docs(root: str, base: str, pr: int, budget: Budget | None = None) -> tuple[list[tuple[str, str]], list[str]]:
    """PR が変えた `issues/*.md` の、`origin/<base>` でのパスと中身と、`origin/<base>` の `issues/` の直下の設計文書の
    一覧（#2453 の決定 1）。起点から消えたパスは読み飛ばす（決定 11）。打ち直しても `gh` / `git` が 0 以外なら ReadFailed。"""
    budget = budget or Budget()
    p = _gh_retrying(root, ["pr", "view", str(pr), "--json", "files", "--jq", ".files[].path"], budget)
    paths = [f for f in p.stdout.split() if re.fullmatch(r"issues/[^/]+\.md", f)]
    _git_retrying(root, ["fetch", "-q", "origin", base], budget)
    # -z: core.quotePath で引用された名前は gh の実パスと一致しないため、NUL 区切りで実際の名前を読む
    listed = _git_retrying(root, ["ls-tree", "-r", "-z", "--name-only", f"origin/{base}", "--", "issues/"], budget).stdout
    present = {f for f in listed.split("\0") if f}
    base_docs = sorted(f for f in present if re.fullmatch(r"issues/[^/]+", f) and name_numbers(f) is not None)
    return [(f, show_base(root, base, f, budget)) for f in paths if f in present], base_docs


def show_base(root: str, base: str, path: str, budget: Budget) -> str:
    return _git_retrying(root, ["show", f"origin/{base}:{path}"], budget).stdout


def _parsed(docs: list[tuple[str, str]], problems: list[dr.CellProblem]) -> list[dr.Row] | None:
    """文書ごとに表を読み、行を合わせる。読める表が 1 本も無ければ None。セルの形の誤りは `problems` へ集める
    （誤りのある文書も表を持つ文書として数える。#2453 の決定 4）。"""
    found: list[list[dr.Row]] = []
    for f, t in docs:
        try:
            rows = dr.parse_results(t, f)
        except dr.DesignCellError as e:
            problems.extend(e.problems)
            found.append([])
            continue
        if rows is not None:
            found.append(rows)
    return [row for rs in found for row in rs] if found else None


def _rows_for_issue(root: str, base: str, n: int, budget: Budget, res: dr.DesignResults, problems: list[dr.CellProblem]):
    """課題 n の設計 PR と表の行を返す（PR が無ければ `(None, None)`）。PR が変えた文書に表が無ければ、
    課題と番号でつながるベースブランチの設計文書から表を探す（#2453 の決定 8）。`res.base_docs` を更新する。"""
    pr = merged_design_pr(root, base, n, budget)
    if pr is None:
        return None, None
    changed, base_docs = changed_design_docs(root, base, pr, budget)
    res.base_docs = base_docs
    rows = _parsed(changed, problems)
    if rows is None:
        seen = {f for f, _ in changed}
        linked = [f for f in connected_docs(base_docs, [n]) if f not in seen]
        rows = _parsed([(f, show_base(root, base, f, budget)) for f in linked], problems)
    return pr, rows


def read_results(root: str, base: str, design: list[int], budget: Budget | None = None) -> dr.DesignResults:
    """設計の課題ごとに設計の結果を読んで合わせる。PR が無い・表が無い課題は `unread` へ入れる。
    PR が変えた文書に表が無ければ、課題と番号でつながるベースブランチの設計文書から表を探す（#2453 の決定 8）。
    一時的な失敗が打ち直しの後も残れば、その課題で止めて ReadFailed（読んでいない課題を `unread` に持つ。決定 10）。
    触るファイルのセルの形の誤りは、すべての課題と文書から集めてから DesignCellError（#2453 の決定 4）。"""
    budget = budget or Budget()
    res = dr.DesignResults()
    problems: list[dr.CellProblem] = []
    for i, n in enumerate(design):
        try:
            pr, rows = _rows_for_issue(root, base, n, budget, res, problems)
        except ReadFailed as e:
            raise ReadFailed(e.command, e.tail, n, list(design[i + 1 :])) from e
        if pr is not None:
            res.design_prs.append(pr)
        if rows is None:
            res.unread.append(n)
        elif not problems:
            res.add(rows)
    if problems:
        raise dr.DesignCellError(problems)
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


def _implementing_plans(plans: dict[int, dict]) -> dict[int, int]:
    """課題 → その課題を実装するプランのキー。課題のまとまりのプラン（#3081）は代表の課題をキーに、まとまりの課題を
    `課題` に持つ。自分のプランを持つ課題（--absorb で取り込んだ課題も）はそのプランを指す。"""
    own = {n: n for n in plans}
    for n, plan in plans.items():
        for i in plan.get("課題") or []:
            own.setdefault(i, n)
    return own


def _checked_absorptions(plans: dict[int, dict], res: dr.DesignResults) -> tuple[dict[int, int], dict[int, list[int]]]:
    """取り込みの対応を作り、すべての取り込み先を確かめる。返すのは書き出しの時点の取り込み（課題 → 取り込み先）と、
    取り込み先の課題を実装するプランごとの取り込んだ課題。取り込み先に流れる実装プランが無ければ DesignResultsError。
    プランは変えない。"""
    absorbed: dict[int, list[int]] = {}
    own = _implementing_plans(plans)
    # new sprint --absorb で書き出しの時点に取り込んだ課題（プランの `取り込み先`）も表の取り込みと合わせる（#1426 の決定 8）
    pre = {n: p["取り込み先"] for n, p in plans.items() if isinstance(p.get("取り込み先"), int)}
    # --absorb の取り込みも表の取り込みも、取り込み先に実装が流れることを確かめる（取り込み先が閉じる・別へ取り込まれると、
    # 取り込んだ受け入れ内容を実装するプランが無くなる）
    for n, h in [*pre.items(), *((r.issue, r.host) for r in res.rows if r.kind == dr.ABSORB)]:
        host_row = res.row_of(h)
        if h not in own or (host_row is not None and host_row.kind != dr.IMPLEMENT):
            raise dr.DesignResultsError(
                f"#{n} の取り込み先 #{h} に流れる実装プランが無い（スプリントの課題でないか、設計の結果で取り込む・閉じるとした）"
            )
        absorbed.setdefault(own[h], []).append(n)
    return pre, absorbed


def _rebuild_impl_prompts(plan: dict, before: dict, after: dict, glossary: bool) -> None:
    """プランの実装のステップの指示を `after` の状態の雛形で組み直し、補正は順のまま残す（`kept_lines`）。"""
    for step in plan.get("steps", []):
        if step.get("id") == "impl":
            kept = kept_lines(str(step.get("prompt") or ""), before, after)
            ns = argparse.Namespace(
                issue=after["課題"],
                prompt=None,
                prompt_file=None,
                files=after["触るファイル"],
                design_docs=after["設計文書"],
                glossary_ready=glossary,
                kept=kept,
            )
            step["prompt"] = impl_prompt(ns)


def rewrite(plans: dict[int, dict], res: dr.DesignResults, glossary: bool = False) -> list[dict]:
    """プランへ設計の結果を書く。取り込み先に流れる実装プランが無ければ DesignResultsError（書き換える前に止まる）。
    「閉じる」と「取り込む」の課題のプランは `実行の条件` で飛ばす。実装の指示は雛形の部分（課題・設計文書・触る範囲・
    共通の規則）だけを組み直し、conductor の補正は順のまま残す（`kept_lines`）。書いた内容の一覧を返す。"""
    pre, absorbed = _checked_absorptions(plans, res)
    items = []
    for n, plan in plans.items():
        # 課題のまとまりのプラン（#3081）は、まとまりの課題のどれかが実装されれば流れる。閉じる・取り込むとした課題は外す。
        # --absorb で `課題` から外したまとまりの代表は戻さない（`課題` が無い古いプランは自分の番号だけを持つ）
        members = list(dict.fromkeys(plan.get("課題") or [n]))
        live = [i for i in members if not _not_implemented_by_own_plan(i, res, pre)]
        if not live:
            # 飛ばす理由はプランのキーの課題から取り、--absorb で `課題` から外したまとまりの代表ならまとまりの課題から取る
            i = next(i for i in dict.fromkeys([n, *members]) if _not_implemented_by_own_plan(i, res, pre))
            row, host = res.row_of(i), res.host_of(i)
            if row is not None and row.kind == dr.CLOSE:
                plan["実行の条件"] = closed_condition(i)
                items.append({"issue": i, "closed": True})
            else:
                plan["実行の条件"] = absorbed_condition(i, host if host is not None else pre[i])
                items.append({"issue": i, "absorbed_into": host if host is not None else pre[i]})
            continue
        before = {k: plan.get(k) for k in ("課題", "触るファイル", "設計文書")}
        issues = list(dict.fromkeys([*live, *absorbed.get(n, [])]))
        # 触るファイルはプランが実装するすべての課題の設計の結果から集める（並列の queue の重なりの判定に使う）
        with_touched(plan, list(dict.fromkeys(f for i in issues for f in res.files_of(i))))
        plan["課題"] = issues
        docs = res.docs_of(issues)
        if docs:
            plan["設計文書"] = docs
        after = {k: plan.get(k) for k in ("課題", "触るファイル", "設計文書")}
        _rebuild_impl_prompts(plan, before, after, glossary)
        items.append({"issue": n, "issues": issues, "files": plan.get("触るファイル", []), "docs": plan.get("設計文書", [])})
    return items


def _not_implemented_by_own_plan(i: int, res: dr.DesignResults, pre: dict[int, int]) -> bool:
    """課題 `i` を自分のプランで実装しない（設計の結果で閉じる・取り込むとした、か --absorb で取り込んだ）。"""
    row = res.row_of(i)
    return (row is not None and row.kind == dr.CLOSE) or res.host_of(i) is not None or i in pre


def marked_dirs(res: dr.DesignResults, root: str) -> dict[int, list[str]]:
    """「実装する」の行の触るファイルのディレクトリ（末尾 `/` か `root` の下の既存のディレクトリ）を末尾 `/` の形に揃える。

    重なりの判定はファイルシステムを読まずに末尾 `/` でディレクトリの宣言を見分ける（#1811 の決定 2）。
    揃えた行を課題ごとに返し、標準エラーへ 1 行ずつ知らせる。止まらない。"""
    found: dict[int, list[str]] = {}
    for row in res.rows:
        if row.kind != dr.IMPLEMENT:
            continue
        dirs = [f if f.endswith("/") else f + "/" for f in row.files if is_dir_entry(f, root)]
        if not dirs:
            continue
        row.files = [f + "/" if not f.endswith("/") and is_dir_entry(f, root) else f for f in row.files]
        found[row.issue] = dirs
        print(
            f"{TOOL}: #{row.issue} の触るファイルにディレクトリ {' '.join(dirs)}"
            "（重なりの判定に使わない。ファイル単位で書くと同じファイルを触るプランと直列になる）",
            file=sys.stderr,
            flush=True,
        )
    return found


def cmd_design_results(a, read=read_results) -> tuple[dict, int | None]:
    manifest = Path(a.manifest)
    try:
        paths = impl_plans(manifest)
        plans = {n: json.loads(p.read_text(encoding="utf-8")) for n, p in paths.items()}
    except (OSError, ValueError) as e:
        return result(TOOL, "stopped", f"manifest か実装のプランを読めない: {e}"), 2
    try:
        res = read(a.root, a.base, list(a.design))
        dirs = marked_dirs(res, a.root)
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
    return result(TOOL, "ok", summary, items, {"rows": len(res.rows), "unread": res.unread, "design_prs": res.design_prs, "dirs": dirs}), 0
