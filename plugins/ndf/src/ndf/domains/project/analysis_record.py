"""解析の記録（`.ndf/analysis/project.json`）の形と、設定（`.ndf/project.json`）から旧い形のキーを取り除く処理（#4516）。

記録は、判定（`check.judge`）が読む値だけを持つ: 形のバージョン・解析器のバージョン・入力のファイルのハッシュ・ブランチの構成・
記録に対応する設定の指紋（`written_digest`）・書き出しの途中のマーカー（`pending`）。記録のディレクトリは中に置いた `.gitignore` で
git から外す。旧い形のキーは、`analysis` の `written` 以外・`test_duration`・`languages[].files`・`ci.workflows`・`ci.provider` である。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.domains.project.items import obj, value_digest
from ndf.domains.records import files, jsonfile

# 解析の記録の形のバージョン
RECORD_VERSION = 1
# 記録のディレクトリを git から外す `.gitignore` の中身
IGNORE_TEXT = "# NDF の解析が書く記録。コミットしない\n*\n"
SCHEMA = contracts.schema_path("internal/project-analysis")
# 項目の中の旧い形のキー（`<項目>[].<キー>` は配列の各行の中、`<項目>.<キー>` は object の中）
_OLD_IN_ITEMS = ("languages[].files", "ci.workflows", "ci.provider")
# 項目ごと消す旧い形のキー
OLD_ITEMS = ("test_duration",)


def _old_keys(head: str) -> tuple[str, ...]:
    """`_OLD_IN_ITEMS` のうち、点より前が `head`（`<項目>` か `<項目>[]`）のキーの名前。"""
    return tuple(path.partition(".")[2] for path in _OLD_IN_ITEMS if path.partition(".")[0] == head)


def strip_item(key: str, value: Any) -> tuple[Any, list[str]]:
    """設定の 1 項目の値から旧い形のキーを取り除いた値と、取り除いたキーの名前。入力を書き換えない。"""
    if isinstance(value, list):
        old = _old_keys(f"{key}[]")
        found = [k for k in old if any(isinstance(row, dict) and k in row for row in value)]
        if not found:
            return value, []
        rows = [{k: v for k, v in row.items() if k not in old} if isinstance(row, dict) else row for row in value]
        return rows, [f"{key}[].{k}" for k in found]
    if isinstance(value, dict):
        found = [k for k in value if k in _old_keys(key)]
        if found:
            return {k: v for k, v in value.items() if k not in found}, [f"{key}.{k}" for k in found]
    return value, []


def strip_legacy(doc: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """設定から旧い形のキーを取り除いたコピーと、見つけたキーの名前（設定のキーの順）。入力を書き換えない。"""
    out: dict[str, Any] = {}
    names: list[str] = []
    for key, value in doc.items():
        if key in OLD_ITEMS:
            names.append(key)
        elif key == "analysis" and isinstance(value, dict):
            names += [f"analysis.{k}" for k in value if k != "written"]
            out[key] = {"written": value["written"]} if "written" in value else {}
        else:
            out[key], found = strip_item(key, value)
            names += found
    return out, names


def load(main: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """メインディレクトリの解析の記録。(記録, None) か、無い・読めない・形が違うときは (None, 理由)。"""
    path = files.path(main, "project-analysis")
    if not path.is_file():
        return None, "解析の記録が無い"
    try:
        record = jsonfile.read_object(path)
    except jsonfile.JsonFileError as exc:
        return None, f"解析の記録が読めない: {exc}"
    except UnicodeDecodeError as exc:
        return None, f"解析の記録が読めない: UTF-8 として読めない（位置 {exc.start}）"
    errs = contracts.schema_errors(record, SCHEMA)
    if errs:
        return None, f"解析の記録が形に合わない: {'; '.join(errs[:3])}"
    return record, None


def written_digest(doc: Optional[dict[str, Any]]) -> str:
    """記録に対応する設定の指紋: 設定の書いた値の指紋（`analysis.written`。無ければ `{}`）の指紋。"""
    return value_digest(obj(obj(obj(doc).get("analysis")).get("written")))
