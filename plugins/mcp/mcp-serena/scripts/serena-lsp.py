#!/usr/bin/env python3
"""mcp-serena の入口。サブコマンド detect / configure / check / hook。

判断の要らない手順（言語の検出・project.yml の書き換え・起動の検証・導入のチェック・hook）を
ここに置き、モデル（Skill）には判断だけを残す。

外部パッケージ（ruamel.yaml）は mcp-serena の環境（`pyproject.toml` と `uv.lock`）にある（#1142 の決定 25。
`serena_lsp/env.py`）。hook ではない副命令は、その環境で動いていなければ環境を用意して起動し直す。
`hook session-start` は環境を用意し、用意できた環境の python で通知を行う（用意できなければ通知を飛ばす）。
`hook pre-tool-use` は hook の command が環境の python を直に起動する（環境が無ければ command が素通しする）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
CLI = str(Path(__file__).resolve())


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
        return
    for key, value in result.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False)}")


def cmd_detect(args) -> int:
    from serena_lsp import detect, table
    root = Path(args.root).resolve()
    try:
        detected, skipped = detect.detect(root, table.load())
    except detect.GitUnavailable as exc:
        print(f"git を使えません: {exc}", file=sys.stderr)
        return 2
    _emit({"root": str(root), "detected": detected, "skipped": skipped}, args.json)
    return 0


def cmd_configure(args) -> int:
    from serena_lsp import verify
    try:
        result, code = verify.configure(
            Path(args.root).resolve(), dry_run=args.dry_run, gitignore=args.gitignore,
            serena_gitignore=args.serena_gitignore,
            only=[s for s in (args.only or "").split(",") if s] or None,
            serena_cmd=args.serena)
    except verify.Terminated as exc:
        print("中断しました。検証を通った言語だけを書きました", file=sys.stderr)
        return 128 + int(exc.args[0])
    _emit(result, args.json)
    return code


def cmd_check(args) -> int:
    from serena_lsp import check
    result, code = check.run(Path(args.root).resolve(), runtime=args.runtime)
    _emit(result, args.json)
    return code


def _session_env(args) -> bool:
    """SessionStart の環境の用意。通知へ進むなら真（環境の python で動いている）。起動し直すときは戻らない。"""
    from serena_lsp import env, hooks
    if hooks._skip_for_client(args.client) or os.environ.get(env.REEXEC_ENV):
        return env.importable()
    python = env.prepare()
    if env.importable():
        return True
    if python:
        env.reexec(python, CLI, args.argv)
    return False


def cmd_hook(args) -> int:
    try:
        if args.event == "session-start" and not _session_env(args):
            return 0
        from serena_lsp import hooks
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        if args.event == "session-start":
            out = hooks.session_start(payload, args.client)
        else:
            out = hooks.pre_tool_use(payload, args.client)
        if out:
            print(json.dumps(out, ensure_ascii=False))
    except Exception:  # hook はツールの呼び出しを止めない
        pass
    return 0


def main(argv=None) -> int:
    from serena_lsp import verify

    parser = argparse.ArgumentParser(prog="serena-lsp.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("detect", help="言語を検出する")
    p.add_argument("--root", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("configure", help="project.yml を書き、1 言語ずつ起動を検証する")
    p.add_argument("--root", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--gitignore", action="store_true")
    p.add_argument("--serena-gitignore", action="store_true")
    p.add_argument("--only")
    p.add_argument("--serena", default=verify.SERENA_CMD)
    p.set_defaults(func=cmd_configure)

    p = sub.add_parser("check", help="導入の欠けをチェックする")
    p.add_argument("--root", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--runtime", choices=["claude-code", "codex"], default="claude-code")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("hook", help="ランタイムの hook から呼ぶ")
    p.add_argument("event", choices=["session-start", "pre-tool-use"])
    p.add_argument("--client", choices=["claude-code", "codex"], required=True)
    p.set_defaults(func=cmd_hook)

    args = parser.parse_args(argv)
    args.argv = list(sys.argv[1:] if argv is None else argv)
    if args.command != "hook":
        from serena_lsp import env
        env.ensure(CLI, args.argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
