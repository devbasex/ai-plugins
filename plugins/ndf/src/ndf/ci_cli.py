"""サブコマンド `ndf ci` のエントリポイント。PR の CI の結果を読み（`watch`）、PR をマージし（`merge`）、停止理由の種類で結果 JSON を返す。

読み取り・分類・マージの前段の確かめは外部との接続の層（`ndf.adapters.ci`・`ndf.adapters.github`）が持ち、
このエントリポイントは引数と出力だけを持つ。

マージ先が自動反映の本番チャネルなら、PR に残ったマージの承認の証跡（`core.merge_approval` の `MERGE_MARK`）を
確かめたときだけマージする（#4409）。判定の規則は `core.production_merge` が持つ。証跡が無いときの終え方は
`--production-gate`（`stop`・`defer`・`stale`）で選ぶ。単発のプランの承認待ちは `ask` が質問し、回答の後に `answer` が
プランの実行の回答の記録から証跡を書く。工程（`ndf.progress`）は読まない。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.adapters import github as github_mod
from ndf.adapters import github_merge
from ndf.adapters.ci import (
    Classifier,
    FailedTestSource,
    Reading,
    Watch,
    WatchResult,
    failed_tests_of,
    files_of,
    is_test_check,
    read,
    watch_head,
)
from ndf.adapters.github import GitHub, MergeExpect, MergeOutcome, Pull
from ndf.contracts import result
from ndf.core import actors, approval_words, merge_approval, production_merge
from ndf.core.production_merge import Verdict

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)

# 承認の証跡が無いときの終え方（`--production-gate`）。stop は承認待ち、defer はマージせずに ok、stale は approval_stale
PRODUCTION_GATES = production_merge.GATES
# `metrics.gate` の値（`ndf ci merge` の承認待ち・先送りと `ndf ci ask` が共有する）
GATE_NAME = "production-merge"
# 判定に読むマージ先の設定のファイル（PR のベースリポジトリのマージ先のブランチ）
PROJECT_FILE = production_merge.PROJECT_FILE
# 直前の確かめで判定をやり直した結果が承認待ちになったことを `GitHub.merge` から受ける種類
_REJUDGED = "production_gate"
# 承認資料の、本番へ反映され得ることを示す行
PRODUCTION_LINE = merge_approval.PRODUCTION_MATERIAL_LINE


def _watch_parser(sub: Any) -> None:
    w = sub.add_parser("watch", help="PR の HEAD の必須のチェックの結論が出るまで待つ")
    w.add_argument("--pr", type=int, required=True)
    w.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    w.add_argument("--required", action="append", required=True, help="待つ必須のチェック（PR の HEAD の設定から渡す）")
    w.add_argument("--test", action="append", default=[], help="テストのチェックの名前（分割ジョブも数える）")
    w.add_argument("--budget", type=float, required=True, help="待ちの合計の秒（ランナーの割り当ての待ちは数えない）")
    w.add_argument("--after-merge", action="store_true", help="マージの段階が落ちた後の判定")
    w.add_argument("--merge-failed", action="append", default=[], help="マージの段階が落ちたと読んだチェックの名前")
    w.add_argument(
        "--head",
        help="待つ PR の HEAD の SHA。チェック結果はこの SHA のものだけを読み、作業場所の HEAD は追わない。"
        "待ちの開始時と、成功を返す直前に PR の HEAD を読み、この SHA と違えば approval_stale を返す。"
        "成功を返す直前の読み直しで、成功 1 回につき PR の取得が 1 回増える"
        "（REST の上限に当たって GraphQL で読み替えるときは、API の要求がさらに 1 回増える）",
    )
    w.set_defaults(run=_watch)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ndf ci", description="CI の結果を読み、停止理由の種類で返す")
    sub = p.add_subparsers(dest="command", required=True)
    _watch_parser(sub)
    m = sub.add_parser(
        "merge",
        help="PR を、前段の確かめ（決めていない残リスク・HEAD の食い違い・本番チャネルへの承認の証跡）を通してマージする",
    )
    m.add_argument("--branch", help="PR の head のブランチ（作業場所の HEAD を添える）。--branch か --pr は必須")
    m.add_argument(
        "--pr",
        type=int,
        help="PR の番号。--branch か --base を添えると、まず PR がその指定に合うかを確かめる。"
        "head のブランチ名かマージ先が指定と違う PR、フォークからの PR、マージされずに閉じた PR は refused で止まる。"
        "--branch と --base を両方省くと、この確かめを省く。確かめを通った後も、省いた後も、次に PR がマージ済みかを調べる。マージ済みの場合はマージ操作を省く。--verify-merged の指定がなければ ok を返す。指定があれば、承認した HEAD とマージ先に PR が合うかを確かめ、違えば refused を返す。"
        "作業場所が無いときは、--branch を省き、--pr と --repo を渡す",
    )
    m.add_argument("--base", help="期待するマージ先。対象の PR のマージ先が違えばマージしない")
    m.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    m.add_argument("--method", help="マージの方式（squash・merge・rebase）。省けば基底のブランチで許された方式の先頭")
    m.add_argument("--reviewed", help="レビューを経た HEAD。作業場所の HEAD と違えばマージしない")
    m.add_argument(
        "--production-gate",
        choices=PRODUCTION_GATES,
        default=production_merge.STOP,
        help="自動反映の本番チャネルへ承認の証跡が無いとき: stop は承認待ち、defer はマージせずに ok、stale は approval_stale",
    )
    m.add_argument("--approved-head", help="工程の承認ゲートで承認した HEAD（--approved-base と組で渡す）")
    m.add_argument("--approved-base", help="工程の承認ゲートで承認したマージ先")
    m.add_argument("--presented-out", help="stop で止まるときに承認の対象を書くファイル（ndf ci answer が読む）")
    m.add_argument(
        "--verify-merged",
        action="store_true",
        help="--pr・--approved-head・--approved-base と組で渡す。指定した PR がマージ済みの場合も、HEAD とマージ先が承認時の値と"
        "同じかを確かめる。値が違えば refused で止まる",
    )
    m.add_argument(
        "--required",
        action="append",
        default=[],
        help="マージの前に、マージする HEAD の結論を待つ必須のチェック。渡さなければ待たない",
    )
    m.add_argument("--test", action="append", default=[], help="--required で待つときのテストのチェックの名前")
    m.add_argument("--budget", type=float, help="--required で待つ合計の秒")
    m.set_defaults(run=_merge)
    a = sub.add_parser("ask", help="ブランチの PR の承認の対象を承認資料に載せ、承認の回答を待つ（終了コード 10）")
    a.add_argument("--branch", required=True, help="PR の head のブランチ")
    a.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    a.add_argument("--presented-out", required=True, help="承認の対象を書くファイル（ndf ci answer が読む）")
    a.set_defaults(run=_ask_approval)
    w2 = sub.add_parser("answer", help="プランの実行の回答の記録を、提示した承認の対象と照らしてマージの承認の証跡に書く")
    w2.add_argument("--branch", required=True, help="PR の head のブランチ")
    w2.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば作業場所の origin")
    w2.add_argument("--answer-record", required=True, help="ndf plan next --answer が書いた回答の記録")
    w2.add_argument("--presented", required=True, help="承認待ちのステップが書いた承認の対象のファイル")
    w2.set_defaults(run=_answer)
    return p


def _progress(reading: Reading, elapsed: float) -> None:
    """待つ間の 1 行（何を何秒待っているか。#2317）。"""
    waiting = reading.running + reading.queued + (reading.missing if not reading.settled else [])
    if waiting:
        print(f"CI を待っている（{int(elapsed)} 秒）: {', '.join(waiting)}", file=sys.stderr)


