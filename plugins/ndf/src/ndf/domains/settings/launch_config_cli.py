"""サブコマンド `ndf launch-config`: 起動の設定（`.ndf/launch.json`）の承認を取り、候補を反映する（#1959）。

    ndf launch-config draft                                     候補（今の設定の写し）を作り、パスを返す
    ndf launch-config show [--candidate <候補>] [--question approve]
    ndf launch-config approve --sha256 <値> [--request <リクエスト>] [--candidate <候補>]
    ndf launch-config revoke --sha256 <値>

承認を確定するのは人だけで、確認は端末での入力（名前とハッシュの先頭 8 文字）か、`show --question approve` の質問への回答
（hook `launch-answer` が記録する）である。NDF が起動した AI の中（`NDF_AI_CHILD`）では draft・approve を拒み、revoke は
回答を記録しない会話（`launch_records.unattended`）でも拒む。候補の反映（設定の書き換え）には、承認の対象が空か承認済みでも毎回人の確認が要る。
承認の行を足すのは許可が広がり未承認のときだけである。作業場所はカレントディレクトリから `.git` を持つ親をたどって決める。
終了コード: 0 = ok / 10 = 承認の質問か端末のコマンドを返した（gate）/ 停止理由の表のとおり（refused・precondition・invalid）。
"""

from __future__ import annotations

import argparse
import functools
import getpass
import json
import os
import re
import shlex
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf import cli, identity
from ndf.adapters import git, launch_approval as la, launch_candidates, launch_records, launch_settings as ls
from ndf.adapters.secret_forms import holds_secret
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
# 比べる 2 つの設定の片方に無いキーの値
_ABSENT = object()
# 確認の画面で、今の設定にある秘密の形の値とキーの代わりに示す文
MASKED = "（秘密の形の値。表示しない）"


class _NoConsent(Exception):
    """承認の回答が使えない。"""


class _Refused(Exception):
    """候補がこの作業場所の反映先のものでない・照らした後に変わったなど、承認の手順の前提を欠く。文は利用者に示す理由。"""


class _Revoked(Exception):
    """承認済みとして進めた内容の承認が、記録の錠を取るまでに取り消された（人の確認が要るようになった）。"""


def _candidate_path(given: Optional[str]) -> Optional[Path]:
    """`--candidate` の絶対パス。`..` を畳む（端末のコマンドの `cd` の後も、候補の記録と同じパスを指すため）。"""
    return Path(os.path.abspath(given)) if given else None


def _worktree_root() -> Path:
    return ls.launch_root(Path.cwd())


def _in_ai_child() -> bool:
    return bool(os.environ.get(identity.env_name("ai-child")))


def _answer_session() -> Optional[str]:
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


def _reading_recordable(root: Path) -> bool:
    """読み手役の確認を記録できるか。`ndf records reading add` は GitHub のリモートの `<所有者>/<リポジトリ>`
    （`Git.remote_repo`）で記録するため、それを求められない作業場所では質問の前の記録を作れない（質問ではなく端末のコマンドを返す）。"""
    try:
        git.Git(root).remote_repo()
    except (git.GitError, OSError):  # noqa: NDF401 外部コマンド: 識別子を求められなければ質問の前の記録を作れない
        return False
    return True


def _widens(t: ls.Target) -> bool:
    """承認の対象が許可を広げるか（空でなければ人の承認が要る）。"""
    return bool(t.listing["unsandboxed"]) or any(t.listing["steps"].values())


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


def experiment_changes(root: Path, cand: Optional[Path]) -> list[dict[str, Any]]:
    """候補が今の `.ndf/launch.json` から変える `experiments` の値（モデルと effort の選択肢・抽選の切り替え・割り付けの値）。

    `experiments` は承認のハッシュに入らないが、反映すると worker・Skill・判定の起動のモデルと effort が変わるので、確認の画面に
    示す（#2944 の r1-1）。要素は `{path, before, after}` で、書いていない側は欄を省く（同梱の値を使う）。今の設定を読めなければ
    空の設定と比べる。候補が無ければ空。候補の `experiments` の値かキーに秘密の形（`holds_secret`）があれば、値を出さずに
    `_Refused` で拒む（反映後に秘密が残る）。今の設定だけにある秘密の形の値とキーは伏せて示す。候補がその値を消す変更を、
    同じ拒否で止めないためである（#2944 の r7-3）。
    """
    if cand is None:
        return []
    after = ls.load(root, cand).experiments
    if _has_secret(after):
        raise _Refused("候補の experiments に秘密の形の値がある（値は表示しない）。秘密を書かずに候補を直してから示し直す")
    try:
        before = ls.load(root).experiments
    except internal.InternalError:  # ファイル: 壊れた今の設定は候補が直す。変更は空の設定からの差として示す
        before = {}
    changes = _changes(before, after, "experiments")
    return [{**c, "before": MASKED} if "before" in c and _has_secret(c["before"]) else c for c in changes]


