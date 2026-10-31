#!/usr/bin/env python3
"""受け入れ条件の確認の区分を課題の本文へ記録する・読む（#2144。完了判定 `quality-gates` が呼ぶ）。

    python3 acceptance-check.py record --issue <番号> [--repo <所有者>/<リポジトリ>]
                                       [--pre-merge '<条件の番号>=<手段>']... [--post-release <条件の番号>]...
    python3 acceptance-check.py show   --issue <番号> [--repo <所有者>/<リポジトリ>]

記録の置き場は課題の本文の `## 受け入れ条件の確認` の節の表（`| 条件 | 区分 | 手段 |`）である。区分は
`マージ前`（マージ前に確かめた。手段にテスト名か実行したコマンドを書く）か `リリース後`（リリースの後でしか
確かめられない）のどちらか。record は今の表へ渡した番号の行を足すか置き換え、課題ごとの錠の中で本文を読んでから
書く。番号は本文の `## 受け入れ条件` にある番号だけを受ける（規則は lib/acceptance.py）。

この記録を読むのは、承認資料の「未実施のリリース後テスト」（release_lib/materials.read_unverified。マージ前の
条件を載せない）と、sprint-close.py --with-verification の閉じる条件 2（マージ前の条件に合格の行を求めない）である。

結果は lib/step_result.py の形の 1 行の JSON（`tool` は `acceptance-check`）。items は条件ごとに
{kind:"criterion", name:<番号>, result:"マージ前"|"リリース後", means?}。終了コード: 0 = 済んだ（変更なしを含む）/
1 = 本文を書けない / 2 = 本文を読めない・引数の誤り（受け入れ条件に無い番号・手段の無いマージ前・同じ番号を 2 度）/
3 = リポジトリを決められない・本文に受け入れ条件が無い。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md", "mdtable", "locks", "durable", "waits")
import acceptance  # noqa: E402
import gh_call  # noqa: E402
import gh_parts  # noqa: E402
import gh_rest  # noqa: E402
import gh_sections  # noqa: E402
import mdtable  # noqa: E402
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, EXIT_VIOLATION, StepError, emit, main_with, result  # noqa: E402

TOOL = "acceptance-check"


def parse_pre_merge(values) -> dict[int, str]:
    """`<番号>=<手段>` の並びを {番号: 手段} にする。形が違えば StepError（2）。"""
    out: dict[int, str] = {}
    for v in values or ():
        num, sep, means = v.partition("=")
        if not sep or not num.strip().isdigit() or not means.strip():
            raise StepError(f"--pre-merge は '<条件の番号>=<手段>' で渡す: {v!r}", EXIT_UNREADABLE)
        out[int(num)] = means.strip()
    return out


def merged_checks(body: str, pre_merge: dict[int, str], post_release) -> dict[int, acceptance.Check]:
    """本文の今の記録へ渡した区分を重ねた記録。受け入れ条件に無い番号・両方に出る番号は StepError。"""
    numbers = {c.number for c in acceptance.criteria(body)}
    if not numbers:
        raise StepError("本文に受け入れ条件（`## 受け入れ条件` のチェックボックス）が無い", EXIT_PRECONDITION)
    post = set(post_release or ())
    both = sorted(post & set(pre_merge))
    if both:
        raise StepError(f"マージ前とリリース後の両方に渡した番号: {both}", EXIT_UNREADABLE)
    unknown = sorted((post | set(pre_merge)) - numbers)
    if unknown:
        raise StepError(f"受け入れ条件に無い番号: {unknown}（本文の条件は 1〜{max(numbers)}）", EXIT_UNREADABLE)
    checks = dict(acceptance.read_check_kinds(body))
    checks.update({n: acceptance.Check(acceptance.PRE_MERGE, m) for n, m in pre_merge.items()})
    checks.update({n: acceptance.Check(acceptance.POST_RELEASE, "") for n in post})
    return checks


def checks_table(checks: dict[int, acceptance.Check]) -> str:
    """`## 受け入れ条件の確認` の節の中身（番号の順の表）。"""
    rows = [(str(n), c.kind, c.means if c.kind == acceptance.PRE_MERGE else acceptance.NO_MEANS) for n, c in sorted(checks.items())]
    return mdtable.table_markdown(acceptance.CHECKS_HEAD, rows, align=("left", "left", "left"))


def recorded_body(body: str, pre_merge: dict[int, str], post_release) -> str:
    """記録を書いた本文。節の外は変えない。"""
    table = checks_table(merged_checks(body, pre_merge, post_release))
    return gh_sections.replace_section(body, acceptance.CHECKS_HEADING, table)


def _criterion_items(checks: dict[int, acceptance.Check]) -> list[dict]:
    return [
        {"kind": "criterion", "name": str(n), "result": c.kind, **({"means": c.means} if c.means else {})}
        for n, c in sorted(checks.items())
    ]


def _repo_slug(a) -> str:
    slug = gh_call.resolve_repo(a.repo)
    if not slug:
        raise StepError("リポジトリを決められない（--repo を渡す）", EXIT_PRECONDITION)
    return slug


def _fetch_issue_body(slug: str, n: int) -> str:
    body = gh_rest.fetch_body(slug, n)
    if body is None:
        raise StepError(f"{slug}#{n} の本文を読めない", EXIT_UNREADABLE)
    return body


def cmd_record(a):
    pre = parse_pre_merge(a.pre_merge)
    if not pre and not a.post_release:
        raise StepError("--pre-merge か --post-release を 1 つ以上渡す", EXIT_UNREADABLE)
    slug = _repo_slug(a)
    with gh_parts.issue_lock(slug, a.issue):  # 読みから書きまでを課題ごとの錠の中で行う
        body = _fetch_issue_body(slug, a.issue)
        new = recorded_body(body, pre, a.post_release)
        if new != body and not gh_rest.update_body(slug, a.issue, new):
            emit(result(TOOL, "stopped", f"{slug}#{a.issue} の本文を書けない"), EXIT_VIOLATION)
    checks = acceptance.read_check_kinds(new)
    pre_n = sum(c.kind == acceptance.PRE_MERGE for c in checks.values())
    state = "変更なし" if new == body else "記録した"
    emit(result(TOOL, "ok", f"#{a.issue}: {state}（マージ前 {pre_n} 件・リリース後 {len(checks) - pre_n} 件）", _criterion_items(checks)), 0)


def cmd_show(a):
    slug = _repo_slug(a)
    checks = acceptance.read_check_kinds(_fetch_issue_body(slug, a.issue))
    emit(result(TOOL, "ok", f"#{a.issue}: 記録 {len(checks)} 件", _criterion_items(checks)), 0)


def build_parser():
    ap = argparse.ArgumentParser(prog="acceptance-check.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record", help="条件ごとの区分（マージ前 / リリース後）と手段を本文へ書く")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--repo")
    p.add_argument("--pre-merge", action="append", default=[], help="'<条件の番号>=<手段（テスト名・実行したコマンド）>'")
    p.add_argument("--post-release", action="append", type=int, default=[], help="リリースの後でしか確かめられない条件の番号")
    p.set_defaults(func=cmd_record)
    p = sub.add_parser("show", help="本文の記録を読む")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--repo")
    p.set_defaults(func=cmd_show)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