def _item(name: str, outcome: str, test: bool, job: Optional[int] = None) -> dict:
    """結果の項目 1 件（`outcome` はキー `result` の値）。`job` が無ければキーを置かない。"""
    row: dict[str, Any] = {"kind": "check", "name": name, "result": outcome}
    if job is not None:
        row["job"] = job
    row["test"] = test
    return row


def _items(reading: Reading, tests: list[str]) -> list[dict]:
    rows = [_item(c.name, c.conclusion, True, c.job) for c in reading.failed_tests]
    rows += [_item(c.name, c.conclusion, False, c.job) for c in reading.failed_other]
    rows += [_item(n, "missing", is_test_check(n, tests)) for n in reading.missing]
    rows += [_item(n, "stale", is_test_check(n, tests)) for n in reading.stale]
    return rows


def _failed_metrics(hub: GitHub, reading: Reading, cwd: Path) -> dict:
    """落ちたテストの node id・ファイルと、チェックごとの読み取りの記録（結果 JSON の `metrics` のキー）。

    作業場所の設定ファイルの `test.ci.junit_artifacts` と suite を読み、JUnit の成果物を先に、ログを後に読む。
    ジョブを特定できないチェックがあっても分類は保ち、理由を `log_error` に残す。
    """
    source = FailedTestSource.from_work(cwd)
    found = failed_tests_of(hub, reading.failed_tests, source)
    out: dict[str, Any] = {
        "failed_tests": found.nodes,
        "test_files": files_of(found.nodes),
        "failed_test_reads": [r.as_metric() for r in found.reads],
    }
    if source.junit_artifacts is not None:
        out["junit_artifacts"] = source.junit_artifacts
    if found.job_errors:
        out["log_error"] = "; ".join(found.job_errors)
    return out


def _unread() -> Reading:
    raise AssertionError("Watch の読み手が差し込まれていない")


# PR の HEAD が作業場所の HEAD に追いつくのを待つ間、待っているものとして出す名前
BEHIND = "PR の HEAD の更新（{sha}）"


def local_head(head_ref: str, repo: Optional[git.Git] = None) -> Optional[str]:
    """作業場所が PR の head のブランチ `head_ref` を checkout していれば、その HEAD の SHA。違えば・読めなければ None。"""
    g = repo if repo is not None else git.Git()
    try:
        branch = g.attempt("symbolic-ref", "--short", "-q", "HEAD")
        if branch.returncode != 0 or branch.stdout.strip() != head_ref:
            return None
        return g.rev_parse("HEAD")
    except (git.GitError, OSError):  # noqa: NDF401 外部コマンド: 作業場所の HEAD が読めなければ PR の HEAD をそのまま読む（直す前と同じ）
        return None


