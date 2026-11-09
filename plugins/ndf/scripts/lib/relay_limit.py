"""レーン数の上限（同時に動かせる名前つきのラッパーの数）の正本（#2733 の決定 8）。

値はマシンの設定（`<状態の根>/relay/hub/limits.json`）→ 環境変数 `NDF_RELAY_MAX_NAMED` → 既定 3 の順で決まる（I12）。
リレーハブの起動の判定（`relay_lib/hub.py` の `max_named`。ここは `named_cap`）と計画の検査（`parallel_plan.named_limit`）が同じ関数を読む。
書くのは `relay.py cap --named <n>` だけである。標準ライブラリと `state_home`・`jsonio` だけを import する
（リレーのバージョンディレクトリにも入る）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional

from state_home import container_home

DEFAULT = 3
ENV = "NDF_RELAY_MAX_NAMED"


def limits_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """マシンの設定のファイル（`<状態の根>/relay/hub/limits.json`）。"""
    return container_home(env) / "relay" / "hub" / "limits.json"


def _positive_int(raw: object) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    try:
        v = int(str(raw))
    except (TypeError, ValueError):
        return None
    return v if v >= 1 else None


def named_cap(env: Optional[Mapping[str, str]] = None) -> tuple[int, str, Optional[str]]:
    """(上限, 出所 `file`・`env`・`default`, 読めなかった理由か None)。設定のファイルが壊れていれば次の出所へ進み、理由を返す。"""
    env = os.environ if env is None else env
    note = None
    p = limits_path(env)
    if p.exists():
        try:
            got = _positive_int(json.loads(p.read_text(encoding="utf-8")).get("max_named"))
        except (OSError, ValueError, AttributeError):
            got = None
        if got is not None:
            return got, "file", None
        note = f"{p} の max_named を読めないため次の出所を使った"
    raw = os.environ.get("NDF_RELAY_MAX_NAMED") if env is os.environ else env.get(ENV)
    if raw not in (None, ""):
        got = _positive_int(raw)
        if got is not None:
            return got, "env", note
        note = f"{ENV}={raw!r} は正の整数でないため既定の {DEFAULT} を使った"
    return DEFAULT, "default", note


def write_max_named(n: int, env: Optional[Mapping[str, str]] = None) -> Path:
    """上限を設定のファイルへ書く（一時ファイルからの置き換え。ディレクトリは 0700）。正の整数でなければ ValueError。"""
    if _positive_int(n) is None:
        raise ValueError(f"上限は正の整数: {n!r}")
    p = limits_path(env)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"max_named": int(n), "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), "by": "relay.py cap"}
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".limits-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    return p
