"""リリースの承認資料（Markdown）を、集めた事実から組む（純粋な関数）。

資料は人が一度で読み切れる量にし、判定の材料をそろえる。

- 含む PR は PR ごとに CI の件数を出す。件数は PR の先端のチェックを GitHub から読んだ値で、手元の検査の状態や
  レーンの記録を読まない（並列リリースでも同じ値になる。#2655）。実行されなかったチェックは内訳に出す（#3169）
- 「利用者向けの変化」はリリース対象のファイルの変化だけで決める。PR 本文の文は読まない（#2601）
- 「指示書と設定の変化」は指示書と `.ndf/` の下の変化を並べる（C7 の変更を人が見られる。#3110）
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from ndf.domains.release import changes_decide, checks_decide, version_decide


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


def _plugins(planned: Sequence[version_decide.Planned]) -> list[str]:
    if not planned:
        return ["- 版を上げるプラグインは無い"]
    return [f"- {p.plugin}: {p.current} → {p.next}" for p in planned]


def _pull_line(p: PullFact) -> str:
    tail = ""
    if p.tally.failed:
        tail = f"。通らなかったチェック: {', '.join(p.tally.failed)}"
    elif p.tally.pending:
        tail = f"。終わっていないチェック: {', '.join(p.tally.pending)}"
    return f"- #{p.number} {p.title}（{p.tally.label()}{tail}）"


def _pulls(pulls: Sequence[PullFact]) -> list[str]:
    return [_pull_line(p) for p in sorted(pulls, key=lambda p: p.number)] if pulls else ["- 含む PR は無い"]


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
    lines = [
        f"# リリースの承認資料（{facts.channel}）",
        "",
        f"提示したコミット: `{facts.head}`",
        "",
        "## 版",
        *_plugins(facts.planned),
        "",
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
        for p in sorted(facts.pulls, key=lambda p: p.number)
    ]