class HeadReader:
    """PR の HEAD のコミットの必須のチェックを読む `Watch.reader`。

    `local`（作業場所の HEAD）を渡すと、PR の HEAD がそれに追いつくまでは PR を読み直し、結論の出ていない読み取り
    （待っているものは `BEHIND`）を返す。push の直後は GitHub の PR がまだ前のコミットを指し、そのまま読むと前の
    コミットの結果を返すため。待ちの間隔と予算は `Watch` が持つ。読んだ PR は `pull` に残る（結果 JSON が使う）。

    `pinned`（`--head` と一致を確かめた PR の HEAD の完全な SHA）を渡すと、`local` を使わず、そのコミットのチェックだけを読む。
    作業場所の未送信のコミットを待ったり、待つ間に PR が動いた先のコミットの結果を読んだりしないため（#3702）。
    """

    def __init__(
        self,
        hub: GitHub,
        pull: github_mod.Pull,
        local: Optional[str],
        required: list[str],
        tests: list[str],
        pinned: Optional[str] = None,
    ) -> None:
        self.hub, self.pull, self.local, self.required, self.tests, self.pinned = hub, pull, local, required, tests, pinned

    def __call__(self) -> Reading:
        if self.pinned is not None:
            return read(self.hub.check_runs(self.pinned), self.required, self.tests)
        if self.local is not None and self.pull.head_sha != self.local:
            self.pull = self.hub.pull(self.pull.number)
            if self.pull.head_sha != self.local:
                return Reading([], [], [BEHIND.format(sha=self.local[:12])], [], [], [], False)
        return read(self.hub.check_runs(self.pull.head_sha), self.required, self.tests)


def _watch(args: argparse.Namespace) -> dict:
    # 上限の解除が待ちの残りを越えるなら githubkit の中で眠らず、`infra_wait` と `retry_after` で返す。
    # 残りは Watch と同じ数え方（ランナーの割り当ての待ちを予算に数えない）で読む
    # 読み手は PR の HEAD が分かってから差し込む（PR を読む API の呼び出しも同じ予算に入れる）
    # マージの段階がテストのチェックの落ちを読んでいれば、読み直しが pending でも理由は `test_failed` に決まるので待たない
    classifier = Classifier(args.test, args.merge_failed if args.after_merge else None)
    watch = Watch(_unread, args.budget, on_round=_progress, decided=lambda r: classifier.cause(r) == "test_failed")
    hub = GitHub(args.repo if args.repo else git.Git().remote_repo(), github_mod.client(remaining=watch.remaining))
    pull = hub.pull(args.pr)
    moved = _moved_head(args, pull)
    if moved is not None:
        return moved
    if args.head is not None:
        # 一致を確かめた PR の HEAD の完全な SHA にチェックの読み取りを固定する（作業場所の HEAD を追わない。#3702）
        reader = HeadReader(hub, pull, None, args.required, args.test, pinned=pull.head_sha)
    else:
        reader = HeadReader(hub, pull, local_head(pull.head_ref), args.required, args.test)
    watch.reader = reader
    watched = watch.run()
    kind = watched.cause_by(classifier)
    if kind is None and args.head is not None:
        # 成功を確定する前に PR を読み直す（待つ間に HEAD が動けば、読んだ結果は承認した HEAD のものと言えない。#3702）
        moved = _moved_head(args, hub.pull(args.pr))
        if moved is not None:
            return moved
    return _report(args, reader.pull, hub, watched, kind)


def _moved_head(args: argparse.Namespace, pull: github_mod.Pull) -> Optional[dict]:
    """`--head` があり、読んだ PR の HEAD がそれと違えば `approval_stale` の結果。無いか同じなら None。"""
    if args.head is None or git.sha_matches(args.head, pull.head_sha):
        return None
    detail = f"#{pull.number} の HEAD {pull.head_sha[:12]} が待つ HEAD {args.head[:12]} と違う（承認ゲートから質問し直す）"
    return result.stopped(TOOL, "approval_stale", detail, instance=pull.head_sha, items=_pr_item_list(pull.number))


def _report(args: argparse.Namespace, pull: github_mod.Pull, hub: GitHub, watched: WatchResult, kind: Optional[str]) -> dict:
    """待ちの結果と停止理由の種類（通っていれば None）から結果 JSON を組む。"""
    metrics: dict[str, Any] = {"rounds": watched.rounds, "elapsed": round(watched.elapsed, 1), "queued_for": round(watched.queued_for, 1)}
    if kind == "test_failed":
        metrics.update(_failed_metrics(hub, watched.reading, Path.cwd()))
    items = _items(watched.reading, args.test)
    if kind is None:
        return result.result(TOOL, result.OK, f"#{pull.number} の必須のチェックが通った", items=items, metrics=metrics)
    detail = f"#{pull.number}（{pull.head_sha[:12]}）の CI: {watched.reading.verdict}"
    return result.stopped(TOOL, kind, detail, instance=pull.head_sha, items=items, metrics=metrics)


def _hub(args: argparse.Namespace) -> GitHub:
    return GitHub(args.repo if args.repo else git.Git().remote_repo(), github_mod.client())


def _open_pull(hub: GitHub, branch: str) -> Optional[Pull]:
    """ブランチの開いた PR（新しい順の先頭）。無ければ None。"""
    opened = [p for p in hub.pulls_by_head([branch])[branch] if p.state == "open"]
    return opened[0] if opened else None


