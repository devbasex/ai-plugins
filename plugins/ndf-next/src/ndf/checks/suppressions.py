"""境界の印 `# noqa: <規則> <境界の種類>: <理由>` を読み、兆候を外せるかを決める。

`NDF201`（ループの中の外部呼び出し）だけは `# noqa: NDF201 上限: <件数と根拠>` も使える。
印で外せない規則（`suppress` が `none`）は、印があっても数に入る。
"""

from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass
from typing import Any

SUPPRESSION = re.compile(r"#\s*noqa:\s*(?P<codes>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)(?P<rest>.*)$")
LABELED = re.compile(r"^\s*(?P<label>[^:：]+?)\s*[:：]\s*(?P<reason>\S.*?)\s*$")
EXCEPT_RULES = frozenset({"S110", "S112", "BLE001", "E722", "NDF401"})


@dataclass(frozen=True)
class Suppression:
    line: int
    codes: tuple[str, ...]
    label: str
    reason: str


def read_suppressions(text: str, codes: frozenset[str]) -> dict[int, list[Suppression]]:
    """コメントの字句から印を読み、行ごとに返す。形の誤った印も `label`・`reason` を空にして返す（捨てない）。"""
    suppressions: dict[int, list[Suppression]] = {}
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        found = SUPPRESSION.search(tok.string) if tok.type == tokenize.COMMENT else None
        if found is None:
            continue
        own = tuple(c.strip() for c in found["codes"].split(",") if c.strip() in codes)
        if not own:
            continue
        labeled = LABELED.match(found["rest"])
        label, reason = (labeled["label"], labeled["reason"]) if labeled else ("", "")
        suppressions.setdefault(tok.start[0], []).append(Suppression(tok.start[0], own, label, reason))
    return suppressions


def suppression_problem(suppression: Suppression, code: str, caught: tuple[str, ...] | None, rules: dict[str, Any]) -> str | None:
    """印がその規則の兆候を外せるなら `None`、外せないなら理由の文を返す。"""
    kind = rules["smells"][code]["suppress"]
    internal = set(rules["exception_types"]["internal"])
    problems = (
        (not suppression.label or not suppression.reason, "印の形が `<規則> <境界の種類>: <理由>` でない"),
        (kind == "none", "この規則は印で外せない。直す"),
        (suppression.label == rules["limit_label"] and code != "NDF201", "`上限:` はループの中の外部呼び出しだけ"),
        (suppression.label not in (*rules["boundaries"], rules["limit_label"]), "境界の種類は " + " / ".join(rules["boundaries"])),
        (code in EXCEPT_RULES and bool(caught) and set(caught) <= internal, "内部の型だけを受ける except は直す（握りつぶさずに送出する）"),
    )
    return next((why for bad, why in problems if bad), None)


@dataclass
class Applied:
    """印を当てた結果。数に入る兆候（外せない理由つき）と、当たる兆候の無い印。"""

    counted: list[tuple[int, str, str | None]]
    unused: list[tuple[Suppression, str]]


def apply(hits: list[tuple[int, str]], suppressions: dict[int, list[Suppression]], caught: dict[int, tuple[str, ...]], rules: dict[str, Any]) -> Applied:
    """兆候に印を当て、数に入るものと要らない印に分ける。"""
    counted: list[tuple[int, str, str | None]] = []
    used: set[tuple[int, str]] = set()
    for line, code in hits:
        own = [m for m in suppressions[line] if code in m.codes] if line in suppressions else []
        problems = [suppression_problem(m, code, caught.get(line), rules) for m in own]
        used.add((line, code))
        if own and None in problems:
            continue
        counted.append((line, code, next((p for p in problems if p), None)))
    unused = [(m, code) for ms in suppressions.values() for m in ms for code in m.codes if m.label and m.reason and (m.line, code) not in used]
    return Applied(counted, unused)
