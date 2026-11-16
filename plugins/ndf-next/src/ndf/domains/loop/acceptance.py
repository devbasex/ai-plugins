"""`ndf loop acceptance` の副命令: 受け入れ条件の確認の区分を課題の本文へ記録する・読む・照らす（#2144・#1682）。

    ndf loop acceptance record --issue <N> [--pre-merge '<条件の番号>=<手段>']... [--post-release <条件の番号>]...
    ndf loop acceptance show   --issue <N>
    ndf loop acceptance check  --issue <N>

- record: 今の表へ渡した番号の行を足すか置き換える（番号は本文の `## 受け入れ条件` にある番号だけを受ける）
- check: 区分の記録の無い受け入れ条件が残れば、条件ごとの欠け（gaps）を付けて incomplete で止まる（受け入れ条件の
  無い課題は照らすものが無いので ok）。実装のプランは
  worker の後にこれを打ち、要求の手動確認（マージ前）を残したまま完了で返らない（#1682）。手動確認を行えなかった
  worker はマージ前の手段を書けないので、その条件は記録の無いまま残り、プランが止まって conductor に見える

本文の読み書きは打った場所（`--cwd`）のリモートの GitHub に対して行う。規則は `acceptance_decide` が持つ。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ndf import cli
from ndf.adapters import git, github
from ndf.contracts import result
from ndf.domains.loop import acceptance_decide as acc

# 結果の tool は親の副命令 `ndf loop` の名前
TOOL = cli.tool_of(f"{__package__}.cli")


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


def _record(a: argparse.Namespace) -> dict[str, Any]:
    pre = parse_pre_merge(a.pre_merge)
    if not pre and not a.post_release:
        raise acc.AcceptanceError("--pre-merge か --post-release を 1 つ以上渡す")
    body = read_body(a.cwd, a.issue)
    new = acc.recorded_body(body, pre, a.post_release)
    if new != body:
        write_body(a.cwd, a.issue, new)
    checks = acc.read_checks(new)
    pre_n = sum(c.kind == acc.PRE_MERGE for c in checks.values())
    state = "変更なし" if new == body else "記録した"
    return result.result(
        TOOL, result.OK, f"#{a.issue}: {state}（マージ前 {pre_n} 件・リリース後 {len(checks) - pre_n} 件）", _items(checks)
    )


def _show(a: argparse.Namespace) -> dict[str, Any]:
    checks = acc.read_checks(read_body(a.cwd, a.issue))
    return result.result(TOOL, result.OK, f"#{a.issue}: 記録 {len(checks)} 件", _items(checks))


def _check(a: argparse.Namespace) -> dict[str, Any]:
    body = read_body(a.cwd, a.issue)
    found = acc.criteria(body)
    if not found:
        return result.result(
            TOOL, result.OK, f"#{a.issue} の本文に受け入れ条件（`## 受け入れ条件` のチェックボックス）が無く、照らすものが無い"
        )
    left = acc.unrecorded(body)
    checks = acc.read_checks(body)
    if not left:
        return result.result(TOOL, result.OK, f"#{a.issue}: 受け入れ条件 {len(found)} 件のすべてに確認の区分がある", _items(checks))
    gaps = [{"kind": "criterion", "name": str(c.number), "reason": f"確認の区分の記録が無い: {c.text}"} for c in left]
    detail = f"#{a.issue}: 受け入れ条件 {len(found)} 件のうち {len(left)} 件に確認の区分の記録が無い（要求の手動確認が残っている）"
    return result.stopped(TOOL, "incomplete", detail, items=_items(checks), gaps=gaps)


def add_parsers(sub: Any) -> None:
    """`ndf loop` の副命令の並びへ `acceptance` を足す。"""
    p = sub.add_parser("acceptance", help="受け入れ条件の確認の区分を記録する・読む・照らす")
    inner = p.add_subparsers(dest="acceptance_command", required=True)
    r = inner.add_parser("record", help="条件ごとの区分（マージ前 / リリース後）と手段を本文へ書く")
    r.set_defaults(handler=_record)
    r.add_argument("--pre-merge", action="append", default=[], help="'<条件の番号>=<手段（テスト名・実行したコマンド）>'")
    r.add_argument("--post-release", action="append", type=int, default=[], help="リリースの後でしか確かめられない条件の番号")
    inner.add_parser("show", help="本文の記録を読む").set_defaults(handler=_show)
    inner.add_parser("check", help="記録の無い受け入れ条件があれば incomplete で止まる").set_defaults(handler=_check)
    for name in ("record", "show", "check"):
        inner.choices[name].add_argument("--issue", type=int, required=True)
