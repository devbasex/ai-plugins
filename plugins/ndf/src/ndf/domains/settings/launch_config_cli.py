"""サブコマンド `ndf launch-config`: 起動の設定（`.ndf/launch.json`）の承認を取り、候補を反映する（#1959）。

    ndf launch-config draft                                     候補（今の設定の写し）を作り、パスを返す
    ndf launch-config show [--candidate <候補>] [--question approve]
    ndf launch-config approve --sha256 <値> [--request <リクエスト>] [--candidate <候補>]
    ndf launch-config revoke --sha256 <値>

承認を確定するのは人だけで、確認は端末での入力（名前とハッシュの先頭 8 文字）か、`show --question approve` の質問への回答
（hook `launch-answer` が記録する）である。NDF が起動した AI の中（`NDF_AI_CHILD`）では draft・approve・revoke を拒む。
対象が空か承認済みの候補は、承認の行を書かずに反映する（許可は広がらない）。ただし `--request` を渡して「承認しない」と
回答していれば、対象が空でも反映しない。作業場所はカレントディレクトリから `.git` を持つ親をたどって決める。
終了コード: 0 = ok / 10 = 承認の質問か端末のコマンドを返した（gate）/ 停止理由の表のとおり（refused・precondition・invalid）。
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shlex
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from ndf import cli, identity
from ndf.adapters import git, launch_approval as la, launch_candidates, launch_records, launch_settings as ls
from ndf.contracts import internal, result

TOOL = cli.tool_of(__name__)
HASH = re.compile(r"[0-9a-f]{64}")
# 端末の確認で打つハッシュの先頭の文字数
PREFIX = 8
# 承認の質問の区分（.ndf/ の書き換えは共通原則の C7）
SOURCE = "ndf-ask:redline:C7"
# 承認の対象に unsandboxed があるとき、質問と端末の表示に入れる文（設計書の「承認を取る」）
WARNING = (
    "この承認により、サンドボックスを使えない場合は、AI が起動先の環境で実行できるコマンドを個別の確認なしで実行できます。"
    "Kiro・agy に作業を任せる場合は毎回この扱いになります。コンテナなどで別途隔離している場合も、その環境内では同じ権限で動きます。"
)
# 片付けてよい候補の一覧に添える文
CLEANUP_NOTE = "metrics.cleanup_targets の候補は消してよい。消す前に、その候補を開いているエディタでの編集を終えて閉じる"


class _NoConsent(Exception):
    """承認の回答が使えない。"""


def _worktree_root() -> Path:
    return ls.launch_root(Path.cwd())


def _in_ai_child() -> bool:
    return bool(os.environ.get(identity.env_name("ai-child")))


def _session() -> Optional[str]:
    """回答を記録できる会話の id。hook `launch-answer` が記録しない会話と、会話の id が無い場合は None。"""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")  # hook `launch-answer` は同じ会話の質問の回答だけを記録する
    if launch_records.unattended(os.environ) or not session:
        return None
    return session


def _refused_config(detail: str) -> dict[str, Any]:
    return result.stopped(TOOL, "refused", detail)


def _approver_name(root: Path) -> str:
    """承認の選択肢に出す名前（`git config user.name`。無ければログイン名）。"""
    name = git.Git(root).attempt("config", "user.name").stdout.strip()
    return name if name else getpass.getuser()


def _terminal() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _target_lines(t: ls.Target) -> list[str]:
    """承認の対象の一覧（MCP はサーバ名・コマンド名・定義ファイルの場所・ハッシュだけ）とハッシュ。unsandboxed があれば警告の文。"""
    listing = t.listing
    out = []
    for name, spec in listing["steps"].items():
        out.append(f"工程 {name}: " + ("、".join(f"{k}={_spec_text(v)}" for k, v in spec.items()) or "指定なし"))
    if listing["unsandboxed"]:
        out.append("安全装置を外してよいランタイム: " + "、".join(listing["unsandboxed"]))
    for f in listing["files"]:
        servers = "、".join(f"{s['name']}（{s['command']}）" for s in f["servers"]) or "無し"
        out.append(f"MCP の定義 {f['path']}（ハッシュ {(f['sha256'] or '無い')[:12]}）: {servers}")
    if not out:
        out.append("承認の対象は空（許可を広げる指定が無い）")
    out.append(f"ハッシュ: {t.sha256}")
    return out + ([WARNING] if listing["unsandboxed"] else [])


def _spec_text(value: Any) -> str:
    return " ".join(value) if isinstance(value, list) else str(value)


def _approve_args(sha256: str, request: Optional[str], candidate: Optional[str]) -> list[str]:
    return ["approve", "--sha256", sha256, *(["--request", request] if request else []), *(["--candidate", candidate] if candidate else [])]


def _candidate_target(root: Path, cand: Path) -> ls.Target:
    """候補の承認の対象。候補の錠の中で、掃除が退避した候補を元の名前へ戻してから読む。無ければ `precondition`。"""
    with la.records_lock(cand.parents[1]):
        launch_candidates.restore_aside(cand)
        if not cand.is_file():
            raise internal.InternalError("precondition", f"候補 {cand} が無い。`draft` で候補を作り直す")
        return ls.approval_target(ls.load(root, cand))


def _base_metrics(repo: str, t: ls.Target) -> dict[str, Any]:
    return {"sha256": t.sha256, "approved": la.is_approved(repo, t.sha256), "cleanup_targets": la.cleanup_targets(repo)}


def _show(a: argparse.Namespace) -> dict[str, Any]:
    root = _worktree_root()
    cand = Path(a.candidate).absolute() if a.candidate else None
    repo, target = la.repo_key(root), ls.launch_file(root)
    pending = la.pending_request(repo, target)
    extra = {"next": result.command(TOOL, *_approve_args(*(pending[k] for k in la.CONTINUATION)))} if pending else {}
    session = _session() if a.question else None
    if a.question is None or session is None:
        t = _candidate_target(root, cand) if cand else ls.approval_target(ls.load(root))
        metrics, items = _base_metrics(repo, t), [{"kind": "launch-target", **t.listing}]
        if a.question is None:
            return result.result(TOOL, result.OK, "承認の対象と状態。" + CLEANUP_NOTE, items, metrics, **extra)
        terminal = "cd {} && {}".format(
            shlex.quote(str(root)),
            shlex.join([str(identity.entrypoint(identity.load().root)), TOOL, *_approve_args(t.sha256, None, a.candidate)]),
        )
        summary = (
            "回答を記録できない会話。利用者に端末で metrics.terminal のコマンドを打ってもらい、打ち終えた後に routes.approved で確かめる"
        )
        routes = {"approved": result.command(TOOL, "show", *(["--candidate", str(cand)] if cand else []))}
        return result.result(TOOL, result.GATE, summary, items, {**metrics, "terminal": terminal}, routes=routes, **extra)
    request = uuid.uuid4().hex
    shown: dict[str, ls.Target] = {}

    def make_question() -> dict[str, Any]:
        # 候補は put_question が錠を取り、退避を戻してから読む（読む前に戻さないと、退避した候補を読めない）
        t = shown["t"] = ls.approval_target(ls.load(root, cand))
        text = "\n".join([f"起動の設定を承認しますか（リクエスト {request}）", f"リポジトリ: {repo}", "操作: 承認", *_target_lines(t)])
        options = [
            {
                "label": la.APPROVE_LABEL.format(_approver_name(root)),
                "description": "この内容で起動してよい。承認を記録し、起動の設定へ反映する",
            },
            {"label": la.DENY_LABEL, "description": "記録も反映もしない。起動の設定は変わらない"},
        ]
        question = {"questions": [{"question": text, "header": "起動の承認", "options": options, "multiSelect": False}]}
        return {**question, "sha256": t.sha256}

    asked = dict(request_id=request, repo=repo, sha256="", target=str(target), session=session)
    made = la.put_question(asked, make_question, cand)
    t = shown["t"]
    question = {"questions": made["questions"], "metadata": {"source": SOURCE}}
    items = [{"kind": "launch-target", **t.listing}, {"kind": "question", "input": question}]
    cand_arg = str(cand) if cand else None
    routes = {"approved": result.command(TOOL, *_approve_args(t.sha256, request, cand_arg)), "rejected": None}
    summary = "items の question の input をそのまま AskUserQuestion に渡し、承認なら routes.approved を打つ"
    return result.result(TOOL, result.GATE, summary, items, {**_base_metrics(repo, t), "request": request}, routes=routes, **extra)


def _terminal_line(prompt: str) -> str:
    """端末へ質問を出して 1 行受ける（質問は標準エラーへ出す。標準出力は結果 JSON だけ）。"""
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def _confirm_terminal(t: ls.Target) -> Optional[str]:
    """端末で承認の対象を示し、名前とハッシュの先頭 8 文字を受ける。端末でない・打ち間違い・中断は None。"""
    if not _terminal():
        return None
    print("\n".join(_target_lines(t)), file=sys.stderr)
    try:
        name, head = _terminal_line("承認者の名前: "), _terminal_line(f"ハッシュの先頭 {PREFIX} 文字: ")
    except EOFError:  # noqa: NDF401 利用者の入力: 入力の中断は確認が無いとして呼び手が拒む
        return None
    return name if name and head == t.sha256[:PREFIX] else None


def _before_approve(a: argparse.Namespace, cand: Optional[Path]) -> Optional[dict[str, Any]]:
    """承認の対象を読む前に決まる結果（AI の中・候補が無い打ち直し・「承認しない」の回答）。続けてよければ None。"""
    if _in_ai_child():
        return _refused_config("NDF が起動した AI の中では承認できない。利用者が端末で打つ")
    if cand is not None and not cand.exists():
        if la.reflected(cand, a.sha256, a.request):
            return result.result(TOOL, result.OK, "反映は済んでいる", metrics={"sha256": a.sha256})
        return _refused_config("候補が無く、反映も確かめられない（反映の後に設定が変わったか、候補が別の内容）。draft からやり直す")
    if a.request is not None and la.request_answer(a.request)[0] == "deny":
        return _refused_config("このリクエストには「承認しない」と回答している。記録も反映もしない")
    return None


def _approve(a: argparse.Namespace) -> dict[str, Any]:
    cand = Path(a.candidate).absolute() if a.candidate else None
    early = _before_approve(a, cand)
    if early is not None:
        return early
    root = _worktree_root()
    repo, target = la.repo_key(root), ls.launch_file(root)
    lock = la.records_lock(la.state_file(la.APPROVALS))
    if cand is None:
        with lock:
            la.recover(target)
    t = ls.approval_target(ls.load(root, cand))
    if t.sha256 != a.sha256:
        return _refused_config(f"示した内容（{a.sha256[:12]}…）と今の内容が違う。show で示し直して承認を取り直す")
    needed = (bool(t.listing["unsandboxed"]) or any(t.listing["steps"].values())) and not la.is_approved(repo, t.sha256)
    approver = _confirm_terminal(t) if needed and a.request is None else None
    if needed and a.request is None and approver is None:
        return _refused_config("人の確認が無い。Skill の質問で回答するか、利用者が端末で打って名前とハッシュの先頭 8 文字を入れる")

    def record() -> None:
        if not needed:
            return
        if a.request is None:
            la.write_row(la.state_file(la.APPROVALS), la.approval_row(repo, t.sha256, la.Action.APPROVE, approver))
        elif la.approve_by_answer(repo, t.sha256, a.request) is None:
            raise _NoConsent

    try:
        if cand is None:
            with lock:
                record()
        else:
            done = la.reflect(cand, record, t.sha256, a.request)
            if not done:
                kept = f"。利用者の保存を {done.saved} に残した" if done.saved is not None else ""
                return _refused_config("候補を作った後に設定が別の作業で変わったため置き換えない。draft からやり直す" + kept)
    except _NoConsent:
        return _refused_config(
            "承認の回答が無いか使えない（未回答・承認しない・別の内容・より新しいリクエスト・取り消し・設定の変更）。質問を出し直す"
        )
    summary = ("承認した" if needed else "承認は要らない（許可が広がらないか承認済み）") + ("。候補を反映した" if cand else "")
    return result.result(TOOL, result.OK, summary, metrics={"sha256": t.sha256})


def _draft(a: argparse.Namespace) -> dict[str, Any]:
    if _in_ai_child():
        return _refused_config("NDF が起動した AI の中では起動の設定を書き換えない")
    root = _worktree_root()
    made = la.new_candidate(la.repo_key(root), ls.launch_file(root))
    metrics = {"candidate": str(made.candidate), "cleanup_targets": made.cleanup_targets}
    return result.result(TOOL, result.OK, "候補を作った。直してから show --candidate で示す。" + CLEANUP_NOTE, metrics=metrics)


def _revoke(a: argparse.Namespace) -> dict[str, Any]:
    if _in_ai_child():
        return _refused_config("NDF が起動した AI の中では取り消せない。利用者が端末で打つ")
    la.append(la.repo_key(_worktree_root()), a.sha256, la.Action.REVOKE)
    return result.result(TOOL, result.OK, "承認を取り消した", metrics={"sha256": a.sha256})


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="起動の設定の承認を取り、候補を反映する")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("draft", help="今の設定の写しの候補を作る")
    s = sub.add_parser("show", help="承認の対象と状態を示す")
    s.add_argument("--candidate", help="候補のパス（draft が返したもの）")
    s.add_argument("--question", choices=["approve"], help="承認の質問を作る")
    ap = sub.add_parser("approve", help="人の確認があれば承認を記録し、候補を反映する")
    ap.add_argument("--sha256", required=True, help="示した承認の対象のハッシュ")
    ap.add_argument("--request", help="承認のリクエストの識別子（質問で確認した場合）")
    ap.add_argument("--candidate", help="反映する候補のパス")
    r = sub.add_parser("revoke", help="承認を取り消す")
    r.add_argument("--sha256", required=True, help="取り消す承認の対象のハッシュ")
    return p


def main(argv: list[str]) -> int:
    a = _parser().parse_args(argv)
    if getattr(a, "sha256", None) is not None and not HASH.fullmatch(a.sha256):
        return result.emit(result.stopped(TOOL, "invalid", "--sha256 の値は 64 桁の小文字の 16 進"))
    run = {"draft": _draft, "show": _show, "approve": _approve, "revoke": _revoke}[a.cmd]
    return result.emit(run(a))
