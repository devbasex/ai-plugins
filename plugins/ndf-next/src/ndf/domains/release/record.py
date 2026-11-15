"""リリース記録を含めた PR の各々へ書き、リリース後テストの記録を同じ PR へ書く（#3487。元の事象は #2783・#3128）。

- 書く時点は、リリースの PR を本番ブランチへマージした後（`release-steps merge` の後に workflow が打つ）。publish は
  タグと GitHub Release だけを持ち、含めた PR の範囲を持たない
- 含めた PR は、リリースの PR のコミットを先端から first-parent で辿った件名から、承認資料と同じ規則
  （`included_decide`）で決める（承認資料の範囲 `<前のリリース>..<ベースブランチ>` と同じ範囲）
- 同じ版のリリース記録が既にある PR には書かない（打ち直しで重ねない）。既にあるかは信用する書き手
  （write 以上の権限）の記録だけで見る。信用しない書き手のコメントは印があっても読み飛ばし、壊れていても止まらない。
  信用する書き手の記録の JSON が読めなければ `record_decide.UnreadableRecord` で止める
- リリース後テストの記録は、同じ版のリリース記録を持つ PR へだけ書く。本番ブランチ宛てのリリースの PR は
  リリース記録を持たないので、そこへは書かれない（#3128）
- 読む側（`read`）は write 以上の権限を持つ書き手の記録だけを読む
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ndf import contracts
from ndf.adapters.github import GitHub, Pull
from ndf.adapters.publish import Publisher
from ndf.domains.release import included_decide, record_decide, version_decide

# GitHub が PR のコミットを返す上限（これだけ返れば、範囲を読み切れたか確かめられない）
PULL_COMMITS_CAP = 250
# リリース後テストの記録のスキーマ
VERIFICATION_SCHEMA = contracts.schema_path("release-verification")
# 書いた結果（PR ごと）
WRITTEN, KEPT = "written", "kept"


class NotReleased(Exception):
    """本番ブランチへマージしたリリースの PR でない（開発版・未マージ。記録を書かない）。"""


class RecordError(Exception):
    """記録を書けない（範囲を読み切れない・記録の形が違う・書き先が同じ版のリリース記録を持たない）。"""


class Written(NamedTuple):
    """PR 1 本の結果（`WRITTEN` か `KEPT`）。"""

    number: int
    result: str

    def item(self) -> dict[str, Any]:
        return {"kind": "pull", "number": self.number, "result": self.result}


class Records(NamedTuple):
    """PR 1 本の信用する記録。`release` は最新のリリース記録（無ければ None）、`verifications` は古い順。"""

    release: record_decide.Record | None
    verifications: tuple[dict[str, Any], ...]


def included_of(hub: GitHub, pull: Pull, production: str) -> list[int]:
    """リリースの PR `pull` に含めた PR の番号（小さい順）。コミットが上限まで返れば `RecordError`。"""
    commits = hub.pull_commits(pull.number)
    if len(commits) >= PULL_COMMITS_CAP:
        raise RecordError(f"#{pull.number} のコミットが {PULL_COMMITS_CAP} 件以上あり、含めた PR を読み切れない")
    return included_decide.numbers(record_decide.first_parent_subjects(commits, pull.head_sha), (production,))


def released(hub: GitHub, number: int, production: str) -> tuple[Pull, record_decide.Record]:
    """本番ブランチへマージしたリリースの PR と、その版のリリース記録。そうでなければ `NotReleased`。"""
    pull = hub.pull(number)
    if not pull.merged or pull.base_ref != production:
        raise NotReleased(f"#{number} は {production} へマージしたリリースの PR でない（{pull.base_ref}・merged={pull.merged}）")
    version = version_decide.version_of_branch(pull.head_ref)
    return pull, record_decide.Record(version, number, tuple(included_of(hub, pull, production)))


def _trusted_bodies(hub: GitHub, number: int) -> list[str]:
    """PR `number` のコメントのうち、信用する書き手（`GitHub.trusts`）の本文（古い順）。"""
    return [c.body for c in hub.comments(number) if hub.trusts(c)]


def write(hub: GitHub, number: int, production: str) -> tuple[record_decide.Record, list[Written]]:
    """リリースの PR `number` に含めた PR の各々へ、まだ無ければリリース記録を書く。"""
    _pull, made = released(hub, number, production)
    publisher = Publisher(hub)
    body = record_decide.render_record(made)
    out = []
    for n in made.included:
        if record_decide.has_version(_trusted_bodies(hub, n), made.version):
            out.append(Written(n, KEPT))
            continue
        publisher.comment(n, body)
        out.append(Written(n, WRITTEN))
    return made, out


def read(hub: GitHub, number: int) -> Records:
    """PR `number` の、信用する書き手のリリース記録とリリース後テストの記録。"""
    bodies = _trusted_bodies(hub, number)
    found = record_decide.records(bodies)
    return Records(found[-1] if found else None, tuple(record_decide.verifications(bodies)))


def _versions(found: Sequence[record_decide.Record]) -> str:
    return ", ".join(r.version for r in found) if found else "無し"


def write_verification(hub: GitHub, number: int, payload: Mapping[str, Any]) -> str:
    """リリース後テストの記録を、同じ版のリリース記録を持つ PR `number` へ書く。書いたコメントの URL を返す。

    形がスキーマ `release-verification` に合わない・PR が同じ版の信用するリリース記録を持たなければ `RecordError`。
    """
    errors = contracts.schema_errors(dict(payload), VERIFICATION_SCHEMA)
    if errors:
        raise RecordError(f"リリース後テストの記録の形が違う: {'; '.join(errors)}")
    found = record_decide.records(_trusted_bodies(hub, number))
    if not record_decide.has_record_of(found, payload["version"]):
        raise RecordError(
            f"#{number} は版 {payload['version']} のリリース記録を持たない（持つ版: {_versions(found)}）。"
            "リリース後テストの記録はリリース記録と同じ PR へ書く"
        )
    return Publisher(hub).comment(number, record_decide.render_verification(payload)).url