def _no_open_pull(branch: str) -> dict:
    """ブランチの開いた PR が無いときの停止結果。"""
    return result.stopped(TOOL, "precondition", f"{branch} の開いた PR が無い")


def _pr_target(hub: GitHub, pull: Pull) -> merge_approval.MergeTarget:
    """読んだ PR の承認の対象（ベースリポジトリ・番号・マージ先・HEAD）。"""
    return merge_approval.MergeTarget(hub.repo, pull.number, pull.base_ref, pull.head_sha)


def _verdict_of(hub: GitHub, base: str, text: Optional[str], repo: str) -> Verdict:
    """マージ先の設定の本文の判定。読めない・別のリポジトリから読んだなら `UNKNOWN`。"""
    return production_merge.judge_project_text(text, base, source_repo=repo, repo=hub.repo)


def _judge_pr(hub: GitHub, pull: Pull) -> tuple[Verdict, Optional[str]]:
    """PR のベースリポジトリのマージ先から設定を読み（作業場所・リモートを読まない。#4409 の決定 3）、判定と設定の blob の oid を返す。"""
    found = hub.branch_file(pull.base_ref, PROJECT_FILE)
    return _verdict_of(hub, pull.base_ref, found.text, found.repo), found.oid


def _approval(hub: GitHub, target: merge_approval.MergeTarget) -> Optional[merge_approval.MergeRow]:
    """今の承認の対象への有効な承認の証跡（決定 7 の「証跡の判定」）。無ければ None。"""
    return merge_approval.merge_judgment(merge_approval.merge_rows(hub, target.number, hub.viewer()), target)


def _approval_material(target: merge_approval.MergeTarget, production: bool) -> str:
    """承認資料。本番へ反映され得るなら、その行を書く（承認する人が本番への反映を知らずに承認しない）。"""
    lines = [
        f"# PR {target.number} のマージの承認",
        "",
        f"- リポジトリ: {target.repo}",
        f"- マージ先: {target.base}",
        f"- {approval_words.HEAD_LINE} {target.head}",
    ]
    if production:
        lines.append(f"- {PRODUCTION_LINE}")
    return "\n".join(lines) + "\n"


def material_beside(path: Path) -> Path:
    """承認の対象のファイル `path` の隣の承認資料のパス。`path` が `.md` でも同じパスにならない（対象を資料で上書きしない）。"""
    return path.with_suffix(".md") if path.suffix != ".md" else path.with_name(f"{path.stem}.material.md")


def _swap_in(path: Path, text: str) -> None:
    """`path` を一時ファイルからの置き換えで書く（`path` がシンボリックリンクでも参照先を書き換えない）。"""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _presented(path: Path, target: merge_approval.MergeTarget, production: bool) -> tuple[str, Path]:
    """提示の識別子を新しく作り、承認の対象と合わせて `path` へ、承認資料を隣（`material_beside`）へ書いて、識別子と資料のパスを返す。"""
    presented_id = uuid.uuid4().hex
    body = {"presented_id": presented_id, **target._asdict(), "production": production}
    path.parent.mkdir(parents=True, exist_ok=True)
    material = material_beside(path)
    _swap_in(material, _approval_material(target, production))
    _swap_in(path, json.dumps(body, ensure_ascii=False))
    return presented_id, material


def _held_target(shown: dict) -> Optional[merge_approval.MergeTarget]:
    """承認の対象のファイルの中身（`_presented` が書く形）から読んだ承認の対象。形が違えば None。"""
    try:
        return merge_approval.MergeTarget(str(shown["repo"]), int(shown["number"]), str(shown["base"]), str(shown["head"]))
    except (KeyError, TypeError, ValueError):  # noqa: NDF401 ファイル: 形の違う中身は承認の対象が無いとして呼び手が扱う
        return None


def _pr_item_list(number: int, merge_sha: str = "") -> list[dict]:
    """PR の項目。マージした・マージ済みだった PR は、GitHub が返したマージコミットを持つ（単位を流す処理が、スプリントの作業場所を最後の単位のマージ結果まで進めるのに使う。#4369）。"""
    return [{"kind": "pr", "number": number, **({"merge_sha": merge_sha} if merge_sha else {})}]


def _asking(target: merge_approval.MergeTarget, verdict: Verdict, out: Optional[str], summary: str) -> dict:
    """承認の回答を待つ結果。`out` があれば承認の対象と承認資料を書く（書けなければ precondition）。"""
    production = production_merge.reaches_production(verdict)
    metrics: dict[str, Any] = {"gate": GATE_NAME, "verdict": verdict.value, "production": production}
    material: Optional[str] = None
    if out is not None:
        try:
            metrics["presented_id"], written = _presented(Path(out), target, production)
        except OSError as exc:
            return result.stopped(TOOL, "precondition", f"承認の対象を {out} へ書けない: {exc}", items=_pr_item_list(target.number))
        material = str(written)
    routes = {approval_words.APPROVED: None, approval_words.REJECTED: None}
    items = _pr_item_list(target.number)
    return result.result(TOOL, result.GATE, summary, items=items, metrics=metrics, routes=routes, presentation_path=material)


