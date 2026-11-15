"""副命令 `ndf statusline`: statusline の導入・切り替え・復元（#3569。旧い `statusline-switch.sh` の移し先）。

    ndf statusline ensure    # SessionStart 向け。statusLine が無ければ NDF の表示を設定し、NDF の表示なら本体を今の版に置き直す
    ndf statusline status    # 今の statusLine と控えの有無
    ndf statusline set       # 今の値を控えて NDF の表示へ切り替える
    ndf statusline restore   # 控えから元の値へ戻す（控えが無く NDF の表示なら statusLine を消す）

結果は 1 行の結果 JSON で出す（形は contracts/schemas/io/statusline.result.schema.json）。`ensure` は SessionStart を
止めないため、排他を待ちきれない・`settings.json` を読めないときも `skipped` で終了コード 0 を返す。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable

from ndf import cli
from ndf.contracts import limits, result
from ndf.domains.relay import signals
from ndf.domains.statusline import settings

TOOL = cli.tool_of(__name__)
LOCK_LIMIT = "statusline.lock"


def _item(sw: settings.Switched) -> dict[str, object]:
    item: dict[str, object] = {"action": sw.action, "message": sw.message, "backup": sw.backup is not None}
    if sw.current is not None:
        item["current"] = sw.current
    return item


def _ok(sw: settings.Switched, warnings: list[str] | None = None) -> int:
    return result.emit(result.result(TOOL, result.OK, sw.message, items=[_item(sw)], warnings=warnings))


def _skipped(reason: str) -> int:
    sw = settings.Switched("skipped", f"statusline を確かめなかった: {reason}", None, None)
    return _ok(sw, warnings=[reason])


def _locked(p: settings.Paths, run: Callable[[settings.Paths], settings.Switched]) -> settings.Switched:
    """排他の中で `run` を打つ。待ちきれなければ `StatuslineError`（store_in_use）。"""
    p.lock.parent.mkdir(parents=True, exist_ok=True)
    with signals.held(p.lock, limits.seconds(LOCK_LIMIT)) as got:
        if not got:
            raise settings.StatuslineError(
                "store_in_use", f"排他 {p.lock} を待ちきれない（別のセッションか旧い statusline-switch.sh が持つ）"
            )
        return run(p)


def _ensure(p: settings.Paths) -> int:
    try:
        return _ok(_locked(p, settings.ensure))
    except settings.StatuslineError as exc:
        # SessionStart を止めないため、読めない・書けない・待ちきれないは何もせずに通す
        return _skipped(exc.detail)


def _run(name: str, p: settings.Paths) -> int:
    handlers: dict[str, Callable[[settings.Paths], settings.Switched]] = {
        "status": settings.status,
        "set": settings.set_ndf,
        "restore": settings.restore,
    }
    try:
        sw = handlers[name](p) if name == "status" else _locked(p, handlers[name])
    except settings.StatuslineError as exc:
        return result.emit(result.stopped(TOOL, exc.kind, exc.detail))
    return _ok(sw)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="statusline の導入・切り替え・復元")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure", help="statusLine が無ければ NDF の表示を設定し、NDF の表示なら本体を今の版に置き直す")
    sub.add_parser("status", help="今の statusLine と控えの有無を出す")
    sub.add_parser("set", help="今の値を控えて NDF の表示へ切り替える")
    sub.add_parser("restore", help="控えから元の値へ戻す")
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        p = settings.paths(os.environ)
    except settings.StatuslineError as exc:
        return _skipped(exc.detail) if args.command == "ensure" else result.emit(result.stopped(TOOL, exc.kind, exc.detail))
    return _ensure(p) if args.command == "ensure" else _run(args.command, p)
