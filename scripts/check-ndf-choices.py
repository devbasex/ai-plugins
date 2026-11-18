#!/usr/bin/env python3
"""NDF の設定で選べる値（取り込み方の表の行・delivery の種類・リリースの形）が、基準から黙って減っていないかを確かめる。

基準は `scripts/ndf-choices.json` にある。集合ごとに出所（事実の表の行・スキーマの列挙・ファイル名の glob の 3 種）と
値の一覧を持ち、値を減らすときは `removed` に Issue の番号と理由を持つ行を足す（#3617 の決定 4）。

- 基準にあり今のコントラクトに無く、`removed` にも無い値があれば落ちる（終了コード 1。I4）
- 今のコントラクトにあり基準に無い値があれば落ちる（終了コード 1。I6）。値を足す PR は基準も直す
- `removed` の行が Issue の番号・理由を欠くか、その値が今もあれば落ちる（終了コード 2。I5）
- 基準か出所を読めなければ落ちる（終了コード 2。0 件を「全部減った」と読まない）

2 は 1 より先に返す。標準ライブラリだけで書き、NDF の依存の環境を要らない。

使い方: python3 scripts/check-ndf-choices.py [--root <リポジトリの根>] [--baseline <基準のファイル>]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from json_pointer import walk  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "scripts/ndf-choices.json"
VERSION = 1
SOURCE_KINDS = ("rows", "enum", "files")
PREFIX = "[check-ndf-choices]"


class BaselineError(Exception):
    """基準のファイルを読めない・形が違う。"""


class SourceError(Exception):
    """集合の出所を読めない。"""


class Verdict(NamedTuple):
    """基準と今の値の照合の結果。各要素は（集合の id, 値）の並び。"""

    removed: list[tuple[str, str]]
    stale: list[tuple[str, str]]
    added: list[tuple[str, str]]


def _source_problem(source: Any) -> Optional[str]:
    if not isinstance(source, Mapping):
        return "source が object でない"
    kinds = [k for k in SOURCE_KINDS if k in source]
    if len(kinds) != 1:
        return f"source は {'・'.join(SOURCE_KINDS)} のどれか 1 つを持つ"
    need = {"rows": ("key",), "enum": ("pointer",), "files": ("prefix",)}[kinds[0]]
    if not all(isinstance(source.get(k), str) for k in (kinds[0], *need)):
        return f"source の {kinds[0]} は {'・'.join(need)} と一緒に文字列で書く"
    return None


def _set_problems(sets: Any) -> list[str]:
    if not isinstance(sets, list) or not sets:
        return ["sets が空でない配列でない"]
    errs: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(sets):
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or not row["id"]:
            errs.append(f"sets[{i}] に id が無い")
            continue
        if row["id"] in seen:
            errs.append(f"sets の id が重なる: {row['id']}")
        seen.add(row["id"])
        problem = _source_problem(row.get("source"))
        if problem:
            errs.append(f"sets[{row['id']}]: {problem}")
        values = row.get("values")
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            errs.append(f"sets[{row['id']}]: values が空でない文字列の配列でない")
    return errs


def _removed_problems(removed: Any, ids: set[str]) -> list[str]:
    if not isinstance(removed, list):
        return ["removed が配列でない"]
    errs: list[str] = []
    for i, row in enumerate(removed):
        where = f"removed[{i}]"
        if not isinstance(row, Mapping):
            errs.append(f"{where} が object でない")
            continue
        if row.get("set") not in ids:
            errs.append(f"{where}: set が sets に無い集合を指す: {row.get('set')!r}")
        if not isinstance(row.get("value"), str) or not row["value"]:
            errs.append(f"{where}: value が無い")
        issue = row.get("issue")
        if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
            errs.append(f"{where}: issue（減らす Issue の番号）が 1 以上の整数でない: {issue!r}")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            errs.append(f"{where}: reason が空")
    return errs


def load_baseline(path: Path) -> dict[str, Any]:
    """基準を読み、形を確かめて返す。誤りは `BaselineError`。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"{path} を読めない: {exc}") from None
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise BaselineError(f"{path} の version が {VERSION} でない")
    errs = _set_problems(data.get("sets"))
    ids = {r["id"] for r in data.get("sets") or [] if isinstance(r, Mapping) and isinstance(r.get("id"), str)}
    errs += _removed_problems(data.get("removed", []), ids)
    if errs:
        raise BaselineError(f"{path} の形が違う: " + "；".join(errs))
    data.setdefault("removed", [])
    return data


