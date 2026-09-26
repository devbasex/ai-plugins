"""new impl / fix / check の雛形と、new の振り分け（#1142 の C1）。`engine` を import しない。"""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from step_result import result
from supervise_lib import release_templates
from supervise_lib.decl import with_decls
from supervise_lib.paths import CHECK_PY, MERGE_CMD, MERGE_PROBE, report_result, state_dir_of, with_paths


RULE_IMPL = ("範囲テストや全体テストが落ちたら（落ちたテストだけの再実行でも落ちた後）、変更に起因するなら fix、"
             "環境や変更に無関係なら次のステップ（範囲テストなら pr、全体テストなら doc-lint）。"
             "2 回直しても同じ失敗なら stop。")
RULE_CHECK = ("全体テストが落ちたら（落ちたテストだけの再実行でも落ちた後）、変更に起因するなら fix、"
              "変更に無関係なら ready。2 回直しても同じなら stop。")
FIX_PROMPT = "失敗した箇所を直してコミットする（push しない）。変更に起因しない失敗は直さない。"
IMPL_RULES = ("共通:\n"
              "- 文書には今の決まりだけを書く。.md の文言を照合するテストは書かない\n"
              "- コミットの件名と本文に課題を閉じる語（Closes / Fixes / Resolves）を書かない。"
              "区切りごとにコミットする（push しない）")


def plan_done(path: Path) -> bool:
    """計画が終わったか。状態ディレクトリの report.md の結果が完了なら終わり。"""
    rep = state_dir_of(str(path)) / "report.md"
    return rep.is_file() and report_result(rep.read_text()) == "完了"


def other_files(out: Path) -> list[tuple[str, list[str]]]:
    """同じディレクトリにある、まだ終わっていない他の計画の「触るファイル」を [(課題, パス)] で返す。"""
    found = []
    for f in sorted(out.parent.glob("*.json")):
        if f.resolve() == out.resolve() or plan_done(f):
            continue
        try:
            plan = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        files = plan.get("触るファイル") if isinstance(plan, dict) else None
        if isinstance(files, list) and files:
            issues = " ".join(f"#{i}" for i in plan.get("課題", []))
            found.append((issues or f.name, [str(x) for x in files]))
    return found


def impl_prompt(a, out: Path | None) -> str:
    """実装の指示文。--prompt が無ければ課題への参照を組み、共通の規則と他の計画の除外を足す。"""
    n = a.issue[0]
    if a.prompt_file:
        head = Path(a.prompt_file).read_text().rstrip()
    elif a.prompt:
        head = a.prompt.rstrip()
    else:
        if len(a.issue) == 1:
            head = f"課題 #{n} を実装する。本文は `gh issue view {n}` で読む（何をするか と 受け入れ条件）。"
        else:
            refs = "・".join(f"#{i}" for i in a.issue)
            head = f"課題 {refs} を実装する。本文はそれぞれ `gh issue view <番号>` で読む（何をするか と 受け入れ条件）。"
        if getattr(a, "files", None):
            head += "\n触る範囲: " + "、".join(a.files)
    parts = [head]
    others = other_files(out) if out else []
    if others:
        parts.append("並行して別の計画が次を触る。それらは変えない: "
                     + "、".join(f"{', '.join(files)}（{issues}）" for issues, files in others))
    parts.append(IMPL_RULES)
    return "\n".join(parts) + "\n"


def plan_impl(a, out: Path | None = None) -> dict:
    if out is None and hasattr(a, "out"):
        out = Path(a.out or f"plan-{a.issue[0]}.json")
    impl = {"id": "impl", "type": "work", "kind": "実装", "serena": True, "stage": "実装", "issues": True,
            "timeout": 3600, "prompt": impl_prompt(a, out)}
    return plan_to_merge(a, [impl])


def plan_fix(a) -> dict:
    """即時修正のプラン（不足 a）。impl の雛形から worker の実装のステップを除き、作業場所の今のコミットを
    範囲テスト → Pull Request → 全体テスト → doc-lint → ready → マージへ流す。"""
    return plan_to_merge(a, [])


def fix_worktree(branch: str) -> str:
    """new fix に --worktree が無いときの作業場所。今のディレクトリのリポジトリの .worktrees/<ブランチ>。"""
    p = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    repo = Path(p.stdout.strip()).parent if p.returncode == 0 and p.stdout.strip() else Path.cwd()
    return str(repo.resolve() / ".worktrees" / branch)


