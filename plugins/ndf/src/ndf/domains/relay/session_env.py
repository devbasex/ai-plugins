"""中継が起動するセッション（レーンの claude・supervise の子の `claude -p`）へ渡す環境（#2464・#2504・#2985）。

渡し方は環境変数の表（`contracts/domains/env.json`）の `child` の列が決める。

- `drop`: 渡さない（VS Code の git 認証の変数。窓を閉じると push と fetch が固まる。#2985。親の Claude Code の
  `CLAUDECODE`・`CLAUDE_CODE_SESSION_ID`。残ると claude が入れ子の起動を断る）
  NDF の起動の部品が入れる `UV_PROJECT_ENVIRONMENT`・`VIRTUAL_ENV`・`UV_RUN_RECURSION_DEPTH`・`CLAUDE_PLUGIN_ROOT`。残ると
  セッションの中の `uv run` が NDF の依存の環境を書き換える）
- `PYTHONPATH` は、`bin/ndf` が先頭に足した NDF の `src` だけを外し、利用者の値は残す
- `set`: NDF が値を入れる（入力欄のサジェストを止める `false`。#2504。指定のアカウントの名前。#2464）

子を起動する処理はどれもこの関数で環境を作る（中継の起動と supervise の子で指定のアカウントが食い違わない）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from ndf.contracts import facts
from ndf.domains.relay import accounts

# `bin/ndf` が PYTHONPATH の先頭に足す NDF の `src`（このファイルから 4 つ上）
NDF_SRC = Path(__file__).resolve().parents[3]


def _set_values(environ: Mapping[str, str], state_dir: Path, account: Optional[str]) -> dict[str, Optional[str]]:
    """`set` の変数に入れる値。None は入れない（親の値も外す）。"""
    return {
        "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
        "NDF_CLAUDE_ACCOUNT": account if account is not None else accounts.chosen(environ, state_dir),
    }


def tool_env(environ: Mapping[str, str]) -> dict[str, str]:
    """`drop` の変数と NDF の `src` の `PYTHONPATH` を外した環境（`set` の値は入れず、`CLAUDE_CONFIG_DIR` は残す）。

    中継が打つ `claude plugin` のコマンドはこれを渡し、子のセッションは `child_env` が `set` の値を足して渡す。
    """
    drop = set(facts.ids("env", child="drop"))
    out = {k: v for k, v in environ.items() if k not in drop}
    if "PYTHONPATH" in out:
        kept = [p for p in out["PYTHONPATH"].split(os.pathsep) if p and Path(p).resolve() != NDF_SRC]
        if kept:
            out["PYTHONPATH"] = os.pathsep.join(kept)
        else:
            del out["PYTHONPATH"]
    return out


def child_env(environ: Mapping[str, str], state_dir: Path, account: Optional[str] = None) -> dict[str, str]:
    """子のセッションへ渡す環境。`account` を渡せば選び方より先にそれを使う（中継がアカウントを替えたとき）。"""
    out = tool_env(environ)
    values = _set_values(environ, state_dir, account)
    for name in facts.ids("env", child="set"):
        value = values[name]
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out
