"""状態ファイルの基本操作（収束ループ共通層）。

読み書きと、bash から `eval` できる KEY=VALUE 形式の出力だけを持つ。
収束の判定やスキーマはこの層に入れない（Skill 固有のため）。

`cross-review` の `state.py` と `cross-refactoring` の `refactor.py` が共有する。
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import shlex
import sys
from typing import Any


def now() -> str:
    """状態ファイルに書く時刻。ローカル時刻の ISO 8601 形式（秒まで）。"""
    return _dt.datetime.now().isoformat(timespec="seconds")


def load(path: pathlib.Path) -> dict[str, Any]:
    """状態ファイルを読む。存在しなければ FileNotFoundError を上げる。"""
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: pathlib.Path, state: dict[str, Any]) -> None:
    """状態ファイルを原子的に書く。

    同じディレクトリへ一時ファイルを書いてから `replace` する。途中で落ちても
    半端な JSON が残らないため、再開時に必ず読める。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def emit(**values: Any) -> None:
    """bash の `eval` で読める KEY=VALUE を標準出力へ書く。

    値は必ず `shlex.quote` を通す。空白や引用符を含む値（作業ディレクトリのパス、
    レビュー担当の CSV など）をそのまま出すと、呼び出し側の `eval` で語が割れる。
    """
    for key, value in values.items():
        if value is None:
            value = ""
        elif isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        print(f"{key}={shlex.quote(str(value))}")


def die(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)
