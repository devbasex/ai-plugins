"""テストを確かめる手段の判定（#4566 の決定 1・2・3）。ファイルもプロセスも触らない純粋な関数だけを置く。

工程「リファクタリング」が変更の後のテストの合否を確かめる先を、設定（`.ndf/project.json` の中身）から決める。
工程の条件の語 `tests_verifiable`、既定の起動の組み立て、`ndf refactor run` の引数の確かめが、同じ `decide` を通る
（不変条件 I1）。判定は上の行から順に当てる。

| 必須のチェック（`ci.required_checks`） | CI テストの設定（`test.ci.only`） | 手元で打てるテスト | 手段 |
| --- | --- | --- | --- |
| 1 件以上 | 問わない | 問わない | `required_checks` |
| 0 件 | 真 | 問わない | `none` |
| 0 件 | 偽か無し | 1 件以上 | `local_test` |
| 0 件 | 偽か無し | 0 件 | `none` |

手元で打てるテストは、テストの suite（`weight.is_test_suite`。`kind` を省いた suite を含む）のうち `command` を持つもの。
形の違う値は「無い」と読み、例外を上げない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ndf.testing import scoped, weight


# 必須のチェックを持つ設定のキーの道
REQUIRED_CHECKS_KEYS = ("ci", "required_checks")


class Kind(StrEnum):
    REQUIRED_CHECKS = REQUIRED_CHECKS_KEYS[1]
    LOCAL_TEST = "local_test"
    NONE = "none"


@dataclass(frozen=True)
class Means:
    """手段と、手元で打つ内容。

    - `required`: 必須のチェックの名前
    - `suites`: 手元で打つテストの suite の (名前, コマンド)（設定の順）
    - `declarations`: テストの suite の設定そのもの（`slots.run_command` の重さの判定へ渡す）
    - `commands`: 保護の判定に使う `command` と `scope_command`
    - `digest`: テストの設定の識別値（suite ごとの `name`・`command`・`scope_command`・`paths` の SHA-256）
    - `protected`: `test.protected_files`
    """

    kind: Kind
    required: tuple[str, ...] = ()
    suites: tuple[tuple[str, str], ...] = ()
    declarations: tuple[Mapping[str, Any], ...] = ()
    commands: tuple[str, ...] = ()
    digest: str = ""
    protected: tuple[str, ...] = ()


def _obj(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(v for v in value if isinstance(v, str) and v) if isinstance(value, list) else ()


def _test_suites(test: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """テストの suite のうち `command` を持つもの（設定の順）。"""
    raw = test.get("suites")
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, Mapping) and weight.is_test_suite(s) and isinstance(s.get("command"), str) and s["command"]]


def _digest(suites: list[Mapping[str, Any]]) -> str:
    rows = [{k: s.get(k) for k in ("name", "command", "scope_command", "paths")} for s in suites]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def decide(project: Mapping[str, Any]) -> Means:
    """設定の中身から手段と、手元で打つ内容を返す（モジュールの説明の表）。"""
    section, key = REQUIRED_CHECKS_KEYS
    required = _strings(_obj(project.get(section)).get(key))
    if required:
        return Means(Kind.REQUIRED_CHECKS, required=required)
    test = _obj(project.get("test"))
    if scoped.ci_only(test):
        return Means(Kind.NONE)
    suites = _test_suites(test)
    if not suites:
        return Means(Kind.NONE)
    commands = tuple(str(s[k]) for s in suites for k in ("command", scoped.SCOPE_KEY) if isinstance(s.get(k), str) and s[k])
    return Means(
        Kind.LOCAL_TEST,
        suites=tuple((str(s["name"]) if "name" in s else "", str(s["command"])) for s in suites),
        declarations=tuple(dict(s) for s in suites),
        commands=commands,
        digest=_digest(suites),
        protected=_strings(test.get("protected_files")),
    )


def total_minutes(project: Mapping[str, Any]) -> float | None:
    """`test.local_total_minutes` が正の数なら返す。無いか、0・負・数でなければ None。"""
    value = _obj(project.get("test")).get("local_total_minutes")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)
