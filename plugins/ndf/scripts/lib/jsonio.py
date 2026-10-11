"""JSON の読みと原子的な書き込み（#1142 の L0）。標準ライブラリだけを import する（ラッパーのバージョンディレクトリにも入る）。

読みは、無いとき・壊れたとき・形が違うときの扱いを引数で選ぶ。既定はどれも `JsonReadError` を上げ、
呼び出し側が自分の失敗の形（`StepError`・終了コード）へ変える。値を渡せば、その値を返して上げない。

    jsonio.read(path)                              # 無い・壊れた・形が違う → JsonReadError
    jsonio.read(path, missing=None)                # 無ければ None。壊れていれば JsonReadError
    jsonio.read(path, missing={}, broken={}, want=dict)  # どれでも {}

JSONL（1 行 1 オブジェクトの追記の記録）は `read_jsonl` と `append_jsonl` で読み書きする。

    jsonio.read_jsonl(path, missing=[])                          # 壊れた行があれば JsonReadError
    jsonio.read_jsonl(path, missing=[], skip_broken=True)        # 壊れた行を飛ばす
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

RAISE = object()


class JsonReadError(ValueError):
    """JSON を読めない。`kind` は `missing`（無い）・`broken`（読めない・JSON でない）・`type`（形が違う）。"""

    def __init__(self, kind: str, path: pathlib.Path, detail: str = "") -> None:
        self.kind, self.path, self.detail = kind, path, detail
        what = {"missing": "無い", "broken": "読めない", "type": "形が違う"}[kind]
        super().__init__(f"{path} が{what}" + (f": {detail}" if detail else ""))


def read(path: pathlib.Path | str, *, missing: Any = RAISE, broken: Any = RAISE, want: type | None = None) -> Any:
    """`path` の JSON を読む。`want` を渡すと、その型でない値を `type` の失敗にする（`broken` の値を返す）。"""
    path = pathlib.Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if missing is RAISE:
            raise JsonReadError("missing", path) from None
        return missing
    except (OSError, ValueError) as e:
        if broken is RAISE:
            raise JsonReadError("broken", path, str(e)) from None
        return broken
    if want is not None and not isinstance(data, want):
        if broken is RAISE:
            raise JsonReadError("type", path, f"{want.__name__} で書く")
        return broken
    return data


def write_atomic(path: pathlib.Path | str, data: Any, indent: int | None = 2) -> None:
    """JSON を原子的に書く。

    同じディレクトリへ一時ファイルを書いてから `replace` する。途中で落ちても半端な JSON が残らないため、
    再開時に必ず読める。失敗は例外で返し、一時ファイルは残さない。
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_jsonl(path: pathlib.Path | str, *, missing: Any = RAISE, broken: Any = RAISE, skip_broken: bool = False) -> Any:
    """`path` の JSONL を辞書の list で読む。

    JSON でない行とオブジェクトでない行は壊れた行である。`skip_broken` なら飛ばし、そうでなければ
    ファイルの読めないときと同じく `broken` の扱い（既定は `JsonReadError`）にする。空行は数えない。
    """
    path = pathlib.Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if missing is RAISE:
            raise JsonReadError("missing", path) from None
        return missing
    except (OSError, ValueError) as e:
        if broken is RAISE:
            raise JsonReadError("broken", path, str(e)) from None
        return broken
    rows = []
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            d = None
        if isinstance(d, dict):
            rows.append(d)
        elif not skip_broken:
            if broken is RAISE:
                raise JsonReadError("broken", path, f"{n} 行目がオブジェクトの JSON でない")
            return broken
    return rows


def append_jsonl(path: pathlib.Path | str, row: dict) -> None:
    """JSONL の末尾へ 1 行足す。ディレクトリが無ければ作る。既存の行は書き換えない。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
