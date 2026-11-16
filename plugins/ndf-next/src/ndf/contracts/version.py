"""契約の版（`contracts/version.json`）の読み手と、版の上げ忘れの検査。

保存されるデータが写し持つ契約（結果 JSON の形・選択の記録の形・止まった理由の種類・答えの語彙）は、
この 1 つの版だけを持つ（表ごとに版を持たない）。進行表などの保存する側は `current()` を記録に写す。
版の履歴の各行は、その版の契約の指紋（`fingerprint()` の sha256）を持つ。契約を変えたのに末尾の行の指紋が
合わなければ、版を 1 つ上げて今の指紋の行を末尾へ足すまで検査（`ndf contracts check`）が落ちる。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ndf import contracts
from ndf.contracts import causes, facts

DECLARATION = "version.json"
SCHEMA = contracts.schema_path("version")
# 指紋に入れる契約のスキーマ（保存されるデータの形）
SCHEMAS = ("result", "route-chosen")
# 指紋に入れる語彙の事実の表
VOCABULARIES = ("answers",)


class VersionError(contracts.ContractError):
    """版の宣言が無い・形が違う。"""


@contracts.cached_reader
def declaration() -> dict[str, Any]:
    raw = contracts.load_as(DECLARATION, VersionError)
    contracts.require_schema(raw, SCHEMA, VersionError, f"契約の版 {DECLARATION}")
    return raw


def current() -> int:
    """今の契約の版（履歴の末尾の版）。"""
    return int(declaration()["history"][-1]["version"])


def fingerprint() -> str:
    """今の契約の指紋。スキーマは JSON の値で比べ、空白と鍵の順の違いを数えない。"""
    body = {
        "schemas": {name: contracts.load_json(contracts.schema_path(name)) for name in SCHEMAS},
        "causes": sorted(causes.table()),
        "vocabularies": {name: sorted(facts.ids(name)) for name in VOCABULARIES},
    }
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def errors() -> list[str]:
    """版の履歴の誤り（版が 1 から 1 つずつ上がらない・指紋の重なり・末尾の指紋が今の契約と合わない）。"""
    history = declaration()["history"]
    errs: list[str] = []
    versions = [h["version"] for h in history]
    if versions != list(range(1, len(history) + 1)):
        errs.append(f"契約の版の履歴は 1 から 1 つずつ上げる: {versions}")
    dup = contracts.duplicates(h["sha256"] for h in history)
    if dup:
        errs.append(f"契約の版の履歴に同じ指紋が 2 つある: {', '.join(dup)}")
    now = fingerprint()
    if history[-1]["sha256"] != now:
        errs.append(
            f"契約（{', '.join(SCHEMAS)} のスキーマ・止まった理由の種類・{', '.join(VOCABULARIES)} の語彙）が版 {versions[-1]} の指紋と違う。"
            f'版を上げて {{"version": {len(history) + 1}, "sha256": "{now}"}} を {DECLARATION} の history の末尾へ足す'
        )
    return errs
