"""機能の対応表（`features.json`）と直しの棚卸し（`fixes.json`）を読み、形を確かめ、一覧を引く。

形と書き方の決まりは同じディレクトリの `README.md` にある。入れ替え（S2）の確かめは、対応表の行の
`feature`・`to`・`not_built`・`trial` の 4 つの鍵だけを読む。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import jsonschema

HERE = Path(__file__).resolve().parent
# ファイルの名前 → スキーマ（schema.json）の $defs の名前
SHAPES = {"features": "features_file", "fixes": "fixes_file"}


def load(name: str) -> dict[str, Any]:
    """`<name>.json`（`features` か `fixes`）を読む。"""
    if name not in SHAPES:
        raise ValueError(f"知らない表: {name}")
    return _read(name)


def _read(name: str) -> Any:
    """同じディレクトリの `<name>.json` を読む。"""
    return json.loads((HERE / f"{name}.json").read_text(encoding="utf-8"))


def _schema_errors(name: str, doc: Any) -> list[str]:
    schema = _read("schema")
    schema["$ref"] = f"#/$defs/{SHAPES[name]}"
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    found = sorted(("/".join(str(p) for p in e.absolute_path) or "(最上位)", e.message) for e in validator.iter_errors(doc))
    return [f"{name}: {where}: {message}" for where, message in found]


def _duplicates(ids: list[str]) -> list[str]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def _unknown_children(name: str, items: list[dict[str, Any]], places: Mapping[str, Any]) -> list[str]:
    return [
        f"{name}: 行き先 {item['to']} が places に無い（{item.get('id') or item.get('issue')}）"
        for item in items
        if "to" in item and item["to"] not in places
    ]


def shape_errors(features: Any, fixes: Any) -> list[str]:
    """2 つの表の誤りを並べる。空なら合う。

    スキーマ（鍵・値の形・`to` か作らない理由のどちらか 1 つ）に加え、id の重なり・`places` に無い行き先・
    受け付けた不具合が指す対応表の行が無いことを数える。
    """
    errs = _schema_errors("features", features) + _schema_errors("fixes", fixes)
    return errs or _reference_errors(features, fixes)


def _reference_errors(features: Mapping[str, Any], fixes: Mapping[str, Any]) -> list[str]:
    """スキーマで表せない参照の誤り（id の重なり・`places` に無い行き先・対応表に無い行を指す受け付け）。"""
    places = features["places"]
    rows = features["rows"]
    ids = [r["id"] for r in rows]
    errs = [f"features: id が重なる: {i}" for i in _duplicates(ids)]
    errs += [f"fixes: id が重なる: {i}" for i in _duplicates([f["id"] for f in fixes["fixes"] + fixes["regressions"]])]
    errs += _unknown_children("features", rows, places)
    for part in ("fixes", "regressions", "intake"):
        errs += _unknown_children(part, fixes[part], places)
    return errs + _missing_rows(fixes["intake"], set(ids))


def _missing_rows(intake: list[dict[str, Any]], known: set[str]) -> list[str]:
    return [f"intake: #{i['issue']} の行 {i['row']} が対応表に無い" for i in intake if i["row"] is not None and i["row"] not in known]


class Entrypoint(NamedTuple):
    """エントリポイントの今の置き場と、移行後の置き場（行き先の子の places）。作らない行は `after` が空。"""

    now: str
    to: str | None
    after: tuple[str, ...]
    conductor: bool


def entrypoints(features: Mapping[str, Any], *, conductor_only: bool = False) -> list[Entrypoint]:
    """エントリポイントの一覧。`conductor_only` なら conductor が直に打つものだけ。"""
    places = features["places"]
    found = []
    for r in features["rows"]:
        if r["kind"] != "entrypoint" or (conductor_only and not r.get("conductor")):
            continue
        to = r.get("to")
        found.append(Entrypoint(r["now"], to, tuple(places[to]) if to else (), bool(r.get("conductor"))))
    return found


def _under(module: str, head: str) -> bool:
    """モジュール（`a.b.c`）が置き場（`src/a/b/` のディレクトリか `src/a/b/c.py` のファイル）にあるか。"""
    path = "src/" + module.replace(".", "/")
    if head.endswith("/"):
        return (path + "/").startswith(head)
    return path == head.removesuffix(".py")


def user_commands(features: Mapping[str, Any], commands: Mapping[str, str]) -> dict[str, list[str]]:
    """利用者が手で打つ今のコマンド → 行き先の子の置き場にある副命令の名前（まだ無ければ空）。

    `commands` は副命令の表（`cli.COMMANDS`）。副命令の名前は表に写さず、行き先の子の置き場にモジュールがある
    副命令をこの表から引く。
    """
    places = features["places"]
    found: dict[str, list[str]] = {}
    for r in features["rows"]:
        if not r.get("user_command"):
            continue
        heads = places[r["to"]] if "to" in r else []
        found[r["now"]] = _commands_under(commands, heads)
    return found


def _commands_under(commands: Mapping[str, str], heads: Sequence[str]) -> list[str]:
    """副命令の表から、モジュールがいずれかの置き場の下にある副命令の名前を並べる。"""
    return sorted(cmd for cmd, module in commands.items() if any(_under(module, h) for h in heads))
