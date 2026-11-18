"""リリース記録を含めた PR の各々へ書き、リリース後テストの記録を同じ PR へ書く（#3487。元の事象は #2783・#3128）。

- 書く時点は、リリースの PR を本番ブランチへマージした後（`release-steps merge` の後に workflow が打つ）。publish は
  タグと GitHub Release だけを持ち、含めた PR の範囲を持たない
- 含めた PR は、組み立てがリリースのコミットへ書いた承認資料の「含む PR」の行から読む。行が無ければ、リリースの PR の
  コミットを HEAD から first-parent で辿った件名から、承認資料と同じ規則（`included_decide`）で決める
- 同じバージョンのリリース記録が既にある PR には書かない（打ち直しで重ねない）。既にあるかは信用する書き手
  （write 以上の権限）の記録だけで見る。信用しない書き手のコメントはマーカーがあっても読み飛ばし、壊れていても止まらない。
  信用する書き手の記録の JSON が読めなければ `record_decide.UnreadableRecord` で止める
- リリース後テストの記録は、同じバージョンのリリース記録を持つ PR へだけ書く。本番ブランチ宛てのリリースの PR は
  リリース記録を持たないので、そこへは書かれない（#3128）
- 読む側（`read`）は write 以上の権限を持つ書き手の記録だけを読む
- 書く前に、書く token の書き手を読む側が信用するかを確かめ、信用されなければ 1 件も書かずに `RecordError` で止まる。
  信用されない書き手（Actions の `github.token` の github-actions[bot] など）の記録は読まれず、打ち直しで重なるため
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters.github import GitHub, Pull, text_of
from ndf.core.branch_strategy import TRAILER_HEAD, TRAILER_PR
from ndf.adapters.publish import Publisher
from ndf.domains.release import included_decide, names_decide, record_decide

# リリース後テストの記録のスキーマ
VERIFICATION_SCHEMA = contracts.schema_path("release-verification")
# 書いた結果（PR ごと）
WRITTEN, KEPT = "written", "kept"


class NotReleased(Exception):
    """本番ブランチへマージしたリリースの PR でない（開発版・未マージ。記録を書かない）。"""


class RecordError(Exception):
    """記録を書けない（マージコミットが読めない・記録の形が違う・書き先が同じバージョンのリリース記録を持たない）。"""


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


def included_of(hub: GitHub, pull: Pull, production: str, formats: names_decide.NameFormats = names_decide.NameFormats(None)) -> list[int]:
    """リリースの PR `pull` に含めた PR の番号（小さい順）。マージコミットが読めなければ `RecordError`。

    PR のコミットは `pulls/<番号>/commits`（250 件で切れる）ではなく、マージコミットの第 1 親（マージ前の本番
    ブランチ）からリリースの HEAD までの compare をページ送りで読み切る。

    組み立て（`assemble`）がリリースのコミットへ書いた「含む PR」の行（`record_decide.INCLUDED_TRAILER`）を、HEAD から
    first-parent で辿って最も新しいものから読めれば（組み立ての後に直しのコミットを積んでも読む）、
    承認資料が確定した範囲としてその番号を使う（`trunk` の前のリリースからの範囲や、`release_branch` のスプリントの中の PR は
    リリースの PR のコミットの中に無いため）。行が無ければ PR のコミットの first-parent の件名から決める。
    """
    if not pull.merge_sha:
        raise RecordError(f"#{pull.number} のマージコミットが読めず、含めた PR を決められない")
    parents = hub.commit_parents(pull.merge_sha)
    if not parents:
        raise RecordError(f"#{pull.number} のマージコミット {pull.merge_sha} が親を持たない")
    commits = hub.compare_commits(parents[0], pull.head_sha)
    approved = record_decide.approved_included(commits, pull.head_sha)
    if approved is not None:
        return approved
    return included_decide.numbers(record_decide.first_parent_subjects(commits, pull.head_sha), (production,), formats.is_release_branch)


def _landed_by(hub: GitHub, pull: Pull, production_sha: Optional[str]) -> Optional[Pull]:
    """閉じた PR を `strict` の squash（早送り）で本番へ入れたコミット `production_sha` の trailer が、その PR と承認済みの
    head を指していれば、マージのコミットを `production_sha` にした PR。指さなければ None。"""
    if production_sha is None or pull.state != "closed":
        return None
    got = hub.rest("GET", f"/repos/{hub.repo}/git/commits/{production_sha}")
    lines = text_of(got.get("message")).splitlines() if isinstance(got, dict) else []
    if f"{TRAILER_PR}: {pull.number}" in lines and f"{TRAILER_HEAD}: {pull.head_sha}" in lines:
        return pull._replace(merge_sha=production_sha)
    return None


def released(
    hub: GitHub,
    number: int,
    production: str,
    formats: names_decide.NameFormats = names_decide.NameFormats(None),
    production_sha: Optional[str] = None,
) -> tuple[Pull, record_decide.Record]:
    """本番ブランチへマージしたリリースの PR と、そのバージョンのリリース記録。そうでなければ `NotReleased`。

    バージョンは head の名前をリリースブランチの書式（`formats`。省けば既定の書式）で読む。書式に合わなければ `NotReleased`。
    `strict` の squash は PR を閉じる（マージ済みにならない）ので、本番へ入れたコミット `production_sha` の trailer が
    その PR と承認済みの head を指せば、マージしたものとして読む。"""
    pull = hub.pull(number)
    landed = pull if pull.merged else _landed_by(hub, pull, production_sha)
    if landed is None or pull.base_ref != production:
        raise NotReleased(f"#{number} は {production} へマージしたリリースの PR でない（{pull.base_ref}・merged={pull.merged}）")
    version = formats.branch_version(landed.head_ref)
    if version is None:
        raise NotReleased(f"#{number} の head {landed.head_ref} はリリースブランチ（{formats.branch}）でない")
    return landed, record_decide.Record(version, number, tuple(included_of(hub, landed, production, formats)))


def _trusted_bodies(hub: GitHub, number: int) -> list[str]:
    """PR `number` のコメントのうち、信用する書き手（`GitHub.trusts`）の本文（古い順）。"""
    return [c.body for c in hub.comments(number) if hub.trusts(c)]


def _require_trusted_writer(hub: GitHub) -> None:
    """書く token の書き手が読む側（`GitHub.trusts`）に信用されなければ `RecordError`。"""
    if not hub.token_trusted():
        raise RecordError(
            "記録を書く token の書き手が write 以上の権限を持つ利用者でなく、書いた記録は読まれない。"
            "利用者の token（workflow では secrets.NDF_RELEASE_TOKEN）で書く"
        )


def write(
    hub: GitHub,
    number: int,
    production: str,
    formats: names_decide.NameFormats = names_decide.NameFormats(None),
    production_sha: Optional[str] = None,
) -> tuple[record_decide.Record, list[Written]]:
    """リリースの PR `number` に含めた PR の各々へ、まだ無ければリリース記録を書く。書く token が信用されなければ `RecordError`。
    `production_sha` は本番へ入れたコミット（`strict` の squash で閉じた PR を読むため。`released`）。"""
    _pull, made = released(hub, number, production, formats, production_sha)
    _require_trusted_writer(hub)
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
    """リリース後テストの記録を、同じバージョンのリリース記録を持つ PR `number` へ書く。書いたコメントの URL を返す。

    形がスキーマ `release-verification` に合わない・PR が同じバージョンの信用するリリース記録を持たない・書く token が
    信用されなければ `RecordError`。
    """
    errors = contracts.schema_errors(dict(payload), VERIFICATION_SCHEMA)
    if errors:
        raise RecordError(f"リリース後テストの記録の形が違う: {'; '.join(errors)}")
    found = record_decide.records(_trusted_bodies(hub, number))
    if not record_decide.has_record_of(found, payload["version"]):
        raise RecordError(
            f"#{number} はバージョン {payload['version']} のリリース記録を持たない（持つバージョン: {_versions(found)}）。"
            "リリース後テストの記録はリリース記録と同じ PR へ書く"
        )
    _require_trusted_writer(hub)
    return Publisher(hub).comment(number, record_decide.render_verification(payload)).url
