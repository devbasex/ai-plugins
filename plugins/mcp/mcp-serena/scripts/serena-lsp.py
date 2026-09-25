#!/usr/bin/env python3
"""mcp-serena の入口。サブコマンド detect / configure / check / hook。

判断の要らない手順（言語の検出・project.yml の書き換え・起動の検証・導入のチェック・hook）を
ここに置き、モデル（Skill）には判断だけを残す。Python 3 の標準ライブラリだけで動く。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


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


def cmd_hook(args) -> int:
    try:
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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