def experiments_sha(data: bytes) -> Optional[str]:
    """候補のバイト列の `experiments`（書いていなければ空）のハッシュ。JSON のオブジェクトでなければ None。"""
    try:
        raw = json.loads(data)
    except ValueError:  # noqa: NDF401 利用者の入力: 読めない候補は、示した内容と一致しないとして拒む
        return None
    return launch_records.canonical_sha(raw["experiments"] if isinstance(raw, dict) and "experiments" in raw else {}) if isinstance(raw, dict) else None


def asked_experiments(request_id: str) -> Optional[str]:
    """リクエストの質問を作ったときに示した `experiments` のハッシュ（質問の記録の最後の行）。無ければ None。"""
    key = la.CONTINUATION[1]  # リクエストの識別子の欄
    found = [r for r in la.records.whole_rows(la.state_file(la.QUESTIONS)) if key in r and r[key] == request_id]
    return found[-1]["experiments_sha"] if found and "experiments_sha" in found[-1] else None


def _has_secret(value: Any) -> bool:
    """値の中のキーと文字列のどれかが秘密の形か。1 つずつ当てる（JSON にするとエスケープで語の境が変わり、見逃すため。r7-2）。"""
    if isinstance(value, str):
        return holds_secret(value)
    if isinstance(value, Mapping):
        return any(holds_secret(str(k)) or _has_secret(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_secret(v) for v in value)
    return False


def _changes(before: Any, after: Any, at: str) -> list[dict[str, Any]]:
    """2 つの値の違う葉。オブジェクトはキーごとに下り（片方に無いオブジェクトも下る）、配列（選択肢の並び）はまとめて比べる。"""
    if _descends(before) and _descends(after) and (before is not _ABSENT or after is not _ABSENT):
        keys = dict.fromkeys([*_keys(before), *_keys(after)])
        # 秘密の形のキーは今の設定にだけある（候補は呼び手が拒む）ので、場所の文では伏せる
        return [c for k in keys for c in _changes(_value(before, k), _value(after, k), f"{at}/{MASKED if holds_secret(k) else k}")]
    if before == after:
        return []
    sides = {"before": before, "after": after}
    return [{"path": at, **{k: v for k, v in sides.items() if v is not _ABSENT}}]


def _descends(value: Any) -> bool:
    return value is _ABSENT or isinstance(value, Mapping)


def _keys(value: Any) -> list[str]:
    return [] if value is _ABSENT else list(value)


def _value(obj: Any, key: str) -> Any:
    return obj[key] if obj is not _ABSENT and key in obj else _ABSENT


def _change_lines(changes: Sequence[Mapping[str, Any]], whole: bool = False) -> list[str]:
    """確認の画面に載せる、モデルと effort の変更の行。書いていない側は同梱の値を使う。

    質問の文（`whole` が偽）は、変わる場所と要点（選択肢の組の数か、1 つの値の前後）だけを載せ、選択肢の全体は結果の
    `items` の `launch-experiments` に任せる（一度で読み切れる量にする）。端末の確認（`whole` が真）は値の全体を載せる。
    """
    return [f"モデルと effort の変更 {c['path']}: {_gist(c, 'before', whole)} → {_gist(c, 'after', whole)}" for c in changes]


def _gist(change: Mapping[str, Any], key: str, whole: bool) -> str:
    if key not in change:
        return "書かない（同梱の値）"
    value = change[key]
    if whole or not isinstance(value, (list, Mapping)):
        return json.dumps(value, ensure_ascii=False)
    count = f"{len(value)} 組の選択肢" if isinstance(value, list) else "値"
    return f"{count}（全体は結果の items の launch-experiments）"


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
    return {
        "sha256": t.sha256,
        "required": _widens(t),
        "approved": la.is_approved(repo, t.sha256),
        "cleanup_targets": la.cleanup_targets(repo),
    }


def _check_destination(cand: Path, target: Path) -> None:
    """候補の記録が持つ反映先が、この作業場所の `.ndf/launch.json` か。違えば `_Refused`。"""
    found = la.candidate_destination(cand)
    if found != str(target):
        raise _Refused(f"候補の反映先（{found}）が、この作業場所の {target} と違う。この作業場所で draft から作り直す")


def _show(a: argparse.Namespace) -> dict[str, Any]:
    root = _worktree_root()
    cand = _candidate_path(a.candidate)
    repo, target = la.repo_key(root), ls.launch_file(root)
    pending = la.pending_request(repo, target)
    extra = {"next": result.command(TOOL, *_approve_args(*(pending[k] for k in la.CONTINUATION)))} if pending else {}
    try:
        return _show_result(a, root, cand, repo, target, extra)
    except _Refused as exc:
        return result.stopped(TOOL, "refused", str(exc), **extra)
    except internal.InternalError as exc:
        if not pending:
            raise
        # 回答済みの続きは、今の設定や MCP の定義を読めなくても返す（壊れた設定を直す候補への回答を失わない）
        return result.stopped(TOOL, exc.kind, f"{exc}。回答済みで反映が済んでいない承認があるので、先に next のコマンドを打つ", **extra)


def _show_result(a: argparse.Namespace, root: Path, cand: Optional[Path], repo: str, target: Path, extra: dict[str, Any]) -> dict[str, Any]:
    """`show` の結果（続きのコマンド `extra` を添える）。設定を読めなければ `InternalError`。"""
    session = _answer_session() if a.question else None
    if session is not None and not _reading_recordable(root):
        session = None
    if a.question is None or session is None:
        return _status_or_terminal(a, root, cand, repo, target, extra)
    request = uuid.uuid4().hex
    shown: dict[str, ls.Target] = {}

    def make_question() -> dict[str, Any]:
        # 候補は put_question が錠を取り、退避を戻してから読む（読む前に戻さないと、退避した候補を読めない）
        if cand is not None:
            _check_destination(cand, target)
        t = shown["t"] = ls.approval_target(ls.load(root, cand))
        changes = shown["changes"] = experiment_changes(root, cand)
        if cand is not None:  # 示した experiments をリクエストに残し、approve が反映する候補と照らす（r7-1）
            asked["experiments_sha"] = experiments_sha(cand.read_bytes())
        if cand is not None:
            head = f"この作業場所の {target} を候補の内容で置き換え、以下の起動時の許可を反映してよいですか"
            effect = "この内容で起動してよい。承認を記録し、候補の内容で起動の設定を置き換える"
        else:
            head = f"この作業場所の {target} にある、以下の起動時の許可を承認してよいですか（設定ファイルは変えません）"
            effect = "この内容で起動してよい。承認を記録する。設定ファイルは変えない"
        # 識別子は、同じ内容の質問どうしを回答の記録で見分けるために入れる
        text = "\n".join([head, *_target_lines(t), *_change_lines(changes), f"質問を見分けるリクエストの識別子: {request}"])
        options = [
            {"label": la.APPROVE_LABEL.format(_approver_name(root)), "description": effect},
            {"label": la.DENY_LABEL, "description": "記録も反映もしない。起動の設定は変わらない"},
        ]
        question = {"questions": [{"question": text, "header": "起動の承認", "options": options, "multiSelect": False}]}
        return {**question, "sha256": t.sha256}

    asked = dict(request_id=request, repo=repo, sha256="", target=str(target), session=session)
    made = la.put_question(asked, make_question, cand)
    t = shown["t"]
    question = {"questions": made["questions"], "metadata": {"source": SOURCE}}
    items = [{"kind": "launch-target", **t.listing}, *_experiment_items(shown["changes"]), {"kind": "question", "input": question}]
    cand_arg = str(cand) if cand else None
    routes = {"approved": result.command(TOOL, *_approve_args(t.sha256, request, cand_arg)), "rejected": None}
    summary = "items の question の input をそのまま AskUserQuestion に渡し、承認なら routes.approved を打つ"
    return result.result(TOOL, result.GATE, summary, items, {**_base_metrics(repo, t), "request": request}, routes=routes, **extra)


def _status_or_terminal(
    a: argparse.Namespace, root: Path, cand: Optional[Path], repo: str, target: Path, extra: dict[str, Any]
) -> dict[str, Any]:
    """質問を作らない `show` の結果（状態か、回答を記録できない会話の端末のコマンド）。"""
    # 候補を指定したときは、その候補を反映した完了の記録があり、反映先（と残っていれば候補）が今もその中身かを示す
    reflected = la.reflected_candidate(cand, target) if cand else None
    if reflected:  # 照らした反映先の設定から許可の状態を示す。片付けた候補は戻さない
        t = ls.approval_target(ls.load(root))
    else:
        t = _candidate_target(root, cand) if cand else ls.approval_target(ls.load(root))
    metrics = {**_base_metrics(repo, t), **({"reflected": reflected} if cand else {})}
    items = [{"kind": "launch-target", **t.listing}, *_experiment_items(experiment_changes(root, cand) if cand and not reflected else [])]
    if a.question is None:
        return result.result(TOOL, result.OK, "承認の対象と状態。" + CLEANUP_NOTE, items, metrics, **extra)
    if cand is not None:
        _check_destination(cand, target)  # 端末のコマンドを返す前に、反映先がこの作業場所かを確かめる
    terminal = "cd {} && {}".format(
        shlex.quote(str(root)),
        shlex.join([str(identity.entrypoint(identity.load().root)), TOOL, *_approve_args(t.sha256, None, str(cand) if cand else None)]),
    )
    summary = (
        "回答を記録できない会話。利用者に端末で metrics.terminal のコマンドを打ってもらい、打ち終えた後に routes.approved で確かめる。"
        "その結果の metrics.reflected が真なら設定へ反映済み（候補を指定したとき）。metrics.required が真なら、"
        "その結果の metrics.approved が真のときに起動の許可も記録済み"
    )
    routes = {"approved": result.command(TOOL, "show", *(["--candidate", str(cand)] if cand else []))}
    return result.result(TOOL, result.GATE, summary, items, {**metrics, "terminal": terminal}, routes=routes, **extra)


def _experiment_items(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`experiments` の変更の項目（変更が無ければ置かない）。"""
    return [{"kind": "launch-experiments", "changes": changes}] if changes else []


def _terminal_line(prompt: str) -> str:
    """端末へ質問を出して 1 行受ける（質問は標準エラーへ出す。標準出力は結果 JSON だけ）。"""
    print(prompt, end="", file=sys.stderr, flush=True)
    return input().strip()


def _confirm_terminal(t: ls.Target, destination: Optional[Path], changes: Sequence[Mapping[str, Any]] = ()) -> Optional[str]:
    """端末で承認の対象（候補を反映するなら反映先のパスとモデルと effort の変更も）を示し、名前とハッシュの先頭 8 文字を受ける。
    端末でない・打ち間違い・中断は None。"""
    if not _terminal():
        return None
    head = [f"候補の内容で {destination} を置き換える"] if destination is not None else []
    print("\n".join([*head, *_target_lines(t), *_change_lines(changes, whole=True)]), file=sys.stderr)
    try:
        name, typed = _terminal_line("承認者の名前: "), _terminal_line(f"ハッシュの先頭 {PREFIX} 文字: ")
    except EOFError:  # noqa: NDF401 利用者の入力: 入力の中断は確認が無いとして呼び手が拒む
        return None
    return name if name and typed == t.sha256[:PREFIX] else None


def _checked_target(root: Path, cand: Path) -> tuple[ls.Target, bytes]:
    """候補を 1 回読んだバイト列と、そのバイト列から求めた承認の対象。照らしたバイト列をそのまま反映に使うため、写しを検証する。"""
    try:
        data = cand.read_bytes()
    except FileNotFoundError:
        raise internal.InternalError("precondition", f"候補 {cand} が無い。`draft` で候補を作り直す") from None
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / cand.name
        copy.write_bytes(data)
        try:
            return ls.approval_target(ls.load(root, copy)), data
        except internal.InternalError as exc:
            raise internal.InternalError(exc.kind, str(exc).replace(str(copy), str(cand))) from None


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
    cand = _candidate_path(a.candidate)
    early = _before_approve(a, cand)
    if early is not None:
        return early
    root = _worktree_root()
    repo, target = la.repo_key(root), ls.launch_file(root)
    try:
        t, data = _approval_input(root, cand, target)
    except _Refused as exc:
        return _refused_config(str(exc))
    if t.sha256 != a.sha256:
        return _refused_config(f"示した内容（{a.sha256[:12]}…）と今の内容が違う。show で示し直して承認を取り直す")
    if a.request is not None and cand is not None and asked_experiments(a.request) != experiments_sha(data):
        return _refused_config("質問で示したモデルと effort の選択肢（experiments）と、反映する候補の内容が違う。show で示し直して承認を取り直す")
    needed = _widens(t) and not la.is_approved(repo, t.sha256)
    # 承認の行を足す条件（起動の許可）と、反映を許す条件（今回の書き換えへの同意）は別に判定する。書き換えは毎回人に確かめる
    asks = needed or cand is not None
    try:
        shown = (target, experiment_changes(root, cand)) if cand else (None, [])
    except _Refused as exc:
        return _refused_config(str(exc))
    approver = _confirm_terminal(t, *shown) if asks and a.request is None else None
    if asks and a.request is None and approver is None:
        return _refused_config("人の確認が無い。Skill の質問で回答するか、利用者が端末で打って名前とハッシュの先頭 8 文字を入れる")
    record = functools.partial(_record_approval, a.request, repo, t, target, cand, approver)
    try:
        refused = _approve_and_place(cand, record, t, a.request, data, target)
    except _Revoked:
        return _refused_config("反映の直前に、この内容の承認が取り消された。記録も反映もしない。show で示し直して承認を取り直す")
    except _NoConsent:
        return _refused_config(
            "承認の回答が無いか使えない（未回答・承認しない・別の内容か候補か反映先・より新しいリクエスト・取り消し・設定の変更）。質問を出し直す"
        )
    if refused is not None:
        return _refused_config(refused)
    summary = ("承認した" if needed else "承認の記録は要らない（許可が広がらないか承認済み）") + ("。候補を反映した" if cand else "")
    return result.result(TOOL, result.OK, summary, metrics={"sha256": t.sha256})


def _approval_input(root: Path, cand: Optional[Path], target: Path) -> tuple[ls.Target, bytes]:
    """承認の対象と、反映する候補のバイト列（候補が無ければ空）。候補の反映先がこの作業場所でなければ `_Refused`。"""
    if cand is None:
        with la.records_lock(la.state_file(la.APPROVALS)):
            la.recover(target)
        return ls.approval_target(ls.load(root)), b""
    _check_destination(cand, target)
    return _checked_target(root, cand)


def _record_approval(request: Optional[str], repo: str, t: ls.Target, target: Path, cand: Optional[Path], approver: Optional[str]) -> None:
    """承認の記録の錠の中で呼ばれる。回答はこの操作（反映先・候補・内容）への同意かを錠の中で照らし、錠の外で見た「承認済み」も照らし直す。"""
    if request is not None and la.consent_by_answer(request, repo, t.sha256, target, cand) is None:
        raise _NoConsent
    if not _widens(t) or la.is_approved(repo, t.sha256):
        return
    if request is not None:
        if la.approve_by_answer(repo, t.sha256, request) is None:
            raise _NoConsent
    elif approver is not None:
        la.write_row(la.state_file(la.APPROVALS), la.approval_row(repo, t.sha256, la.Action.APPROVE, approver))
    else:
        raise _Revoked


def _approve_and_place(cand: Optional[Path], record: Any, t: ls.Target, request: Optional[str], data: bytes, target: Path) -> Optional[str]:
    """承認を記録し、候補があれば反映する。拒んだら利用者に示す文、済めば None。"""
    if cand is None:
        with la.records_lock(la.state_file(la.APPROVALS)):
            record()
        return None
    done = la.reflect(cand, record, t.sha256, request, data=data, expected=target)
    if done.refusal is not None:
        return "反映しない。" + done.refusal
    if not done:
        kept = f"。利用者の保存を {done.saved} に残した" if done.saved is not None else ""
        return "候補を作った後に設定が別の作業で変わったため置き換えない。draft からやり直す" + kept
    return None


def _draft(a: argparse.Namespace) -> dict[str, Any]:
    if _in_ai_child():
        return _refused_config("NDF が起動した AI の中では起動の設定を書き換えない")
    root = _worktree_root()
    made = la.new_candidate(la.repo_key(root), ls.launch_file(root))
    metrics = {"candidate": str(made.candidate), "cleanup_targets": made.cleanup_targets}
    return result.result(
        TOOL, result.OK, "候補を作った。直してから show --question approve --candidate で示す。" + CLEANUP_NOTE, metrics=metrics
    )


def _revoke(a: argparse.Namespace) -> dict[str, Any]:
    if launch_records.unattended(os.environ):
        return _refused_config(
            "回答を記録しない会話（NDF が起動した AI・人のいない実行・計画や外のオーガナイザーのレーン）では取り消せない。利用者が端末で打つ"
        )
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