def _stale(target: merge_approval.MergeTarget, detail: str, verdict: Optional[Verdict] = None) -> dict:
    """承認の取り直しで止まる結果。再試行のコマンドは工程の実行側が組む（状態機械の ID と工程を知らないため）。"""
    metrics: dict[str, Any] = {"gate": GATE_NAME}
    if verdict is not None:
        metrics["verdict"] = verdict.value
    return result.stopped(TOOL, "approval_stale", detail, instance=target.head, items=_pr_item_list(target.number), metrics=metrics)


def _unapproved(args: argparse.Namespace, target: merge_approval.MergeTarget, verdict: Verdict) -> dict:
    """承認が要って有効な証跡が無いときの結果（`--production-gate` の値で決まる。マージはしていない）。"""
    where = f"#{target.number}（{target.base} 宛て、HEAD {target.head[:12]}）"
    if args.production_gate == production_merge.DEFER:
        metrics = {"gate": GATE_NAME, "merge": "deferred", "verdict": verdict.value}
        summary = f"{where} は本番チャネルへの承認の証跡が無いので、マージをリリース承認ゲートの後へ先送りした"
        return result.result(TOOL, result.OK, summary, items=_pr_item_list(target.number), metrics=metrics)
    if args.production_gate == production_merge.STALE:
        return _stale(target, f"{where} に、今の HEAD とマージ先への本番チャネルの承認の証跡が無い（承認ゲートから質問し直す）", verdict)
    return _asking(target, verdict, args.presented_out, f"{where} は本番チャネルへのマージなので承認の回答を待つ")


def _read_json(path: str) -> Optional[dict]:
    try:
        got = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない記録は無いものとして呼び手が断る
        return None
    return got if isinstance(got, dict) else None


def _approved_mismatch(args: argparse.Namespace, pull: Pull) -> Optional[str]:
    """`--approved-head`・`--approved-base` が今の PR と違えば理由。渡されていなければ None。"""
    if args.approved_head is None and args.approved_base is None:
        return None
    head = args.approved_head if args.approved_head is not None else ""
    base = args.approved_base if args.approved_base is not None else ""
    if not base or base != pull.base_ref:
        return f"承認したマージ先 {base or '（無い）'} が今のマージ先 {pull.base_ref} と違う"
    if not head or not git.sha_matches(head, pull.head_sha):
        return f"承認した HEAD {head[:12] or '（無い）'} が今の HEAD {pull.head_sha[:12]} と違う"
    return None


def _foreign(args: argparse.Namespace, pull: Pull) -> Optional[str]:
    """番号の PR が `--branch`・`--base` の指すものと違えば理由。どちらも無ければ照らさない（番号だけの呼び出し）。"""
    if args.branch is None and args.base is None:
        return None
    if args.branch is not None and pull.head_ref != args.branch:
        return f"head が {pull.head_ref} で、{args.branch} でない"
    if args.base is not None and pull.base_ref != args.base:
        return f"マージ先が {pull.base_ref} で、{args.base} でない"
    if pull.cross_repository:
        return "head がフォークのブランチ"
    if pull.state != "open" and not pull.merged:
        return "PR がマージされないまま閉じている"
    return None


def _numbered_target(hub: GitHub, args: argparse.Namespace) -> Pull | dict:
    """`--pr` の PR。マージ済みなら何もせずに ok を返す（方式の問い合わせ・設定の読み取りをしない。決定 5）。

    マージ先などの確かめは、マージ済みの確かめより先にする。
    """
    pull = hub.pulls([args.pr])[args.pr]
    if pull is None:
        return result.stopped(TOOL, "precondition", f"#{args.pr} は PR でない")
    foreign = _foreign(args, pull)
    if foreign is not None:
        detail = f"#{pull.number} の{foreign}"
        return result.stopped(TOOL, "refused", detail, instance=pull.head_sha, items=_pr_item_list(pull.number))
    if pull.merged:
        mismatch = _approved_mismatch(args, pull) if args.verify_merged else None
        if mismatch is not None:
            # マージ済みの PR は承認し直しても戻せないので、approval_stale でなく refused で止まる（#3702）
            detail = f"マージ済みの #{pull.number} の{mismatch}"
            return result.stopped(TOOL, "refused", detail, instance=pull.head_sha, items=_pr_item_list(pull.number, pull.merge_sha))
        summary = f"#{pull.number} は既にマージ済み"
        return result.result(TOOL, result.OK, summary, items=_pr_item_list(pull.number, pull.merge_sha), metrics={"merge": "already"})
    if pull.state != "open":
        return result.stopped(TOOL, "precondition", f"#{pull.number} は閉じている")
    return pull


def _target(hub: GitHub, args: argparse.Namespace) -> Pull | dict:
    """マージする PR。決められなければ（マージ済みも含めて）返す結果。

    `--pr` が無く `--base` があれば、ブランチの開いた PR のうちマージ先が `--base` のものを選ぶ（フォークの PR は除く）。
    """
    if args.pr is not None:
        return _numbered_target(hub, args)
    if args.base is None:
        pull = _open_pull(hub, args.branch)
        return pull if pull is not None else _no_open_pull(args.branch)
    # 同じリポジトリの head とマージ先が同じ開いた PR は 1 件だけなので、見つかった時点で読み終える
    opened = hub.open_pulls_of_head(args.branch, enough=1, base=args.base)
    return opened[0] if opened else result.stopped(TOOL, "precondition", f"{args.branch} の {args.base} 宛ての開いた PR が無い")


