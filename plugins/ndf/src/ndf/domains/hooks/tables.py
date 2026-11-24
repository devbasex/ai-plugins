"""hook が読むコントラクトの表（`contracts/limits/` と `contracts/hooks/*.json`）の読み込みと、出力の組み立て。

標準ライブラリだけで書く（環境が無いときのエントリポイントも `strict` の回答をここで出力の形にする）。

hook の経路では `ndf.contracts`（jsonschema を読む）を import しないため、表は `json` で直に読む。表の形は CI の
テストが確かめる。保存先はこのファイルの位置だけから求める（ほかのプラグインの `CLAUDE_PLUGIN_ROOT` を読まない）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

# このファイルから 4 つ上がプラグインの根（src/ndf/domains/hooks/tables.py）
PLUGIN_ROOT = Path(__file__).resolve().parents[4]
CONTRACTS = PLUGIN_ROOT / "contracts"
# 上限の設定の分けた表（1 方針 1 ファイル）
LIMITS_DIR = CONTRACTS / "limits"


class TableError(Exception):
    """コントラクトの表が無い・読めない・引いた行が無い。"""


class TableMissing(TableError):
    """コントラクトの表のファイルが無い。"""


@lru_cache(maxsize=None)
def load(rel: str) -> Any:
    """`contracts/` からの相対パスの表を 1 プロセスに 1 回読む。"""
    return _read_json(CONTRACTS / rel)


def _read_json(path: Path) -> Any:
    """表のファイル 1 つを読む。同じキーが 2 つあれば断る（`ndf.contracts` の `load_json` と同じ規則）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_refuse_duplicate_keys)
    except FileNotFoundError as exc:
        raise TableMissing(f"コントラクトの表が無い: {path}") from exc
    except (OSError, ValueError) as exc:
        raise TableError(f"コントラクトの表を読めない: {path}: {exc}") from exc


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    out = dict(pairs)
    if len(out) != len(pairs):
        raise ValueError("同じキーが 2 つある")
    return out


def _split_item(rel: str, key: str) -> dict[str, Any]:
    item = load(f"{rel}/{key}.json")
    if not isinstance(item, dict):
        raise TableError(f"分けた表の項目 {CONTRACTS / rel / key}.json がオブジェクトでない（{type(item).__name__}）")
    return item


def json_object(text: str) -> Optional[dict[str, Any]]:
    """JSON のオブジェクトを読む。空白だけ・JSON でない・オブジェクトでなければ None。"""
    try:
        data = json.loads(text) if text.strip() else None
    except ValueError:  # noqa: NDF401 利用者の入力: JSON でない入力・書きかけの行は形の違いとして通す
        return None
    return data if isinstance(data, dict) else None


def hooks_dir(state_dir: Path) -> Path:
    """状態の保存先の下の hook の保存先（計測・送信待ち・セッションの数え手・注入のバックアップ）。"""
    return state_dir / "hooks"


def policy_path(name: str) -> str:
    """方針のファイルの、プラグインの根からの相対パス（直す先として示す）。"""
    return f"{CONTRACTS.name}/{LIMITS_DIR.name}/{name}.json"


@lru_cache(maxsize=None)
def policy(name: str) -> Optional[dict[str, Any]]:
    """方針 1 つの中身。そのファイルだけを 1 プロセスに 1 回読む。方針が無ければ None。

    ファイルの有無を先に照会せず、読めなかったことから欠けを知る（無いことも 1 プロセスに 1 回だけ確かめる）。
    """
    try:
        return _split_item(LIMITS_DIR.name, name)
    except TableMissing:  # noqa: NDF401 ファイル: 方針が無いことは値の無さとして返し、要る側（`policy_value` など）が断る
        return None


def policy_value(name: str, key: str) -> Any:
    """方針の 1 つの値。無ければ `TableError`。"""
    row = policy(name)
    if row is None or key not in row:
        raise TableError(f"上限の設定の方針 {name} に {key} が無い")
    return row[key]


def guards() -> list[dict[str, Any]]:
    return load("hooks/guards.json")["guards"]


def ndf_commands() -> list[str]:
    """NDF のコマンドとみなす名前（パスの最後の部分）。"""
    return load("hooks/guards.json")["ndf_commands"]


def runtimes() -> dict[str, dict[str, Any]]:
    return load("hooks/runtimes.json")["runtimes"]


def event_spec(runtime: str, event: str) -> Optional[dict[str, Any]]:
    """ランタイムの事象のコントラクト。表に無ければ None。"""
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
    """コントラクト表の形の標準出力（1 行の JSON）。形の無い判定と空の文は空。"""
    spec = event_spec(runtime, event)
    template = spec["outputs"].get(kind) if spec and kind != "pass" else None
    if template is None or not text:
        return ""
    return json.dumps(_fill(template, text), ensure_ascii=False) + "\n"
