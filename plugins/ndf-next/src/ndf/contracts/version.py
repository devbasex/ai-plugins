"""コントラクトのバージョン（`contracts/version.json`）の読み手と、バージョンの上げ忘れの検査。

保存されるデータが写し持つコントラクト（結果 JSON の形・選択の記録の形・停止理由の種類・回答の語彙）は、
この 1 つのバージョンだけを持つ（表ごとにバージョンを持たない）。ステートマシンなどの保存する側は `current()` を記録に写す。
バージョンの履歴の各行は、そのバージョンのコントラクトの指紋（`fingerprint()` の sha256）を持つ。コントラクトを変えたのに末尾の行の指紋が
合わなければ、バージョンを 1 つ上げて今の指紋の行を末尾へ足すまで検査（`ndf contracts check`）が落ちる。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ndf import contracts
from ndf.contracts import causes, facts

NDF_SETTINGS = "version.json"
SCHEMA = contracts.schema_path("version")
# 指紋に入れるコントラクトのスキーマ（保存されるデータの形）
SCHEMAS = ("result", "route-chosen")
# 指紋に入れる語彙の事実の表
VOCABULARIES = ("answers",)


class VersionError(contracts.ContractError):
    """バージョンの設定が無い・形が違う。"""


@contracts.cached_reader
def ndf_settings() -> dict[str, Any]:
    raw = contracts.load_as(NDF_SETTINGS, VersionError)
    contracts.require_schema(raw, SCHEMA, VersionError, f"コントラクトのバージョン {NDF_SETTINGS}")
    return raw


def current() -> int:
    """今のコントラクトのバージョン（履歴の末尾のバージョン）。"""
    return int(ndf_settings()["history"][-1]["version"])


def fingerprint() -> str:
    """今のコントラクトの指紋。スキーマは JSON の値で比べ、空白とキーの順の違いを数えない。"""
    body = {
        "schemas": {name: contracts.load_json(contracts.schema_path(name)) for name in SCHEMAS},
        "causes": sorted(causes.table()),
        "vocabularies": {name: sorted(facts.ids(name)) for name in VOCABULARIES},
    }
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def errors() -> list[str]:
    """バージョンの履歴の誤り（バージョンが 1 から 1 つずつ上がらない・指紋の重なり・末尾の指紋が今のコントラクトと合わない）。"""
    history = ndf_settings()["history"]
    errs: list[str] = []
    versions = [h["version"] for h in history]
    if versions != list(range(1, len(history) + 1)):
        errs.append(f"コントラクトのバージョンの履歴は 1 から 1 つずつ上げる: {versions}")
    dup = contracts.duplicates(h["sha256"] for h in history)
    if dup:
        errs.append(f"コントラクトのバージョンの履歴に同じ指紋が 2 つある: {', '.join(dup)}")
    now = fingerprint()
    if history[-1]["sha256"] != now:
        errs.append(
            f"コントラクト（{', '.join(SCHEMAS)} のスキーマ・停止理由の種類・{', '.join(VOCABULARIES)} の語彙）がバージョン {versions[-1]} の指紋と違う。"
            f'バージョンを上げて {{"version": {len(history) + 1}, "sha256": "{now}"}} を {NDF_SETTINGS} の history の末尾へ足す'
        )
    return errs
