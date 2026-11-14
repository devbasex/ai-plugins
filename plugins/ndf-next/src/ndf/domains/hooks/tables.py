"""hook が読む契約の表（`contracts/limits.json` と `contracts/hooks/*.json`）の読み込みと、出力の組み立て。

標準ライブラリだけで書く（環境が無いときのエントリポイントも `strict` の答えをここで出力の形にする）。

hook の経路では `ndf.contracts`（jsonschema を読む）を import しないため、表は `json` で直に読む。表の形は CI の
テストが確かめる。置き場はこのファイルの位置だけから求める（ほかのプラグインの `CLAUDE_PLUGIN_ROOT` を読まない）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# このファイルから 4 つ上がプラグインの根（src/ndf/domains/hooks/tables.py）
PLUGIN_ROOT = Path(__file__).resolve().parents[4]
CONTRACTS = PLUGIN_ROOT / "contracts"


class TableError(Exception):
    """契約の表が無い・読めない・引いた行が無い。"""


@lru_cache(maxsize=None)
def load(rel: str) -> Any:
    """`contracts/` からの相対パスの表を 1 プロセスに 1 回読む。"""
    path = CONTRACTS / rel
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TableError(f"契約の表を読めない: {path}: {exc}") from exc


def json_object(text: str) -> Optional[dict[str, Any]]:
    """JSON のオブジェクトを読む。空白だけ・JSON でない・オブジェクトでなければ None。"""
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:  # noqa: NDF401 利用者の入力: JSON でない入力・書きかけの行は形の違いとして通す
        return None
    return data if isinstance(data, dict) else None


def policies() -> dict[str, dict[str, Any]]:
    """上限の宣言の方針の表（名前 → 値）。"""
    return load("limits.json")["policies"]


def policy_value(name: str, key: str) -> Any:
    """方針の 1 つの値。無ければ `TableError`。"""
    row = policies().get(name)
    if row is None or key not in row:
        raise TableError(f"上限の宣言の方針 {name} に {key} が無い")
    return row[key]


def guards() -> list[dict[str, Any]]:
    return load("hooks/guards.json")["guards"]


def runtimes() -> dict[str, dict[str, Any]]:
    return load("hooks/runtimes.json")["runtimes"]


def event_spec(runtime: str, event: str) -> Optional[dict[str, Any]]:
    """ランタイムの事象の契約。表に無ければ None。"""
    rt = runtimes().get(runtime)
    return rt["events"].get(event) if rt else None


def off_names(value: Optional[str]) -> frozenset:
    """`NDF_HOOK_OFF` の値から止める guard の名前。`all` は全部。"""
    return frozenset(n.strip() for n in (value or "").split(",") if n.strip())  # noqa: NDF501 利用者の入力: 未設定は止める guard が無い


def is_off(name: str, off: frozenset) -> bool:
    return "all" in off or name in off


def _fill(template: Any, text: str) -> Any:
    if isinstance(template, dict):
        return {k: _fill(v, text) for k, v in template.items()}
    if template in ("{reason}", "{context}"):
        return text
    return template


def output_for(runtime: str, event: str, kind: str, text: str) -> str:
    """契約表の形の標準出力（1 行の JSON）。形の無い判定と空の文は空。"""
    spec = event_spec(runtime, event)
    template = spec["outputs"].get(kind) if spec and kind != "pass" else None
    if template is None or not text:
        return ""
    return json.dumps(_fill(template, text), ensure_ascii=False) + "\n"