def _worktree_head(args: argparse.Namespace) -> tuple[Optional[str], Optional[dict]]:
    """`--branch` なら作業場所の HEAD。`--reviewed`（レビューを経た HEAD）と違えば、PR を読まずに止まる `refused` の結果。"""
    if args.branch is None:
        return None, None
    head = git.Git().rev_parse("HEAD")
    if args.reviewed is not None and args.reviewed != head:
        detail = f"作業場所の HEAD {head[:12]} がレビューを経た HEAD {args.reviewed[:12]} と違う"
        return head, result.stopped(TOOL, "refused", detail, instance=head)
    return head, None


def _held_mismatch(args: argparse.Namespace, pull: Pull, target: merge_approval.MergeTarget) -> Optional[dict]:
    """承認した対象（`--approved-head`・`--approved-base`）か、`--pr` のときのレビューを経た HEAD が今の PR と違えば止まる結果。"""
    mismatch = _approved_mismatch(args, pull)
    if mismatch is not None:
        return _stale(target, f"#{pull.number} の{mismatch}（承認ゲートから質問し直す）")
    if args.reviewed is not None and args.pr is not None and not git.sha_matches(args.reviewed, pull.head_sha):
        detail = f"#{pull.number} の HEAD がレビューを経た HEAD {args.reviewed[:12]} と違う"
        return result.stopped(TOOL, "refused", detail, instance=pull.head_sha)
    return None


def _dequeue_queued(hub: GitHub, pull: Pull) -> None:
    """読んだ PR がマージキューに入っていれば外す（止まると返した後に、GitHub のキューがマージしないようにする）。"""
    if pull.queued:
        hub.dequeue(pull.number)


def _approved_by(
    args: argparse.Namespace, hub: GitHub, pull: Pull, target: merge_approval.MergeTarget, verdict: Verdict
) -> tuple[Optional[str], Optional[dict]]:
    """承認が要る判定なら、今の承認の対象への証跡の担い手。証跡が無ければ、マージキューから外して `--production-gate` の結果。
    token の利用者のアカウント ID を読めなければ、マージキューから外して `precondition`（`merge_approval.NO_LOGIN`）。"""
    if not production_merge.needs_approval(verdict):
        return None, None
    unreadable: Optional[dict] = None
    try:
        row = _approval(hub, target)
    except merge_approval.TrailError as exc:
        # token の利用者を読めず証跡を数えられない。承認し直しても変わらないので approval_stale にせず precondition で止まる
        row = None
        unreadable = result.stopped(TOOL, "precondition", str(exc), instance=target.head, items=_pr_item_list(target.number))
    if row is not None:
        return row.by, None
    _dequeue_queued(hub, pull)
    return None, unreadable if unreadable is not None else _unapproved(args, target, verdict)


def _failed_checks(args: argparse.Namespace, hub: GitHub, pull: Pull) -> Optional[dict]:
    """`--required` を受けたら、マージする HEAD のチェックの結論を待つ。通らなければ止まる結果。

    読むのは、今の PR と同じだと確かめた HEAD のコミットそのもので、待つ間に PR の HEAD が動いて戻っても別のコミットの結果を読まない。
    """
    if not args.required:
        return None
    watch = Watch(watch_head(hub, pull.head_sha, list(args.required), list(args.test)), args.budget, on_round=_progress)
    watched = watch.run()
    kind = watched.cause_by(Classifier(args.test))
    return None if kind is None else _report(args, pull, hub, watched, kind)


def _merged(
    args: argparse.Namespace, target: merge_approval.MergeTarget, outcome: MergeOutcome, verdict: Verdict, approved_by: Optional[str]
) -> dict:
    """`GitHub.merge` の結果から結果 JSON を組む。"""
    items = _pr_item_list(target.number)
    if outcome.kind == _REJUDGED:
        return _unapproved(args, target, Verdict(outcome.detail))
    if outcome.kind == github_merge.TARGET_MOVED:
        # 承認の対象を持つ呼び手（承認ゲートの後のマージ・stale）は、初めの読み取りで見つけたときと同じく承認を取り直す
        if args.approved_head is not None or args.production_gate == production_merge.STALE:
            return _stale(target, f"{outcome.detail}（承認ゲートから質問し直す）")
        return result.stopped(TOOL, "refused", outcome.detail, instance=outcome.head_sha, items=items)
    if outcome.merged:
        metrics: dict[str, Any] = {"merge": "merged", "verdict": verdict.value}
        if approved_by is not None:
            metrics["approved_by"] = approved_by
        return result.result(TOOL, result.OK, outcome.detail, items=_pr_item_list(target.number, outcome.merge_sha), metrics=metrics)
    kind = outcome.kind if outcome.kind is not None else "unknown"
    return result.stopped(TOOL, kind, outcome.detail, instance=outcome.head_sha, items=items)


