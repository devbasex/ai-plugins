"""マシンで 1 つの上限の正本（#2733 の決定 8・#2999 の決定 2）。

- レーン数の上限（同時に動かせる名前つきのラッパーの数）: マシンの設定（`<状態の根>/relay/hub/limits.json`）の
  `max_named` → 環境変数 `NDF_RELAY_MAX_NAMED` → 既定 3。リレーハブの起動の判定（`relay_lib/hub.py` の `max_named`）と
  計画の検査（`parallel_plan.named_limit`）が `named_cap` を読む
- プランの本数の上限（同じマシンで同時に流すプランの合計）: 同じファイルの `max_plans` → `NDF_PARALLEL_MAX_PLANS` →
  既定 6。計画の検査（`parallel_plan.max_plans`）とマシンの数え方（`parallel_machine`）が `plans_cap` を読む

書くのは `relay.py cap --named <n>` / `--plans <n>` だけで、書かないキーは残す。標準ライブラリと `state_home`・`jsonio` だけを import する
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
PLANS_ENV = "NDF_PARALLEL_MAX_PLANS"
DEFAULT_PLANS = 6  # 同時に流すプランの合計の既定（2026-09-25 の実測。8 コア・30 GB）


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


_SOURCES = {
    # キー: (環境変数, 既定)
    "max_named": (ENV, DEFAULT),
    "max_plans": (PLANS_ENV, DEFAULT_PLANS),
}


def _cap(key: str, raw: Optional[str], env: Optional[Mapping[str, str]]) -> tuple[int, str, Optional[str]]:
    """(上限, 出所 `file`・`env`・`default`, 読めなかった理由か None)。設定のファイルが壊れていれば次の出所へ進み、理由を返す。

    `raw` は呼び手が読んだ環境変数の値（契約の検査が読みを見つけられるよう、呼び手が名前を書いて読む）。"""
    env_name, default = _SOURCES[key]
    note = None
    p = limits_path(env)
    if p.exists():
        data = _read_limits(p)
        got = _positive_int(data.get(key))
        if got is not None:
            return got, "file", None
        if key in data or not data:  # 他のキーだけを持つファイルは、このキーを書いていないだけである
            note = f"{p} の {key} を読めないため次の出所を使った"
    if raw not in (None, ""):
        got = _positive_int(raw)
        if got is not None:
            return got, "env", note
        note = f"{env_name}={raw!r} は正の整数でないため既定の {default} を使った"
    return default, "default", note


def named_cap(env: Optional[Mapping[str, str]] = None) -> tuple[int, str, Optional[str]]:
    """レーン数の上限（`max_named` → `NDF_RELAY_MAX_NAMED` → 既定 3）。"""
    raw = os.environ.get(ENV) if env is None else env.get(ENV)
    return _cap("max_named", raw, env)


def plans_cap(env: Optional[Mapping[str, str]] = None) -> tuple[int, str, Optional[str]]:
    """プランの本数の上限（`max_plans` → `NDF_PARALLEL_MAX_PLANS` → 既定 6。#2999 の I1）。"""
    raw = os.environ.get(PLANS_ENV) if env is None else env.get(PLANS_ENV)
    return _cap("max_plans", raw, env)


def _read_limits(p: Path) -> dict:
    """設定のファイルの中身。読めない・辞書でなければ空。"""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_limit(key: str, n: int, env: Optional[Mapping[str, str]] = None) -> Path:
    """1 つの上限を設定のファイルへ書き、他のキーを残す（一時ファイルからの置き換え。ディレクトリは 0700。#2999 の I2）。

    正の整数でないか、知らないキーなら ValueError で、何も書かない。
    """
    if key not in _SOURCES:
        raise ValueError(f"知らない上限: {key!r}")
    if _positive_int(n) is None:
        raise ValueError(f"上限は正の整数: {n!r}")
    p = limits_path(env)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = _read_limits(p) if p.exists() else {}
    data.update({key: int(n), "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), "by": "relay.py cap"})
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".limits-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
    os.replace(tmp, p)
    return p


def write_max_named(n: int, env: Optional[Mapping[str, str]] = None) -> Path:
    """レーン数の上限を書く（`write_limit("max_named", n)`）。"""
    return write_limit("max_named", n, env)