def _pointer(doc: Any, pointer: str) -> Any:
    """JSON Pointer（RFC 6901）の先の値。無ければ `SourceError`。"""
    if pointer and not pointer.startswith("/"):
        raise SourceError(f"JSON Pointer が / で始まらない: {pointer}")
    keys = [raw.replace("~1", "/").replace("~0", "~") for raw in pointer.split("/")[1:]]
    try:
        return walk(doc, keys)
    except LookupError:
        raise SourceError(f"JSON Pointer の先が無い: {pointer}") from None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceError(f"{path} を読めない: {exc}") from None


def read_values(source: Mapping[str, Any], root: Path) -> set[str]:
    """出所の種類ごとに今の値の集合を読む。読めない・0 件は `SourceError`。"""
    if "rows" in source:
        rows = _read_json(root / source["rows"]).get("rows")
        if not isinstance(rows, list):
            raise SourceError(f"{source['rows']} に rows の配列が無い")
        got = {r[source["key"]] for r in rows if isinstance(r, Mapping) and isinstance(r.get(source["key"]), str)}
    elif "enum" in source:
        enum = _pointer(_read_json(root / source["enum"]), source["pointer"])
        if not isinstance(enum, list) or not all(isinstance(v, str) for v in enum):
            raise SourceError(f"{source['enum']} の {source['pointer']} が文字列の列挙でない")
        got = set(enum)
    else:
        got = {p.stem.removeprefix(source["prefix"]) for p in root.glob(source["files"]) if p.is_file()}
    if not got:
        raise SourceError(f"値が 0 件: {json.dumps(dict(source), ensure_ascii=False)}")
    return got


def judge(baseline: Mapping[str, Any], current: Mapping[str, set[str]]) -> Verdict:
    """基準と今の値を照らし、減った値（I4）・誤った例外（I5）・足した値（I6）に分ける。"""
    excused = {(r["set"], r["value"]) for r in baseline["removed"]}
    removed, stale, added = [], [], []
    for row in baseline["sets"]:
        sid, known, now = row["id"], set(row["values"]), current[row["id"]]
        removed += [(sid, v) for v in sorted(known - now) if (sid, v) not in excused]
        added += [(sid, v) for v in sorted(now - known)]
    stale = sorted((s, v) for s, v in excused if v in current.get(s, set()))
    return Verdict(removed, stale, added)


def _report(verdict: Verdict) -> int:
    for sid, value in verdict.stale:
        print(f"{PREFIX} 例外 {sid}/{value} の値がまだある（removed から外す）", file=sys.stderr)
    if verdict.stale:
        return 2
    for sid, value in verdict.removed:
        line = json.dumps({"set": sid, "value": value, "issue": "<番号>", "reason": "<理由>"}, ensure_ascii=False)
        print(f"{PREFIX} 集合 {sid} から {value} が減った。減らすなら removed に {line} を足す", file=sys.stderr)
    for sid, value in verdict.added:
        print(f"{PREFIX} 集合 {sid} に {value} が増えた。sets の {sid} の values に足す", file=sys.stderr)
    return 1 if verdict.removed or verdict.added else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="リポジトリの根")
    parser.add_argument("--baseline", type=Path, help=f"基準のファイル（既定は <根>/{BASELINE}）")
    args = parser.parse_args(argv)
    path = args.baseline if args.baseline is not None else args.root / BASELINE
    try:
        baseline = load_baseline(path)
    except BaselineError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return 2
    current: dict[str, set[str]] = {}
    for row in baseline["sets"]:
        try:
            current[row["id"]] = read_values(row["source"], args.root)
        except SourceError as exc:
            print(f"{PREFIX} 集合 {row['id']} の出所を読めない: {exc}", file=sys.stderr)
            return 2
    for sid, values in current.items():
        print(f"{PREFIX} {sid}: {len(values)} 件")
    return _report(judge(baseline, current))


if __name__ == "__main__":
    raise SystemExit(main())