class _Rejudge:
    """`GitHub.merge` の直前の確かめで、読み直したプロジェクトのファイルから本番チャネルを判定し直す関数（`MergeExpect.rejudge`）。

    判定し直してマージへ進むときは、判定し直した値を `verdict` に採り、成功の結果へ引き継ぐ（だれの承認でどの経路のマージを
    したかを結果に残す）。承認が要れば今の承認の対象への証跡の担い手を `approved_by` に採り、要らなければ空にする。
    判定し直さなかったとき（設定の oid が同じ）は初めの判定と担い手のまま。
    """

    def __init__(self, hub: GitHub, pull: Pull, target: merge_approval.MergeTarget, verdict: Verdict, approved_by: Optional[str]) -> None:
        self._hub, self._pull, self._target = hub, pull, target
        self.verdict, self.approved_by = verdict, approved_by

    def __call__(self, text: Optional[str]) -> Optional[MergeOutcome]:
        again = _verdict_of(self._hub, self._pull.base_ref, text, self._hub.repo)
        if not production_merge.needs_approval(again):
            # 承認の要らない経路でマージするので、初めの判定で読んだ担い手を結果に残さない
            self.verdict, self.approved_by = again, None
            return None
        try:
            row = _approval(self._hub, self._target)
        except merge_approval.TrailError as exc:
            # 初めの判定と同じく、token の利用者を読めなければ precondition（approval_stale にしない）。
            # キューにある PR は、`GitHub.merge` が確かめの応答の最新のキューの状態で外す
            return MergeOutcome(False, "precondition", str(exc), self._pull.head_sha)
        if row is not None:
            self.verdict, self.approved_by = again, row.by
            return None
        return MergeOutcome(False, _REJUDGED, again.value, self._pull.head_sha)


def _invalid_args(args: argparse.Namespace) -> Optional[dict]:
    """`_merge` の引数の組み合わせを確かめ、足りなければ `invalid` で止まる結果を返す。"""
    if args.branch is None and args.pr is None:
        return result.stopped(TOOL, "invalid", "--branch か --pr のどちらかを渡す")
    if (args.approved_head is None) != (args.approved_base is None):
        return result.stopped(TOOL, "invalid", "--approved-head と --approved-base は組で渡す")
    if args.verify_merged and (args.pr is None or args.approved_head is None):
        # 番号を渡さない経路は開いている PR しか探さないので、マージ済みの PR を承認の値と照らせない（#3702）
        return result.stopped(TOOL, "invalid", "--verify-merged は --pr・--approved-head・--approved-base と組で渡す")
    if args.required and args.budget is None:
        return result.stopped(TOOL, "invalid", "--required を渡すときは --budget も渡す")
    return None


def _merge(args: argparse.Namespace) -> dict:
    """PR を、確かめ・本番チャネルの判定・承認の証跡の確かめを通してから `GitHub.merge` でマージする（#2602・#4409）。

    本文に決めていない「マージの前に決める」残リスクの行（`- [ ]`）が残れば、マージせずに `refused` で止まり、残った行を理由に出す。
    `--reviewed`（レビューを経た HEAD）が作業場所の HEAD と違えば、PR を読まずに `refused` で止まる（#3879 決定 2）。
    `--pr` に `--branch`・`--base` を添えた呼び出しは、head・マージ先・リポジトリの違う PR と、マージされず閉じた PR を
    要求を送らずに `refused` にする（#4369）。直前の確かめは、判定の前に読んだマージ先と照らすので、`--base` も守られる。
    `--approved-head`・`--approved-base` を受けたら、判定の前に今の PR と照らし、違えば判定によらず `approval_stale` で止まる。
    マージ先が自動反映の本番チャネル（判定の値が `automatic`・`unset`・`unknown`）なら、今の承認の対象への証跡があるときだけ
    マージし、無ければ `--production-gate` の形で終える。
    `--required` を受けたら、承認した対象を今の PR と照らした後に、マージする HEAD のチェックを待つ。マージ済みの PR は待たない。
    待つ間に HEAD かマージ先が変われば、`GitHub.merge` の直前の確かめが止める。
    """
    invalid = _invalid_args(args)
    if invalid is not None:
        return invalid
    head, refused = _worktree_head(args)
    if refused is not None:
        return refused
    hub = _hub(args)
    pull = _target(hub, args)
    if isinstance(pull, dict):
        return pull
    target = _pr_target(hub, pull)
    stopped = _held_mismatch(args, pull, target)
    if stopped is not None:
        _dequeue_queued(hub, pull)
        return stopped
    failed = _failed_checks(args, hub, pull)
    if failed is not None:
        return failed
    if args.approved_head is not None:
        head = args.approved_head
    verdict, oid = _judge_pr(hub, pull)
    approved_by, unapproved = _approved_by(args, hub, pull, target, verdict)
    if unapproved is not None:
        return unapproved
    rejudge = _Rejudge(hub, pull, target, verdict, approved_by)
    expect = MergeExpect(pull, pull.base_ref, pull.head_sha, PROJECT_FILE, oid, rejudge)
    outcome = hub.merge(pull.number, expect_head=head if head is not None else pull.head_sha, method=args.method, expect=expect)
    return _merged(args, target, outcome, rejudge.verdict, rejudge.approved_by)


def _ask_approval(args: argparse.Namespace) -> dict:
    """`--branch` の PR の承認の対象を承認資料と提示のファイルに書き、承認の回答を待つ（プランの中の承認ゲート `{{gate}}`）。"""
    hub = _hub(args)
    pull = _open_pull(hub, args.branch)
    if pull is None:
        return _no_open_pull(args.branch)
    verdict, _ = _judge_pr(hub, pull)
    target = _pr_target(hub, pull)
    return _asking(target, verdict, args.presented_out, f"#{pull.number} のマージの承認の回答を待つ")


