"""json_pick.py: JSON Pointer（RFC 6901）で JSON の値を 1 つずつ取り出す（#2909）。

`--field` を持つ取り出し（`sprint-state.py get`・`loop get`・`glossary.py check|diff|lookup`・`step_result.py pick`）は
すべてここの関数を呼ぶ。取り出しの規則を 1 か所に置き、スクリプトごとに書かない（決定 2）。

- 指し先は空文字（根）か `/` で始まる。`~0` は `~`、`~1` は `/` に戻す
- 配列の添字は 10 進の非負の整数だけを受ける（`-` や先頭の 0 は値が無い扱い）
- 値が無い指し先は `None` にし、`missing` に指し先を並べる（読み手が null の値と区別できるように）

ファイルも GitHub も触らない。標準ライブラリだけで書く。`cli_pick` は `step_result.py pick` の本体である。
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys

import step_read
import step_result

_INDEX = re.compile(r"^(0|[1-9][0-9]*)$")
_MISSING = object()


class PointerError(ValueError):
    """指し先の書き方の誤り（`/` で始まらない・`~` の後が 0 か 1 でない）。"""


def parse_pointer(pointer: str) -> list[str]:
    """指し先を参照の並びに割る。書き方が誤っていれば PointerError。"""
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PointerError(f"指し先は / で始める: {pointer!r}")
    tokens = pointer[1:].split("/")
    for t in tokens:
        if re.search(r"~(?![01])", t):
            raise PointerError(f"~ の後は 0 か 1 だけ: {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in tokens]


def _step(cur, token: str):
    if isinstance(cur, dict):
        return cur.get(token, _MISSING)
    if isinstance(cur, list) and _INDEX.match(token):
        i = int(token)
        return cur[i] if i < len(cur) else _MISSING
    return _MISSING


def resolve_pointer(doc, pointer: str):
    """指し先の値を返す。無ければ `(None, False)`、あれば `(値, True)`。"""
    cur = doc
    for token in parse_pointer(pointer):
        cur = _step(cur, token)
        if cur is _MISSING:
            return None, False
    return cur, True


def validate_pointers(pointers) -> None:
    """指し先の並びの書き方を先に確かめる（読む前に 2 で止めるため）。"""
    for p in pointers:
        parse_pointer(p)


def pick_fields(doc, pointers) -> tuple[dict, list[str]]:
    """指し先ごとの値の辞書と、値の無かった指し先の並びを返す。"""
    values: dict = {}
    missing: list[str] = []
    for p in pointers:
        v, found = resolve_pointer(doc, p)
        values[p] = v
        if not found:
            missing.append(p)
    return values, missing


def missing_field_items(missing) -> list[dict]:
    """値の無かった指し先を結果 JSON の `items` の形にする。"""
    return [{"kind": "field", "name": p, "result": "missing"} for p in missing]


def picked(func, tool: str):
    """結果 JSON を `emit` で出す副命令の本体 `func(a)` を包む。`a.field` を渡したときだけ、出した結果 JSON の `metrics` を
    `{<指し先>: <値>}` に、`items` を空に置き換えて出し直す。`status` と終了コードは `--field` を渡さないときと同じ。"""

    def run(a):
        fields = getattr(a, "field", None)
        if not fields:
            return func(a)
        try:
            validate_pointers(fields)
        except PointerError as e:
            step_result.emit(step_result.result(tool, "stopped", str(e)), step_result.EXIT_UNREADABLE)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                func(a)
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        obj = step_read.result_line(buf.getvalue())
        if obj is None:
            sys.stdout.write(buf.getvalue())
            raise SystemExit(code)
        values, _ = pick_fields(obj, fields)
        step_result.emit({**obj, "items": [], "metrics": values}, code)

    return run


# --- `step_result.py pick`（標準入力の結果 JSON から値を取り出す） ---------------------------


def _raw_line(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def cli_pick(a, stdin=None) -> "NoReturn":  # noqa: F821
    """`pick`: 結果 JSON から値を取り出す。`status` と `next` は入力を写す（#2909 の F1〜F3）。"""
    try:
        validate_pointers(a.field)
    except PointerError as e:
        step_result.emit(step_result.result("pick", "stopped", str(e)), step_result.EXIT_UNREADABLE)
    if a.raw and len(a.field) != 1:
        step_result.emit(step_result.result("pick", "stopped", "--raw には --field を 1 つだけ渡す"), step_result.EXIT_UNREADABLE)
    src = step_read.result_line((stdin or sys.stdin).read())
    if src is None:
        step_result.emit(step_result.result("pick", "stopped", "標準入力の最後の行が結果 JSON でない"), step_result.EXIT_UNREADABLE)
    values, missing = pick_fields(src, a.field)
    if a.raw:
        print(_raw_line(values[a.field[0]]))
        raise SystemExit(step_result.EXIT_PRECONDITION if missing else step_result.EXIT_OK)
    summary = f"{len(a.field) - len(missing)}/{len(a.field)} 件の値を取り出した（元: {src['tool']} {src['status']}）"
    keep = {"presentation_path": src.get("presentation_path"), "next": src.get("next"), "gaps": src.get("gaps")}
    step_result.emit(step_result.result("pick", src["status"], summary, missing_field_items(missing), values, **keep))
