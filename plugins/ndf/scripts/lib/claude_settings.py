"""従量の接続の子の claude へ、宣言の変数を `--settings` の env でも渡す（#1543）。

relay の区間・supervise.py の `claude -p`・`launch-cli.sh` の claude が同じ組み立てを使う。宣言の読み先は
`claude_accounts` が持つ。スクリプトとして呼ぶと、今の環境で足す `--settings` の値を 1 行で出す（足さなければ何も出さない）。

子が読む設定のファイルの `env` の値を集める部品（`env_values`。#2855）もここに置く。claude は設定のファイルの `env` を
起動の環境より優先する（https://code.claude.com/docs/en/env-vars の Precedence）ため、子へ渡す前に送り先などを確かめる。
"""

from __future__ import annotations

import glob
import json
import os
import sys
import tempfile

import claude_accounts as ca

# 引数へ載せない名前の語。`SECRET_ENV` に無い資格情報（`AWS_BEARER_TOKEN_BEDROCK`・`ANTHROPIC_CUSTOM_HEADERS` など）を
# 名前で外す。引数は同じホストの他のプロセスから読める（`ps`・`/proc/<pid>/cmdline`）ため、迷う名前は載せない側へ倒す
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "HEADERS")
# 管理の設定（Linux / macOS）と、その横の追加の置き場（managed-settings.d）
MANAGED_SETTINGS = ("/etc/claude-code/managed-settings.json", "/Library/Application Support/ClaudeCode/managed-settings.json")


def is_secret(name: str) -> bool:
    """引数へ載せない変数か（`SECRET_ENV` の名前か、名前に `SECRET_WORDS` の語を含む）。"""
    return name in ca.SECRET_ENV or any(w in name.upper() for w in SECRET_WORDS)


def metered_settings(args: list[str], env: dict, cwd: str, keep: int = 0, store: str | None = None) -> list[str]:
    """従量の接続の子（`env` が `account_env` の従量の接続の環境）の引数へ、宣言の変数を `--settings` の env でも足す（#1543）。

    Claude Code は利用者の `settings.json` の `env` を環境変数より優先するため、環境変数だけでは宣言が負ける。
    `--settings` は複数あると最後の 1 つだけが効くため、`--` より前の既存の `--settings`（JSON かファイル。相対パスは
    `cwd` から）を 1 つに読み込み、宣言のキーだけを上書きして先頭に置く。読めない既存の値があれば引数を変えない。
    資格情報の変数（`is_secret`）は引数に載せない（環境変数だけで渡す。資格情報でなくても名前が当たれば同じ扱いで、
    `settings.json` の `env` に同じ名前があれば負ける）。先頭の `keep` 語（起動の語）はそのまま残す。

    既存の値がファイルなら、まとめた設定も 0600 のファイル（`store`。無ければ `tempfile.mkstemp` で作り、消すのは呼んだ側）へ
    書いてパスで渡す。ファイルの `env` は利用者の資格情報を持ちうるため、中身を引数へ展開しない（引数は他のプロセスから
    読める）。書けなければ引数を変えない。"""
    if env.get(ca.NAME_ENV) != ca.METERED:
        return list(args)
    declared = {k: v for k, v in ca.fallback_env(env).items() if not is_secret(k)}
    if not declared:
        return list(args)
    head, args = list(args[:keep]), list(args[keep:])
    end = args.index("--") if "--" in args else len(args)
    rest, value, i = [], None, 0
    while i < end:
        a = args[i]
        if a == "--settings" and i + 1 < end:
            value, i = args[i + 1], i + 2
            continue
        if a.startswith("--settings="):
            value = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1
    settings = _read_settings(value, cwd) if value is not None else {}
    if settings is None:
        return head + args
    settings["env"] = {**(settings.get("env") if isinstance(settings.get("env"), dict) else {}), **declared}
    if value is not None and not _is_json(value):
        try:
            merged = _write_private(settings, store)
        except OSError:
            return head + args
    else:
        merged = json.dumps(settings, ensure_ascii=False)
    return head + ["--settings", merged] + rest + args[end:]


def _is_json(value: str) -> bool:
    """`--settings` の値が JSON の文字列か（そうでなければファイルのパス）。"""
    return value.lstrip().startswith("{")


def _write_private(settings: dict, store: str | None) -> str:
    """まとめた設定を 0600 のファイルへ書き、そのパスを返す（`store` が無ければ一時ファイルを作る）。"""
    if store is None:
        fd, store = tempfile.mkstemp(prefix="ndf-claude-settings-", suffix=".json")
        os.close(fd)
    ca._write(store, settings)
    return store


def _read_settings(value: str, cwd: str) -> dict | None:
    """`--settings` の値（JSON の文字列かファイルのパス）を読む。読めなければ None。"""
    try:
        if _is_json(value):
            d = json.loads(value)
        else:
            with open(os.path.join(cwd, os.path.expanduser(value)), encoding="utf-8") as f:
                d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _project_dirs(cwd: str) -> list[str]:
    """子の claude がプロジェクトの設定を読みうる場所。`cwd` とその上の各階層と、worktree なら元の checkout の根。"""
    dirs, d = [], os.path.abspath(cwd)
    while True:
        dirs.append(d)
        git = os.path.join(d, ".git")
        if os.path.isfile(git):  # worktree: 手元の設定は元の checkout の根から読まれる
            try:
                with open(git, encoding="utf-8") as f:
                    line = f.readline().strip()
            except OSError:
                line = ""
            if line.startswith("gitdir:") and "/.git/worktrees/" in line:
                dirs.append(line[len("gitdir:") :].strip().split("/.git/worktrees/")[0])
        parent = os.path.dirname(d)
        if parent == d:
            return dirs
        d = parent


def env_values(name: str, env: dict, cwd: str | None) -> list[str]:
    """`env` の環境・`cwd` で起動した子の claude が読む設定のファイル（管理・利用者・プロジェクト・手元）の `env` にある `name` の値。

    空でない文字列だけを返す。読めないファイル・壊れた JSON は飛ばす（claude -p も飛ばす）。"""
    conf = env.get(ca.CONFIG_ENV) or os.path.join(os.path.expanduser("~"), ".claude")
    files = [os.path.join(conf, "settings.json")]
    for m in MANAGED_SETTINGS:
        files += [m, *sorted(glob.glob(os.path.join(os.path.dirname(m), "managed-settings.d", "*.json")))]
    for d in _project_dirs(cwd or os.getcwd()):
        files += [os.path.join(d, ".claude", n) for n in ("settings.json", "settings.local.json")]
    found = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        sub = data.get("env") if isinstance(data, dict) else None
        value = sub.get(name) if isinstance(sub, dict) else None
        if isinstance(value, str) and value:
            found.append(value)
    return found


def main() -> int:
    """今の環境の子の claude へ足す `--settings` の値を出す（`launch-cli.sh` が読む。足さなければ何も出さない）。"""
    args = metered_settings([], dict(os.environ), os.getcwd())
    if args:
        print(args[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
