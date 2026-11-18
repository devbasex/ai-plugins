"""リリースの承認資料（Markdown）を、集めた事実から組む（純粋な関数）。

資料は人が一度で読み切れる量にし、判定の材料をそろえる。

- 含む PR は PR ごとに CI の件数を出す。件数は PR の HEAD のチェックを GitHub から読んだ値で、手元の検査の状態や
  レーンの記録を読まない（並列リリースでも同じ値になる。#2655）。実行されなかったチェックは内訳に出す（#3169）
- 「利用者向けの変化」はリリース対象のファイルの変化だけで決める。PR 本文の文は読まない（#2601）
- 「指示書と設定の変化」は指示書と `.ndf/` の下の変化を並べる（C7 の変更を人が見られる。#3110）
- 「届き方」は設定のリリースの形の行（リリースの形の表の `reach`）と、設定の届け先（`release.deliveries`）のうち
  そのチャネルの行（ストアの提出・業務文書の提出先など）を出す。バージョンを持たない保存先（`none`）では
  「バージョン」のセクションを出さない（#3615）
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from ndf.domains.release import approval_decide, changes_decide, checks_decide, forms_decide, version_decide


class PullFact(NamedTuple):
    """含む PR 1 件の事実。"""

    number: int
    title: str
    tally: checks_decide.Tally


class Facts(NamedTuple):
    """承認資料の材料。"""

    channel: str
    planned: tuple[version_decide.Planned, ...]
    pulls: tuple[PullFact, ...]
    changes: tuple[changes_decide.Change, ...]
    instructions: tuple[str, ...]
    head: str
    form: forms_decide.Form
    versioned: bool
    deliveries: tuple[forms_decide.Delivery, ...] = ()


# 範囲の起点の SHA を承認資料に出す桁数
SHORT_SHA = 12


def _versions(planned: Sequence[version_decide.Planned]) -> list[str]:
    if not planned:
        return ["- バージョンを上げる単位は無い"]
    return [f"- {p.plugin}: {p.current} → {p.next}（範囲の起点 {p.since[:SHORT_SHA]}）" for p in planned]


def _pull_line(p: PullFact) -> str:
    tail = ""
    if p.tally.failed:
        tail = f"。通らなかったチェック: {', '.join(p.tally.failed)}"
    elif p.tally.pending:
        tail = f"。終わっていないチェック: {', '.join(p.tally.pending)}"
    return f"- #{p.number} {p.title}（{p.tally.label()}{tail}）"


def _ordered(pulls: Sequence[PullFact]) -> list[PullFact]:
    """含む PR を番号の順に並べる（承認資料と機械が読む項目で同じ順）。"""
    return sorted(pulls, key=lambda p: p.number)


def _pulls(pulls: Sequence[PullFact]) -> list[str]:
    return [_pull_line(p) for p in _ordered(pulls)] if pulls else ["- 含む PR は無い"]


def _distribution(changes: Sequence[changes_decide.Change]) -> list[str]:
    if not changes_decide.distribution_changed(changes):
        return ["- 利用者向けの変化なし（リリース対象のファイルの変化が 0 件）"]
    return [f"- {c.plugin}: リリース対象のファイル {len(c.distributed)} 件" for c in changes if c.distributed]


def _instructions(paths: Sequence[str]) -> list[str]:
    return [f"- `{p}`" for p in paths] if paths else ["- 無し"]


def checks_green(facts: Facts) -> bool:
    """含む PR の CI がすべて通ったか（失敗も待ちも無い）。"""
    return all(p.tally.green for p in facts.pulls)


def render(facts: Facts) -> str:
    """承認資料の本文。"""
    versions = ["## バージョン", *_versions(facts.planned), ""] if facts.versioned else []
    lines = [
        f"# リリースの承認資料（{facts.channel}）",
        "",
        f"{approval_decide.HEAD_LINE} `{facts.head}`",
        "",
        "## 届き方",
        forms_decide.reach_line(facts.form),
        *(forms_decide.delivery_line(d) for d in facts.deliveries),
        "",
        *versions,
        "## 含む PR",
        *_pulls(facts.pulls),
        "",
        "## 検査の結果",
        f"- 含む PR の CI: {'すべて通った' if checks_green(facts) else '通っていないものがある'}",
        "",
        "## 利用者向けの変化",
        *_distribution(facts.changes),
        "",
        "## 指示書と設定の変化",
        *_instructions(facts.instructions),
    ]
    return "\n".join(lines) + "\n"


def items(facts: Facts) -> list[dict]:
    """機械が読む項目（含む PR ごとの CI の件数。分母は実行されたチェック）。"""
    return [
        {
            "kind": "pull",
            "number": p.number,
            "checks_passed": p.tally.passed,
            "checks": p.tally.ran,
            "checks_skipped": p.tally.skipped,
            "failed": list(p.tally.failed),
        }
        for p in _ordered(facts.pulls)
    ]
