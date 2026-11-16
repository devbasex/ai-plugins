"""解消する Issue を閉じる証拠の入力の形と判定（純粋。#3395 の「閉じる操作」・IY5）。

証拠は 3 種類で、1 行に 1 つだけ持つ。

| 種類 | 鍵 | 確かめ方（`settle.py` が GitHub から読む） |
| --- | --- | --- |
| `improved`（改善の証拠） | `pr`・`test`（テストのノード ID） | PR がマージ済みで宛先が基底のブランチで、PR の変更ファイルにテストのファイルがあり、PR の差分でテストの関数の定義が足され、基底のブランチのそのファイルに関数がある |
| `removed`（消えたコードのパス） | `path`・`commit` | コミットが基底のブランチの祖先で、指したパスが基底のブランチに無い |
| `not_reproduced`（再現しない記録） | `command`・`exit`・`record`・`at`・`after_pr` | `exit` が 0 で、記録のファイルが基底のブランチにあり、`after_pr` がマージ済みで、`at` がそのマージの後で今より前 |
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, NamedTuple, Optional

IMPROVED, REMOVED, NOT_REPRODUCED = "improved", "removed", "not_reproduced"
# 行の結果: 閉じた・確かめた（--dry-run）・確かめられない・前から閉じていた
CLOSED, VERIFIED, UNVERIFIED, ALREADY_CLOSED = "closed", "verified", "unverified", "already_closed"
# 種類ごとの必須の鍵と型
SHAPES: dict[str, dict[str, type]] = {
    IMPROVED: {"pr": int, "test": str},
    REMOVED: {"path": str, "commit": str},
    NOT_REPRODUCED: {"command": str, "exit": int, "record": str, "at": str, "after_pr": int},
}


class Row(NamedTuple):
    issue: int
    child: str
    kind: str
    evidence: dict[str, Any]


def _shape_error(e: Any) -> Optional[str]:
    if not isinstance(e, dict) or not isinstance(e.get("issue"), int) or e["issue"] <= 0:
        return "issue（正の整数）が無い"
    ev = e.get("evidence")
    if not isinstance(ev, dict) or ev.get("kind") not in SHAPES:
        return f"evidence.kind は {' / '.join(SHAPES)} のどれか"
    for key, typ in SHAPES[ev["kind"]].items():
        val = ev.get(key)
        # 終了コード（exit）は 0 が正しい値。空を断るのは文字列と PR の番号だけ
        if not isinstance(val, typ) or isinstance(val, bool) or val == "" or (key != "exit" and val == 0):
            return f"evidence.{key} が無いか形が違う（{typ.__name__}）"
    if ev["kind"] == NOT_REPRODUCED and parse_time(ev["at"]) is None:
        return f"evidence.at が ISO 8601 の日時でない: {ev['at']!r}"
    return None


def parse(raw: Any) -> tuple[list[Row], list[dict[str, Any]]]:
    """入力の JSON を行と、形の違う行（番号と理由）に分ける。改善の証拠のテストのノード ID の形が違えば `ValueError`。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("settle"), list):
        raise ValueError('入力は {"settle": [...]} の形で書く')
    rows: list[Row] = []
    bad: list[dict[str, Any]] = []
    for i, e in enumerate(raw["settle"]):
        err = _shape_error(e)
        if err is not None:
            bad.append({"index": i, "issue": e.get("issue") if isinstance(e, dict) else None, "reason": err})
            continue
        child = e["child"] if isinstance(e.get("child"), str) else ""
        rows.append(Row(e["issue"], child, e["evidence"]["kind"], e["evidence"]))
    for row in rows:
        if row.kind == IMPROVED:
            test_target(row.evidence["test"])
    return rows, bad


def test_target(node_id: str) -> tuple[str, str]:
    """テストのノード ID（`<パス>::[<クラス>::]<関数>[<引数>]`）からファイルのパスと関数の名前。"""
    parts = node_id.split("::")
    if len(parts) < 2 or not parts[0] or not parts[-1]:
        raise ValueError(f"テストのノード ID の形が違う: {node_id!r}")
    return parts[0], re.sub(r"\[.*\]$", "", parts[-1])


def _def_re(func: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(async\s+)?def\s+{re.escape(func)}\s*\(", re.MULTILINE)


def defines(text: str, func: str) -> bool:
    """ファイルの中身が関数 `func` を定義するか。"""
    return _def_re(func).search(text) is not None


def adds_def(patch: str, func: str) -> bool:
    """PR の差分（unified diff）が関数 `func` の定義の行を足すか。"""
    added = "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
    return defines(added, func)


# 時差つきの ISO 8601 の日時（秒と小数は省ける）
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


def parse_time(text: str) -> Optional[datetime]:
    """時差つきの ISO 8601 の日時。形が違えば None（暦に無い日付は `ValueError`）。"""
    if not _ISO.match(text):
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def comment(row: Row) -> str:
    """閉じる前に Issue へ書く証拠のコメント。"""
    ev = row.evidence
    head = f"閉じる証拠（{row.child}）: " if row.child else "閉じる証拠: "
    if row.kind == IMPROVED:
        return head + f"再現のテスト `{ev['test']}` を足した PR #{ev['pr']} がマージ済みで、基底のブランチにある"
    if row.kind == REMOVED:
        return head + f"原因のパス `{ev['path']}` はコミット {ev['commit']} の後の基底のブランチに無い"
    return head + (
        f"再現の手順 `{ev['command']}` は終了コード {ev['exit']} で再現しなかった（記録 `{ev['record']}`・{ev['at']}・"
        f"PR #{ev['after_pr']} のマージの後）"
    )
