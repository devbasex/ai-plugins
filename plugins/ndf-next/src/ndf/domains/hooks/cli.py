"""`ndf hooks` の副命令（#3345 の決定 90・94・95）。

    ndf hooks stats [--days 14]                         # 事象・guard ごとの p50・p95・拒否と差し戻しの数・注入の最大
    ndf hooks check-budget                              # 予算の順序の検査（破れがあれば stopped）
    ndf hooks capture <ランタイム> <版> <事象> < payload  # 本体の入力の見本を、秘密を伏せて足す
    ndf hooks render [--check]                          # 登録（hooks/<ランタイム>.json）を契約表と予算から作り直す

hook の経路ではない（利用者と CI が打つ）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ndf import cli, identity
from ndf.contracts import causes, result
from ndf.domains.hooks import budget, metrics, registration, tables

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)
SAMPLES = tables.PLUGIN_ROOT / "tests" / "domains" / "hooks" / "samples"
# 見本で中身を残すトップレベルの鍵（ほかの文字列と、入れ子の中の同名の鍵は伏せる）
_KEEP = frozenset({"hook_event_name", "tool_name", "source", "reason", "permission_mode", "model", "matcher"})
_ID_KEYS = frozenset({"session_id", "turn_id", "tool_use_id", "prompt_id"})
_PLACEHOLDER = {"cwd": "<cwd>", "transcript_path": "<transcript>"}
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")


def redact(payload: Any) -> Any:
    """見本に書く形。トップレベルの識別子は 0 に、作業ディレクトリと会話の記録のパスは置き換え、既知のメタデータだけを残す。

    `tool_input`・`tool_response` などの入れ子の中は、鍵の名前によらず文字列をすべて伏せる。
    """
    if not isinstance(payload, dict):
        return _hide(payload)
    return {k: _top(k, v) for k, v in payload.items()}


def _top(key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return _hide(value)
    if key in _KEEP:
        return value
    if key in _ID_KEYS:
        return re.sub(r"[0-9a-fA-F]", "0", value)
    return _PLACEHOLDER.get(key, "<text>")  # noqa: NDF502 外部コマンド: ランタイムが渡したほかの鍵の文字列は伏せる


def _hide(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _hide(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_hide(v) for v in value]
    return "<text>" if isinstance(value, str) else value


def capture(runtime: str, version: str, event: str, payload: dict, root: Path = SAMPLES) -> Path:
    """見本を `<root>/<ランタイム>/<版>/<事象>.json` へ書く。"""
    path = root / runtime / version / f"{event}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _stats(days: float) -> int:
    state_dir = identity.load().state_dir
    got = metrics.stats(metrics.read_records(state_dir, days))
    summary = f"{days:g} 日の hook の計測: 事象 {len(got['events'])} 種・guard {len(got['guards'])} 種"
    return result.emit(result.result(TOOL, result.OK, summary, items=[got], metrics={"days": days}))


def _check_budget() -> int:
    errors = budget.check_budget(tables.policies(), budget.registrations_of(tables.PLUGIN_ROOT))
    if errors:
        return result.emit(result.stopped(TOOL, "config_broken", f"hook の予算の破れが {len(errors)} 件ある", items=errors))
    return result.emit(result.result(TOOL, result.OK, "hook の予算の順序を守れている"))


def _invalid(detail: str) -> int:
    return result.emit(result.stopped(TOOL, "invalid", detail))


def _capture(args: argparse.Namespace) -> int:
    if args.runtime not in tables.runtimes() or tables.event_spec(args.runtime, args.event) is None:
        return _invalid(f"契約表に無いランタイムか事象: {args.runtime} {args.event}")
    if not _VERSION_RE.match(args.version):
        return _invalid(f"版の形が違う: {args.version}")
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError as exc:
        return _invalid(f"標準入力が JSON でない: {exc}")
    if not isinstance(payload, dict):
        return _invalid("標準入力が JSON のオブジェクトでない")
    path = capture(args.runtime, args.version, args.event, payload)
    return result.emit(result.result(TOOL, result.OK, f"見本を書いた: {path}", items=[{"path": str(path)}]))


def _render(check: bool) -> int:
    changed = registration.stale() if check else registration.write()
    return result.render_report(
        TOOL,
        [{"path": str(p)} for p in changed],
        check,
        stale_summary=f"作り直した結果と違う登録が {len(changed)} 件ある（ndf hooks render で作り直す）",
        ok_summary="登録は作り直した結果と同じ",
        written_summary=f"登録を {len(changed)} 件書いた",
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="hook の計測・予算の検査・入力の見本・登録の生成")
    sub = parser.add_subparsers(dest="action", required=True)
    st = sub.add_parser("stats", help="事象・guard ごとの所要と判定の数")
    st.add_argument("--days", type=float, default=metrics.keep_days())
    st.set_defaults(run=lambda a: _stats(a.days))
    sub.add_parser("check-budget", help="予算の順序の検査").set_defaults(run=lambda a: _check_budget())
    cap = sub.add_parser("capture", help="本体の入力の見本を、秘密を伏せて足す（標準入力に payload）")
    cap.add_argument("runtime")
    cap.add_argument("version")
    cap.add_argument("event")
    cap.set_defaults(run=_capture)
    ren = sub.add_parser("render", help="登録を契約表と予算から作り直す")
    ren.add_argument("--check", action="store_true", help="書かずに、作り直した結果と違えば stopped")
    ren.set_defaults(run=lambda a: _render(a.check))
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    return args.run(args)
