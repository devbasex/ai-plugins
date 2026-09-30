"""従量の接続の子の claude へ、宣言の変数を `--settings` の env でも渡す（#1543）。

relay の区間と supervise.py の `claude -p` が同じ組み立てを使う。宣言の読み先は `claude_accounts` が持つ。
"""

from __future__ import annotations

import json
import os

import claude_accounts as ca


def metered_settings(args: list[str], env: dict, cwd: str, keep: int = 0) -> list[str]:
    """従量の接続の子（`env` が `account_env` の従量の接続の環境）の引数へ、宣言の変数を `--settings` の env でも足す（#1543）。

    Claude Code は利用者の `settings.json` の `env` を環境変数より優先するため、環境変数だけでは宣言が負ける。
    `--settings` は複数あると最後の 1 つだけが効くため、`--` より前の既存の `--settings`（JSON かファイル。相対パスは
    `cwd` から）を 1 つに読み込み、宣言のキーだけを上書きして先頭に置く。読めない既存の値があれば引数を変えない。
    資格情報の変数（`SECRET_ENV`）は引数に載せない（環境変数だけで渡す）。先頭の `keep` 語（起動の語）はそのまま残す。"""
    if env.get(ca.NAME_ENV) != ca.METERED:
        return list(args)
    declared = {k: v for k, v in ca.fallback_env(env).items() if k not in ca.SECRET_ENV}
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
    return head + ["--settings", json.dumps(settings, ensure_ascii=False)] + rest + args[end:]


def _read_settings(value: str, cwd: str) -> dict | None:
    """`--settings` の値（JSON の文字列かファイルのパス）を読む。読めなければ None。"""
    try:
        if value.lstrip().startswith("{"):
            d = json.loads(value)
        else:
            with open(os.path.join(cwd, os.path.expanduser(value)), encoding="utf-8") as f:
                d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None