def _refused_answer(detail: str, number: Optional[int] = None) -> dict:
    return result.stopped(TOOL, "refused", detail, items=_pr_item_list(number) if number is not None else [])


def _answer_with_shown(args: argparse.Namespace) -> tuple[Optional[tuple[dict, dict]], Optional[dict]]:
    """回答の記録と、その回答先の提示（承認の対象のファイルの中身）。利用者の今の提示への回答でなければ、断る結果。"""
    record = _read_json(args.answer_record)
    if record is None:
        return None, _refused_answer(f"回答の記録 {args.answer_record} が無い（ndf plan next --answer <回答> --by human で回答する）")
    if record.get("by") != actors.HUMAN:
        detail = f"回答した者 {record.get('by')} は利用者（{actors.HUMAN}）でないので、マージの承認の証跡を書かない"
        return None, _refused_answer(detail)
    answer = record.get("answer")
    if answer not in approval_words.ANSWERS:
        return None, _refused_answer(f"回答 {answer} は {' / '.join(approval_words.ANSWERS)} のどれでもない")
    shown = _read_json(args.presented)
    presented_id = record.get("presented_id")
    if shown is None or not presented_id or shown.get("presented_id") != presented_id:
        return None, _refused_answer("回答の記録が今の提示（承認の対象のファイル）への回答でない（プランを流し直して回答し直す）")
    return (record, shown), None


def _unchanged_target(args: argparse.Namespace, hub: GitHub, shown: dict) -> tuple[Optional[merge_approval.MergeTarget], Optional[dict]]:
    """今の PR の承認の対象。提示した承認の対象か「本番へ反映されるか」が今の PR と違えば、止まる結果。"""
    pull = _open_pull(hub, args.branch)
    if pull is None:
        return None, _no_open_pull(args.branch)
    now = _pr_target(hub, pull)
    held = _held_target(shown)
    if held is None:
        return None, _refused_answer(f"承認の対象のファイル {args.presented} の形が違う", pull.number)
    if not held.same(now):
        detail = f"#{pull.number} の承認の対象が質問の後に変わった（今は {now.base} 宛て、HEAD {now.head[:12]}）"
        return None, _refused_answer(detail, pull.number)
    # 本番へ反映されるかも提示から変わっていないかを照らす（工程の承認ゲートと同じ。設定の読み取りは利用者も同じ要求で読む）
    verdict, _ = _judge_pr(hub, pull)
    production = production_merge.reaches_production(verdict)
    if shown.get("production") is not production:
        reach = "反映される" if production else "反映されない"
        return None, _refused_answer(
            f"#{pull.number} は質問の後にマージ先の設定が変わり、このマージは本番へ{reach}ようになった"
            "（プランを流し直し、出し直した資料で回答し直す）",
            pull.number,
        )
    return now, None


def _write_answer(hub: GitHub, record: dict, shown: dict, target: merge_approval.MergeTarget) -> dict:
    """確かめを通った回答をマージの承認の証跡に書き、結果 JSON を組む。"""
    answer = record.get("answer")
    production = shown.get("production") is True
    # 本番へ反映されないマージの承認は読む側が無いので、コメントを読まず投稿もしない。
    # 却下は書く（同じ対象への先の承認を、設定が自動反映へ戻った後も取り消す）
    if not production and answer == approval_words.APPROVED:
        summary = f"#{target.number} は本番へ反映されないので、回答 {answer} の証跡は書かない"
        metrics = {"answer": answer, "written": False}
        return result.result(TOOL, result.OK, summary, items=_pr_item_list(target.number), metrics=metrics)
    row = merge_approval.MergeRow(
        str(answer),
        merge_approval.USER_BY,
        target,
        production,
        f"{record.get('run')}:{record.get('step')}:{record.get('presented_id')}",
    )
    written = merge_approval.write_merge_row(hub, row, hub.viewer())
    summary = f"#{target.number} へ回答 {answer} のマージの承認の証跡を{'書いた' if written else '既に書いてあった'}"
    return result.result(TOOL, result.OK, summary, items=_pr_item_list(target.number), metrics={"answer": answer, "written": written})


def _answer(args: argparse.Namespace) -> dict:
    """プランの実行が書いた回答の記録を、提示した承認の対象と今の PR に照らしてから、マージの承認の証跡に書く
    （本番へ反映されないマージの承認は、照らすだけで書かない）。

    書くのは、回答した者が利用者（`human`）で、記録の提示の識別子が提示のファイルの識別子と同じで、提示した承認の対象と
    「本番へ反映されるか」が今の PR と同じときだけで、担い手はいつも `user` である。端末から直に打っても、回答の記録が無ければ書かない（決定 7）。
    """
    answered, refused = _answer_with_shown(args)
    if answered is None:
        return refused
    record, shown = answered
    hub = _hub(args)
    target, stopped = _unchanged_target(args, hub, shown)
    if target is None:
        return stopped
    return _write_answer(hub, record, shown, target)


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        out = args.run(args)
    except github_mod.GitHubError as exc:
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        out = result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)
    except git.GitError as exc:
        out = result.stopped(TOOL, "precondition", str(exc))
    except merge_approval.TrailError as exc:
        out = result.stopped(TOOL, "refused", str(exc))
    return result.emit(out)
