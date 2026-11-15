"""課題の本文の受け入れ条件と、その確認の区分の記録の読み書き（#2144・#1682。GitHub を呼ばない）。

受け入れ条件の節・確認の表の見出し・区分の語と、節と表の行の読み方は `ndf.core.plan.inputs_decide` が持ち、ここは
その上に手段の列と記録の書き込みを足す。

確認の区分の記録は `## 受け入れ条件の確認` の節の表（`| 条件 | 区分 | 手段 |`）で、区分は `マージ前`（マージ前に
確かめた。手段にテスト名か実行したコマンドを書く）か `リリース後`（リリースの後でしか確かめられない）である。
手段の無い `マージ前` の行は確かめていないので読まない。

実装のプランは worker の後に照合（`unrecorded`）を打ち、区分の記録の無い条件が残れば完了で返さない（#1682）。
要求の手動確認（マージ前）を行えなかった worker は手段を書けないので、その条件は記録の無いまま残る。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import NamedTuple, Optional

from ndf.core.plan import inputs_decide as inputs

# 確認の表の手段の列の見出しと、リリース後の行の手段の欄
MEANS = "手段"
NO_MEANS = "—"
PRE_MERGE, POST_RELEASE = inputs.KINDS


class AcceptanceError(Exception):
    """記録の引数の誤り（受け入れ条件に無い番号・両方の区分に出る番号・本文に受け入れ条件が無い）。"""


class Criterion(NamedTuple):
    number: int
    text: str


class Check(NamedTuple):
    """1 つの受け入れ条件の確認の区分。means はマージ前に確かめた手段（リリース後なら空）。"""

    kind: str
    means: str


def criteria(body: str) -> list[Criterion]:
    """受け入れ条件の番号（上から 1 始まり）とチェックボックスの後の文。節が無ければ空。"""
    return [Criterion(i, text) for i, text in enumerate(inputs.criteria(body), start=1)]


def _head() -> tuple[str, ...]:
    return (*inputs.CHECKS_HEAD, MEANS)


def _parse_row(cells: list[str], cols: tuple[int, ...]) -> Optional[tuple[int, Check]]:
    if len(cells) <= max(cols):
        return None
    num, kind, means = re.match(r"^(\d+)", cells[cols[0]]), cells[cols[1]], cells[cols[2]].strip("`").strip()
    if not num or kind not in inputs.KINDS:
        return None
    if kind == PRE_MERGE and (not means or means == NO_MEANS):
        return None
    return int(num.group(1)), Check(kind, "" if kind == POST_RELEASE else cells[cols[2]])


def read_checks(body: str) -> dict[int, Check]:
    """`## 受け入れ条件の確認` の表を {条件の番号: Check} で返す。節・表・列が無ければ空。"""
    section = inputs.section_lines(body, inputs.CHECKS_HEADING)
    rows = [line for line in section if line.strip().startswith("|")] if section is not None else []
    if len(rows) < 2:
        return {}
    head = inputs.cells(rows[0])
    if any(h not in head for h in _head()):
        return {}
    cols = tuple(head.index(h) for h in _head())
    out: dict[int, Check] = {}
    for row in rows[2:]:
        parsed = _parse_row(inputs.cells(row), cols)
        if parsed is not None:
            out[parsed[0]] = parsed[1]
    return out


def unrecorded(body: str) -> list[Criterion]:
    """確認の区分の記録が無い受け入れ条件（手段の無いマージ前の行も記録が無いとみなす）。"""
    checks = read_checks(body)
    return [c for c in criteria(body) if c.number not in checks]


def merged(body: str, pre_merge: Mapping[int, str], post_release: Iterable[int]) -> dict[int, Check]:
    """本文の今の記録へ渡した区分を重ねた記録。受け入れ条件に無い番号・両方に出る番号は `AcceptanceError`。"""
    numbers = {c.number for c in criteria(body)}
    if not numbers:
        raise AcceptanceError(f"本文に受け入れ条件（`{inputs.HEADING}` のチェックボックス）が無い")
    post = set(post_release)
    both = sorted(post & set(pre_merge))
    if both:
        raise AcceptanceError(f"マージ前とリリース後の両方に渡した番号: {both}")
    unknown = sorted((post | set(pre_merge)) - numbers)
    if unknown:
        raise AcceptanceError(f"受け入れ条件に無い番号: {unknown}（本文の条件は 1〜{max(numbers)}）")
    checks = read_checks(body)
    checks.update({n: Check(PRE_MERGE, m) for n, m in pre_merge.items()})
    checks.update({n: Check(POST_RELEASE, "") for n in post})
    return checks


def checks_table(checks: Mapping[int, Check]) -> str:
    """`## 受け入れ条件の確認` の節の中身（番号の順の表）。"""
    head = "| " + " | ".join(_head()) + " |"
    rule = "| " + " | ".join(":---" for _ in _head()) + " |"
    rows = [f"| {n} | {c.kind} | {c.means if c.kind == PRE_MERGE else NO_MEANS} |" for n, c in sorted(checks.items())]
    return "\n".join([head, rule, *rows])


def recorded_body(body: str, pre_merge: Mapping[int, str], post_release: Iterable[int]) -> str:
    """記録を書いた本文。節が無ければ末尾へ足し、あれば節の中の表だけを置き換える。節の外は変えない。"""
    table = checks_table(merged(body, pre_merge, post_release))
    lines = body.replace("\r\n", "\n").split("\n")
    heads = [i for i, line in enumerate(lines) if line.rstrip() == inputs.CHECKS_HEADING]
    if not heads:
        return body.rstrip("\n") + f"\n\n{inputs.CHECKS_HEADING}\n\n{table}\n"
    start = heads[0] + 1
    end = next((i for i in range(start, len(lines)) if re.match(r"^#{1,2} ", lines[i])), len(lines))
    tail = lines[end:]
    return "\n".join([*lines[:start], "", table, *([""] if tail else []), *tail])
