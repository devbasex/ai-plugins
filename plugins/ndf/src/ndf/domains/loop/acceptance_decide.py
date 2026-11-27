"""課題の本文の受け入れ条件と、その確認の区分の記録の読み書き（#2144・#1682。GitHub を呼ばない）。

受け入れ条件のセクション・確認の表の見出し・区分の語と、セクションと表の行の読み方は `ndf.core.plan.inputs_decide` が持ち、ここは
その上に手段の列と記録の書き込みを足す。

確認の区分の記録は `## 受け入れ条件の確認` のセクションの表（`| 条件 | 区分 | 手段 |`）で、区分は `マージ前`（マージ前に
確かめた。手段にテスト名か実行したコマンドを書く）か `リリース後`（リリースの後でしか確かめられない）である。
手段の無い `マージ前` の行は確かめていないので読まない。

実装のプランは worker の後に検証（`unrecorded`）を打ち、区分の記録の無い条件が残れば完了で返さない（#1682）。
要求の手動確認（マージ前）を行えなかった worker は手段を書けないので、その条件は記録の無いまま残る。
"""

from __future__ import annotations

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
    """受け入れ条件の番号（上から 1 始まり）とチェックボックスの後の文。セクションが無ければ空。"""
    return [Criterion(i, text) for i, text in enumerate(inputs.criteria(body), start=1)]


def _head() -> tuple[str, ...]:
    return (*inputs.CHECKS_HEAD, MEANS)


def _parse_row(kind: str, means: str) -> Optional[Check]:
    """区分と手段のセルから Check を作る。区分の語が違う行と、手段の無いマージ前の行は読まない。"""
    if kind not in inputs.KINDS:
        return None
    stripped = means.strip("`").strip()
    if kind == PRE_MERGE and (not stripped or stripped == NO_MEANS):
        return None
    return Check(kind, "" if kind == POST_RELEASE else means)


def read_checks(body: str) -> dict[int, Check]:
    """`## 受け入れ条件の確認` の表を {条件の番号: Check} で返す。セクション・表・列が無ければ空。"""
    return inputs.check_values(body, _head(), lambda row: _parse_row(row[1], row[2]))


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
    """`## 受け入れ条件の確認` のセクションの表（番号の順）。手段の `|` と改行は `inputs.cell` で書き、`read_checks` が元へ戻す。"""
    head = inputs.pipe_row(_head())
    rule = inputs.pipe_row([":---" for _ in _head()])
    rows = [inputs.pipe_row([str(n), c.kind, inputs.cell(c.means) if c.kind == PRE_MERGE else NO_MEANS]) for n, c in sorted(checks.items())]
    return "\n".join([head, rule, *rows])


def recorded_rows(body: str, pre_merge: Mapping[int, str], post_release: Iterable[int]) -> dict[int, Optional[Check]]:
    """渡した番号の行だけで書いた表を単独で読み戻した {番号: Check}（読めない行は None）。本文の別の表や既存の行が
    混ざらないので、手段が `—` だけの行などの入力の誤りを本文の古い記録で隠さない（#3886 の r3-1）。"""
    checks = merged(body, pre_merge, post_release)
    numbers = [*pre_merge, *post_release]
    back = read_checks(f"{inputs.CHECKS_HEADING}\n\n{checks_table({n: checks[n] for n in numbers})}\n")
    return {n: back.get(n) for n in numbers}


def recorded_body(body: str, pre_merge: Mapping[int, str], post_release: Iterable[int]) -> str:
    """記録を書いた本文。セクションが無ければ末尾へ足し、あればセクションの中の表だけを置き換える（表が無ければ見出しの直後へ置く）。
    セクションの中の説明文・小見出しと、セクションの外は変えない。"""
    table = checks_table(merged(body, pre_merge, post_release))
    lines = inputs.body_lines(body)
    section = inputs.section_span(lines, inputs.CHECKS_HEADING)
    if section is None:
        return body.rstrip("\n") + f"\n\n{inputs.CHECKS_HEADING}\n\n{table}\n"
    start = section[0]
    # 置き換える表は read_checks が読む表と同じ（`inputs.checks_table_span`。#3886 の r4-1）
    span = inputs.checks_table_span(lines)
    if span is not None:
        return "\n".join([*lines[: span[0]], *table.split("\n"), *lines[span[1] :]])
    rest = lines[start:]
    return "\n".join([*lines[:start], "", *table.split("\n"), *([""] if rest and rest[0].strip() else []), *rest])
