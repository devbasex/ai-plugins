"""`ndf loop acceptance` のサブコマンド: 受け入れ条件の確認の区分を課題の本文へ記録する・読む・照らす（#2144・#1682）。

    ndf loop acceptance record --issue <N> [--pre-merge '<条件の番号>=<手段>']... [--post-release <条件の番号>]...
    ndf loop acceptance show   --issue <N>
    ndf loop acceptance check  --issue <N>... [--criteria '<課題>:<条件の番号>']...

- record: 今の表へ渡した番号の行を足すか置き換える（番号は本文の `## 受け入れ条件` にある番号だけを受ける）。
  課題ごとのロック（Git の共通ディレクトリの下）の中で読み・書き・読み直し、消されていれば 3 回まで当て直す（#3879）
- check: 区分の記録の無い受け入れ条件が残れば、条件ごとの欠け（gaps）を付けて incomplete で止まる（受け入れ条件の
  無い課題は照らすものが無いので ok）。`--issue` を繰り返すと課題ごとに照らしてまとめ、`--criteria` に組のある課題は
  その条件だけを照らす（レビューの単位が満たす条件だけを見る、#3879）。実装のプランは
  worker の後にこれを打ち、要求の手動確認（マージ前）を残したまま完了で返らない（#1682）。手動確認を行えなかった
  worker はマージ前の手段を書けないので、その条件は記録の無いまま残り、プランが止まって conductor に見える

本文の読み書きは打った場所（`--cwd`）のリモートの GitHub に対して行う。規則は `acceptance_decide` が持つ。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from filelock import FileLock

from ndf import cli, identity
from ndf.adapters import git, github
from ndf.contracts import limits, result
from ndf.domains.loop import acceptance_decide as acc

# 結果の tool は親のサブコマンド `ndf loop` の名前
TOOL = cli.tool_of(f"{__package__}.cli")
# 書いた後の読み直しで自分の記録がそろわないときに当て直す回数
REAPPLY_LIMIT = 3


def _hub(cwd: Path) -> github.GitHub:
    return github.GitHub(git.Git(cwd).remote_repo(), github.client())


def read_body(cwd: Path, issue: int) -> str:
    """課題の本文（GitHub から読む）。テストが差し替える。"""
    return _hub(cwd).issue_body(issue).body


def write_body(cwd: Path, issue: int, body: str) -> None:
    """課題の本文を書き換える。テストが差し替える。"""
    hub = _hub(cwd)
    hub.rest("PATCH", f"/repos/{hub.repo}/issues/{int(issue)}", {"body": body})


def _items(checks: dict[int, acc.Check]) -> list[dict[str, Any]]:
    return [
        {"kind": "criterion", "name": str(n), "result": c.kind, **({"means": c.means} if c.means else {})}
        for n, c in sorted(checks.items())
    ]


def parse_pre_merge(values: list[str]) -> dict[int, str]:
    """`<番号>=<手段>` の並びを {番号: 手段} にする。形が違えば `AcceptanceError`。"""
    out: dict[int, str] = {}
    for v in values:
        num, sep, means = v.partition("=")
        if not sep or not num.strip().isdigit() or not means.strip():
            raise acc.AcceptanceError(f"--pre-merge は '<条件の番号>=<手段>' で渡す: {v!r}")
        out[int(num)] = means.strip()
    return out


def lock_path(cwd: Path, issue: int) -> Path:
    """課題ごとの記録のロック。Git の共通ディレクトリの下に置き、同じリポジトリの worktree のすべてが共有する。"""
    return git.Git(cwd).common_dir() / identity.load().name / f"acceptance-{int(issue)}.lock"


def _unsettled(body: str, want: dict[int, acc.Check | None]) -> list[int]:
    """`want` の条件のうち、本文の記録がそろっていない番号。`want` は書く本文の表から読み戻した値で、手段の表記の
    変換（リテラルの `<br>` が改行に戻るなど）があっても読み戻し同士で比べる（#3886 の r1-1）。"""
    have = acc.read_checks(body)
    return [n for n, c in sorted(want.items()) if have.get(n) != c]


