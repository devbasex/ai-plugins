"""中継が起動するセッション（レーンの claude・supervise の子の `claude -p`）へ渡す環境（#2464・#2504・#2985）。

渡し方は環境変数の表（`contracts/domains/env.json`）の `child` の列が決める。

- `drop`: 渡さない（VS Code の git 認証の変数。窓を閉じると push と fetch が固まる。#2985）
- `set`: NDF が値を入れる（入力欄のサジェストを止める `false`。#2504。指定のアカウントの名前。#2464）

子を起動する処理はどれもこの関数で環境を作る（中継の起動と supervise の子で指定のアカウントが食い違わない）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from ndf.contracts import facts
from ndf.domains.relay import accounts


def _set_values(environ: Mapping[str, str], state_dir: Path, account: Optional[str]) -> dict[str, Optional[str]]:
    """`set` の変数に入れる値。None は入れない（親の値も外す）。"""
    return {
        "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
        "NDF_CLAUDE_ACCOUNT": account if account is not None else accounts.chosen(environ, state_dir),
    }


def child_env(environ: Mapping[str, str], state_dir: Path, account: Optional[str] = None) -> dict[str, str]:
    """子のセッションへ渡す環境。`account` を渡せば選び方より先にそれを使う（中継がアカウントを替えたとき）。"""
    drop = set(facts.ids("env", child="drop"))
    out = {k: v for k, v in environ.items() if k not in drop}
    values = _set_values(environ, state_dir, account)
    for name in facts.ids("env", child="set"):
        value = values[name]
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out