def plan_to_merge(a, head: list[dict]) -> dict:
    """head（impl なら worker の実装）の後に、同期とチェック → 範囲テスト → Pull Request → 全体テスト →
    doc-lint → ready → マージ（--escape-of なら逃げた不具合の記録）を続けた実装のプラン。"""
    tests = " ".join(a.tests)
    sync = bool(getattr(a, "sync_checks", None))
    if head:
        head[-1]["next"] = "sync" if sync else "test-limited"
    steps = list(head)
    if sync:  # 同期とチェックの宣言が無いプロジェクトではステップを置かない
        steps += [
            {"id": "sync", "type": "run", "preset": "sync-check", "stage": "実装", "on_fail": "fix-sync",
             "next": "test-limited"},
            {"id": "fix-sync", "type": "work", "kind": "修正", "inputs": ["sync"], "prompt": FIX_PROMPT,
             "next": "sync"},
        ]
    steps += [
        {"id": "test-limited", "type": "run", "stage": "完了判定", "timeout": 900, "rerun_failed": True,
         "cmd": with_paths(a.test_cmd, tests), "on_fail": "judge", "next": "pr"},
        {"id": "judge", "type": "judge", "inputs": ["test-limited", "test-all"],
         "question": "テストの失敗を直すか（fix）、範囲テストの失敗が変更に無関係なら PR へ（pr）、"
                     "全体テストの失敗が変更に無関係なら文書のチェックへ（doc-lint）、止めるか（stop）",
         "choices": ["fix", "pr", "doc-lint", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test-limited", "test-all"],
         "prompt": FIX_PROMPT, "next": "test-limited"},
        # Draft の PR を全体テストの前に出し、CI と手元の全体テストを並べる。直した後は pr のステップが push して本文を更新する
        {"id": "pr", "type": "pr", "stage": "Pull Request", "base": a.base, "title": a.title,
         "summary": a.summary or "", "changes": getattr(a, "changes", None) or "", "next": "test-all"},
        {"id": "test-all", "type": "run", "stage": "完了判定", "timeout": 1800, "rerun_failed": True,
         "cmd": with_paths(a.test_cmd, a.test_all), "on_fail": "judge", "next": "doc-lint"},
        {"id": "doc-lint", "type": "run", "preset": "doc-lint", "stage": "完了判定", "on_fail": "fix-doc",
         "next": "ready"},
        {"id": "fix-doc", "type": "work", "kind": "修正", "inputs": ["doc-lint"],
         "prompt": "ヒットした行を今の決まりだけを書く形へ直してコミットする（push しない）。", "next": "doc-lint"},
        {"id": "ready", "type": "run", "cmd": "sh -c 'git push -q && gh pr ready {pr}'", "next": "merge"},
        {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "probe": MERGE_PROBE, "next": "end"},
    ]
    if getattr(a, "escape_of", None) is not None:
        # その場で直した不具合を「逃げた不具合」として記録する（検査のトリガーの材料。#1078）
        steps[-1]["next"] = "escape"
        steps.append({"id": "escape", "type": "run", "cmd": f"{CHECK_PY} escape --pr {{pr}} --of {a.escape_of}",
                      "next": "end"})
    plan = {
        "フェーズ": "実装", "課題": a.issue, "モード": a.mode, "作業場所": a.worktree,
        "規則": RULE_IMPL, "上限": 20, "steps": steps,
    }
    with_decls(plan, a)
    if getattr(a, "files", None):
        plan["触るファイル"] = a.files
    if a.branch:
        plan["branch"] = a.branch
        plan["起点"] = f"origin/{a.base}"
    return plan


def plan_check(a) -> dict:
    pr = a.pr
    # 範囲の指定が無ければ、PR が変えたファイルのディレクトリ（根を除く）を範囲にする。ステップはシェルで動く
    scope = (" ".join(map(shlex.quote, a.scope)) if a.scope else
             f"$(gh pr diff {pr} --name-only | xargs -n1 dirname | sort -u | grep -vx '\\.')")
    # 駆動で回す（最終ゲートは全体のテスト）
    refactor = {"id": "refactor", "type": "drive", "drive": "cross-refactoring", "kind": "構造改善",
                "stage": "構造改善", "timeout": 3600,
                "args": f"{pr} --workflow-step --scope {scope} "
                        f"--baseline-test {shlex.quote(with_paths(a.test_cmd, a.test_all))}",
                "next": "review"}
    return with_decls({
        "フェーズ": "検査", "課題": a.issue or [], "モード": a.mode, "作業場所": a.worktree,
        "規則": RULE_CHECK, "上限": 12, "Pull Request": str(pr),
        "steps": [
            {"id": "assess", "type": "run", "preset": "assess", "stage": "構造改善", "skip_to": "review",
             "on_fail": "refactor", "next": "refactor"},
            refactor,
            {"id": "review", "type": "drive", "drive": "cross-review", "kind": "実装レビュー",
             "stage": "実装レビュー", "timeout": 3600, "args": f"{pr} --max-rounds 4", "next": "test-all"},
            {"id": "test-all", "type": "run", "stage": "完了判定", "timeout": 1800, "rerun_failed": True,
             "cmd": "git pull -q --rebase && " + with_paths(a.test_cmd, a.test_all), "on_fail": "judge",
             "next": "ready"},
            {"id": "judge", "type": "judge", "inputs": ["test-all"],
             "question": "全体テストの失敗を直す（fix）か、変更に無関係として進める（ready）か、止める（stop）か",
             "choices": ["fix", "ready", "stop"]},
            {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test-all"],
             "prompt": "失敗したテストを直してコミットし、git push する。", "next": "test-all"},
            {"id": "ready", "type": "run", "cmd": f"git push -q; gh pr ready {pr}", "next": "merge"},
            {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "probe": MERGE_PROBE, "next": "end"},
        ],
    }, a)


RULE_CHECK_SINCE = ("全体テストが落ちたら（落ちたテストだけの再実行でも落ちた後）、検査の修正に起因するなら fix、"
                    "修正に無関係なら finish。2 回直しても同じなら stop。")


def plan_check_since(a) -> dict:
    """前回の検査からの差分を範囲にする検査（pace: fast。#1078）。

    範囲は「前回の検査の時点（check-base/<名>）を宛先にした Pull Request」で表す。cross-refactoring と
    cross-review は Pull Request 1 本を入力に取るため、駆動を変えずに差分全体を見られる。検査の後に宛先を
    起点のブランチへ付け替えると、差分は検査の修正だけになる。実行の条件（check-trigger.py eval）が
    立ったときだけ流れ、作業ツリー（check/<名>）はその後に作る。落ちた run のステップは abort へ行き、
    失敗の記録・check-base の削除・検査の Pull Request を閉じる後始末をしてから止まる。

    `--review-only` は実装レビューだけを通す（構造改善のステップを持たない）。実行の条件は前回のレビューから
    PR が 1 本以上あること（`eval --review`）で、記録は構造改善のトリガーの起点にならない。"""
    repo = str(Path(a.worktree).resolve())
    review_only = getattr(a, "review_only", False)
    flag = " --review" if review_only else ""
    if getattr(a, "since_ref", None):
        flag += f" --since {shlex.quote(a.since_ref)}"
    name = a.id
    tests_all = shlex.quote(with_paths(a.test_cmd, a.test_all))
    state = "{state_dir}"
    scope = f"$({CHECK_PY} scope --id {name} --state {state} --root .)"
    cond = f"git -C {shlex.quote(repo)} fetch -q origin && {CHECK_PY} eval --id {name} --root {shlex.quote(repo)}"
    cond += flag
    if getattr(a, "final", False):
        cond += " --final"
    record = f"{CHECK_PY} record --id {name} --state {state} --root .{' --review' if review_only else ''}"
    first = "実装レビュー" if review_only else "構造改善"
    steps = [
        {"id": "prepare", "type": "run", "stage": first, "timeout": 600,
         "cmd": f"{CHECK_PY} prepare --id {name} --state {state} --root .{flag}", "on_fail": "abort-before-pr",
         "next": "pr"},
        {"id": "pr", "type": "pr", "stage": first, "base": f"check-base/{name}", "title": f"検査: {name}",
         "body": "template", "on_fail": "abort-before-pr",
         "summary": (f"前回の検査からの差分に{'実装レビュー' if review_only else '構造改善と実装レビュー'}を"
                     f" 1 回ずつ通す（{name}）。範囲・立ったトリガー・"
                     f"先に見る範囲は `{CHECK_PY} scope --id {name}` と状態ディレクトリの check.json にある。"
                     "検査の後に宛先を起点のブランチへ付け替える"),
         "changes": "無し（検査の修正だけ）", "next": "review" if review_only else "assess"},
        {"id": "assess", "type": "run", "preset": "assess", "stage": "構造改善", "skip_to": "review",
         "on_fail": "refactor", "next": "refactor"},
        {"id": "refactor", "type": "drive", "drive": "cross-refactoring", "kind": "構造改善", "stage": "構造改善",
         "timeout": 3600, "args": f"{{pr}} --workflow-step --scope {scope} --baseline-test {tests_all}",
         "next": "review"},
        {"id": "review", "type": "drive", "drive": "cross-review", "kind": "実装レビュー", "stage": "実装レビュー",
         "timeout": 3600, "args": "{pr} --max-rounds 4", "next": "test-all"},
        # 検査の間に起点のブランチが進んでも finish の付け替えの後にマージできるよう、毎回取り込んでから測る
        {"id": "test-all", "type": "run", "stage": "完了判定", "timeout": 1800, "rerun_failed": True,
         "cmd": (f"git pull -q --rebase && git fetch -q origin {shlex.quote(a.base)} && "
                 f"git merge -q --no-edit origin/{shlex.quote(a.base)} && git push -q && "
                 + with_paths(a.test_cmd, a.test_all)), "on_fail": "judge",
         "next": "finish"},
        {"id": "judge", "type": "judge", "inputs": ["test-all"],
         "question": "全体テストの失敗（起点のブランチの取り込みの衝突を含む）を直す（fix）か、修正に無関係として進める"
                     "（finish）か、止める（stop）か",
         "choices": ["fix", "finish", "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": ["test-all"],
         "prompt": (f"失敗したテストを直してコミットし、git push する。origin/{a.base} の取り込みで衝突していれば、"
                    "両方の変更を残して衝突を解き、マージのコミットを作って git push する。"), "next": "test-all"},
        {"id": "finish", "type": "run", "stage": "Pull Request",
         "cmd": f"{CHECK_PY} finish --id {name} --pr {{pr}} --root .", "skip_to": "record", "on_fail": "abort",
         "next": "ready"},
        {"id": "ready", "type": "run", "cmd": "sh -c 'git push -q && gh pr ready {pr}'", "on_fail": "abort",
         "next": "merge"},
        {"id": "merge", "type": "run", "timeout": 7200, "cmd": MERGE_CMD, "probe": MERGE_PROBE, "on_fail": "abort",
         "next": "record"},
        {"id": "record", "type": "run", "cmd": f"{record} --pr {{pr}}", "on_fail": "abort", "next": "end"},
        {"id": "abort", "type": "run", "cmd": f"{record} --failed --pr {{pr}}", "next": "end"},
        {"id": "abort-before-pr", "type": "run", "cmd": f"{record} --failed", "next": "end"},
    ]
    if review_only:
        steps = [s for s in steps if s["id"] not in ("assess", "refactor")]
    issues = list(a.issue or [])
    if not issues and getattr(a, "mission", None):
        try:
            issues = [int(i) for i in json.loads(Path(a.mission).read_text()).get("issues", [])]
        except (OSError, ValueError, TypeError):
            issues = []
    plan = {
        "フェーズ": "検査", "課題": issues, "モード": a.mode, "作業場所": f"{repo}/.worktrees/check/{name}",
        "branch": f"check/{name}", "起点": f"origin/{a.base}", "リポジトリ": repo, "規則": RULE_CHECK_SINCE,
        "上限": 20, "実行の条件": {"cmd": cond, "skip_code": 3}, "steps": steps,
    }
    return with_decls(plan, a)


def cmd_new(a) -> dict:
    since = a.kind == "check" and getattr(a, "since_last", False)
    maker = plan_check_since if since else {"impl": plan_impl, "fix": plan_fix, "check": plan_check,
                                             "release": release_templates.plan_release}[a.kind]
    plan = maker(a)
    key = {"impl": lambda: a.issue[0], "fix": lambda: f"fix-{a.issue[0] if a.issue else Path(a.worktree).name}",
           "check": lambda: f"{a.id}-check" if since else f"{a.pr}-check",
           "release": lambda: f"release-{a.version}"}[a.kind]()
    out = Path(a.out or f"plan-{key}.json")
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return result("supervise-new", "ok", f"計画を書いた: {out}",
                  [{"path": str(out), "kind": a.kind, "steps": [s["id"] for s in plan["steps"]]}],
                  {"steps": len(plan["steps"])},
                  next=(f"実装の queue へ --then {out} で渡す（実装がすべて完了した後に続けて流れる）"
                        if a.kind == "release" else None))