def _reapply(cwd: Path, issue: int, body: str, want: dict[int, acc.Check | None], pre: dict[int, str], post: list[int]) -> tuple[str, bool]:
    """`want` の記録が本文にそろうまで書いて読み直す（最初の 1 回と当て直し `REAPPLY_LIMIT` 回まで）。
    最後に読んだ本文と、1 度でも書いたかを返す。ロックは呼び手が持つ。"""
    current, wrote = body, False
    for _ in range(1 + REAPPLY_LIMIT):
        if not _unsettled(current, want):
            break
        write_body(cwd, issue, acc.recorded_body(current, pre, post))
        wrote = True
        current = read_body(cwd, issue)
    return current, wrote


def _record(a: argparse.Namespace) -> dict[str, Any]:
    pre = parse_pre_merge(a.pre_merge)
    if not pre and not a.post_release:
        raise acc.AcceptanceError("--pre-merge か --post-release を 1 つ以上渡す")
    path = lock_path(a.cwd, a.issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 読む・書く・読み直すを課題ごとに直列にする。取れなければ Timeout（store_in_use）で何も書かない（#3879）
    with FileLock(str(path), timeout=limits.seconds("locks.append")):
        body = read_body(a.cwd, a.issue)
        # 今回書く行だけを単独で読み戻す。本文の後続の表に同じ番号の古い行があっても、それで入力の誤りを隠さない（#3886 の r3-1）
        want = acc.recorded_rows(body, pre, a.post_release)
        # 読み戻せない行（手段が `—` で手段なしと読まれるなど）は、記録が無いまま ok で返らないよう止める（#3886 の r2-1）
        lost = sorted(n for n, c in want.items() if c is None)
        if lost:
            raise acc.AcceptanceError(f"条件 {lost} の記録が表から読み戻せない（手段に `—` だけを渡していないか）")
        current, wrote = _reapply(a.cwd, a.issue, body, want, pre, a.post_release)
        left = _unsettled(current, want)
    checks = acc.read_checks(current)
    if left:
        # ロックの外の書き手が古い本文で上書きし続けた
        gaps = [{"kind": "criterion", "name": f"{a.issue}:{n}", "reason": "書いた記録が本文から消され続けた"} for n in left]
        detail = (
            f"#{a.issue}: 当て直しを {REAPPLY_LIMIT} 回しても条件 {left} の記録が本文にそろわない（ロックの外の書き手が上書きしている）"
        )
        return result.stopped(TOOL, "incomplete", detail, items=_items(checks), gaps=gaps)
    pre_n = sum(c.kind == acc.PRE_MERGE for c in checks.values())
    state = "記録した" if wrote else "変更なし"
    return result.result(
        TOOL, result.OK, f"#{a.issue}: {state}（マージ前 {pre_n} 件・リリース後 {len(checks) - pre_n} 件）", _items(checks)
    )


def _show(a: argparse.Namespace) -> dict[str, Any]:
    checks = acc.read_checks(read_body(a.cwd, a.issue))
    return result.result(TOOL, result.OK, f"#{a.issue}: 記録 {len(checks)} 件", _items(checks))


def parse_criteria(values: list[str], issues: list[int]) -> dict[int, set[int]]:
    """`<課題>:<条件>` の並びを {課題: {条件の番号}} にする。形が違うか渡した課題に無ければ `AcceptanceError`。"""
    out: dict[int, set[int]] = {}
    for v in values:
        issue, sep, num = v.partition(":")
        if not sep or not issue.strip().isdigit() or not num.strip().isdigit():
            raise acc.AcceptanceError(f"--criteria は '<課題>:<条件の番号>' で渡す: {v!r}")
        if int(issue) not in issues:
            raise acc.AcceptanceError(f"--criteria の課題 #{int(issue)} は --issue に無い: {v!r}")
        out.setdefault(int(issue), set()).add(int(num))
    return out


def _check_one(cwd: Path, issue: int, wanted: set[int]) -> tuple[list[acc.Criterion], list[acc.Criterion], dict[int, acc.Check]]:
    """1 つの課題の (照らす条件, 記録の無い条件, 記録)。`wanted` が空なら本文の条件のすべてを照らし、
    空でなければ 3 つとも `wanted` の番号に絞る。"""
    body = read_body(cwd, issue)
    found = acc.criteria(body)
    missing = sorted(wanted - {c.number for c in found})
    if missing:
        raise acc.AcceptanceError(f"--criteria の条件 {missing} は #{issue} の本文の受け入れ条件に無い")
    left = acc.unrecorded(body)
    checks = acc.read_checks(body)
    if wanted:
        found = [c for c in found if c.number in wanted]
        left = [c for c in left if c.number in wanted]
        checks = {n: c for n, c in checks.items() if n in wanted}
    return found, left, checks


def _check(a: argparse.Namespace) -> dict[str, Any]:
    wanted = parse_criteria(a.criteria, a.issue)
    # 課題 1 つ・--criteria なしは今の形（名前は条件の番号だけ）。それ以外は名前に課題を付ける
    prefixed = len(a.issue) > 1 or bool(wanted)
    found: list[acc.Criterion] = []
    gaps: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for issue in a.issue:
        each, left, checks = _check_one(a.cwd, issue, wanted.get(issue, set()))  # noqa: NDF502 利用者の入力: --criteria を渡さなかった課題は条件で絞らない
        tag = f"{issue}:" if prefixed else ""
        found += each
        gaps += [{"kind": "criterion", "name": f"{tag}{c.number}", "reason": f"確認の区分の記録が無い: {c.text}"} for c in left]
        items += [{**i, "name": f"{tag}{i['name']}"} for i in _items(checks)]
    where = ", ".join(f"#{n}" for n in a.issue)
    if not found:
        return result.result(
            TOOL, result.OK, f"{where} の本文に受け入れ条件（`## 受け入れ条件` のチェックボックス）が無く、照らすものが無い"
        )
    if not gaps:
        return result.result(TOOL, result.OK, f"{where}: 受け入れ条件 {len(found)} 件のすべてに確認の区分がある", items)
    detail = f"{where}: 受け入れ条件 {len(found)} 件のうち {len(gaps)} 件に確認の区分の記録が無い（要求の手動確認が残っている）"
    return result.stopped(TOOL, "incomplete", detail, items=items, gaps=gaps)


def add_parsers(sub: Any) -> None:
    """`ndf loop` のサブコマンドの並びへ `acceptance` を足す。"""
    p = sub.add_parser("acceptance", help="受け入れ条件の確認の区分を記録する・読む・照らす")
    inner = p.add_subparsers(dest="acceptance_command", required=True)
    r = inner.add_parser("record", help="条件ごとの区分（マージ前 / リリース後）と手段を本文へ書く")
    r.set_defaults(handler=_record)
    r.add_argument("--pre-merge", action="append", default=[], help="'<条件の番号>=<手段（テスト名・実行したコマンド）>'")
    r.add_argument("--post-release", action="append", type=int, default=[], help="リリースの後でしか確かめられない条件の番号")
    inner.add_parser("show", help="本文の記録を読む").set_defaults(handler=_show)
    c = inner.add_parser("check", help="記録の無い受け入れ条件があれば incomplete で止まる")
    c.set_defaults(handler=_check)
    c.add_argument("--issue", action="append", type=int, required=True, help="照らす課題（繰り返せる）")
    c.add_argument("--criteria", action="append", default=[], help="'<課題>:<条件の番号>'。その課題はこの条件だけを照らす")
    for name in ("record", "show"):
        inner.choices[name].add_argument("--issue", type=int, required=True)
