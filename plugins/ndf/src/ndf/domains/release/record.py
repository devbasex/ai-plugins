"""リリース記録を含めた PR の各々へ書き、リリース後テストの記録を同じ PR へ書く（#3487。元の事象は #2783・#3128）。

- 書く時点は、リリースの PR を本番ブランチへマージした後（`release-steps merge` の後に workflow が打つ）。publish は
  タグと GitHub Release だけを持ち、含めた PR の範囲を持たない
- 含めた PR は、組み立てがリリースのコミットへ書いた承認資料の「含む PR」の行から読む。行が無ければ、リリースの PR の
  コミットを HEAD から first-parent で辿った件名から、承認資料と同じ規則（`included_decide`）で決める
- 単位が 2 つ以上のときは、同じコミットの単位ごとの行（単位・バージョン・その単位の範囲の PR）から、PR へその単位の
  バージョンの記録を書く（ブランチ名の単位のバージョンを別の単位の PR へ書かない）
- 同じバージョンのリリース記録が既にある PR には書かない（打ち直しで重ねない）。既にあるかは信用する書き手
  （write 以上の権限）の記録だけで見る。信用しない書き手のコメントはマーカーがあっても読み飛ばし、壊れていても止まらない。
  信用する書き手の記録の JSON が読めなければ `record_decide.UnreadableRecord` で止める
- リリース後テストの記録は、同じバージョンのリリース記録を持つ PR へだけ書く。本番ブランチ宛てのリリースの PR は
  リリース記録を持たないので、そこへは書かれない（#3128）。リリースの PR を渡すと、含めた PR のうち同じバージョンの
  リリース記録を持つ PR を示して止まる（`RecordElsewhere`。#3852。書く先を自動で選び直さない）
- 書く先は 1 本にまとめる。同じバージョンのリリース後テストの記録を既に持つ PR を先に、それぞれ小さい順に選ぶ
  （`record_decide.verified_first`）。部分失敗からの再実行でリリース記録が小さい番号の PR に後から付いても、書く先と
  閉じる側の読む先が記録のある PR から動かない。記録を持つ PR を渡しても、記録の `included` の別の PR が既に
  リリース後テストの記録を持てば、その PR を示して止まる
- 読む側（`read`）は write 以上の権限を持つ書き手の記録だけを読む
- 書く前に、書く token の書き手を読む側が信用するかを確かめ、信用されなければ 1 件も書かずに `RecordError` で止まる。
  信用されない書き手（Actions の `github.token` の github-actions[bot] など）の記録は読まれず、打ち直しで重なるため
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, NoReturn, Optional

from ndf import contracts
from ndf.adapters.github import GitHub, Pull, text_of
from ndf.adapters.publish import Publisher
from ndf.domains.release import included_decide, names_decide, record_decide

# リリース後テストの記録のスキーマ
VERIFICATION_SCHEMA = contracts.schema_path("release-verification")
# 書いた結果（PR ごと）
WRITTEN, KEPT = "written", "kept"
# 記録の無い PR で止まるときに足す文（workflow でリリースする形の置き先）
WORKFLOW_HINT = "workflow でリリースする形ではリリース記録は含めた PR の各々にある"


class NotReleased(Exception):
    """本番ブランチへマージしたリリースの PR でない（開発版・未マージ。記録を書かない）。"""


class RecordError(Exception):
    """記録を書けない（マージコミットが読めない・記録の形が違う・書き先が同じバージョンのリリース記録を持たない）。"""


class RecordElsewhere(RecordError):
    """渡した PR へ書かず、書く先の PR を示して止まる（#3852）。`holders` は書く先の候補の番号（先頭が書く先。
    `record_decide.verified_first` の順）、`items` は止まるときに示す PR の項目。"""

    def __init__(self, message: str, holders: tuple[int, ...]) -> None:
        super().__init__(message)
        self.holders = holders
        self.items = [{"kind": "pull", "number": n} for n in holders]


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


def _release_commits(hub: GitHub, pull: Pull) -> list[dict]:
    """リリースの PR `pull` のコミット。マージコミットが読めなければ `RecordError`。

    PR のコミットは `pulls/<番号>/commits`（250 件で切れる）ではなく、マージコミットの第 1 親（マージ前の本番
    ブランチ）からリリースの HEAD までの compare をページ送りで読み切る。"""
    if not pull.merge_sha:
        raise RecordError(f"#{pull.number} のマージコミットが読めず、含めた PR を決められない")
    parents = hub.commit_parents(pull.merge_sha)
    if not parents:
        raise RecordError(f"#{pull.number} のマージコミット {pull.merge_sha} が親を持たない")
    return hub.compare_commits(parents[0], pull.head_sha)


def _included(commits: Sequence[dict], pull: Pull, production: str, formats: names_decide.NameFormats) -> list[int]:
    """リリースの PR `pull` に含めた PR の番号（小さい順）。

    組み立て（`assemble`）がリリースのコミットへ書いた「含む PR」の行（`record_decide.INCLUDED_TRAILER`）を、HEAD から
    first-parent で辿って最も新しいものから読めれば（組み立ての後に直しのコミットを積んでも読む）、
    承認資料が確定した範囲としてその番号を使う（`trunk` の前のリリースからの範囲や、`release_branch` のスプリントの中の PR は
    リリースの PR のコミットの中に無いため）。行が無ければ PR のコミットの first-parent の件名から決める。
    """
    approved = record_decide.approved_included(commits, pull.head_sha)
    if approved is not None:
        return approved
    return included_decide.numbers(record_decide.first_parent_subjects(commits, pull.head_sha), (production,), formats.is_release_branch)


def _tree(commit: Any) -> str:
    """git のコミットの REST の形の木の SHA（読めなければ空）。"""
    tree = commit.get("tree") if isinstance(commit, dict) else None
    return text_of(tree.get("sha")) if isinstance(tree, dict) else ""


def _on_production(hub: GitHub, production: str, sha: str) -> bool:
    """`sha` が本番ブランチ `production` の履歴にあるか（compare の状態が identical か behind）。"""
    got = hub.rest("GET", f"/repos/{hub.repo}/compare/{production}...{sha}")
    return isinstance(got, dict) and got.get("status") in ("identical", "behind")


def landed_squash(hub: GitHub, production: str, sha: str) -> Optional[Pull]:
    """`strict` の squash（早送り）で本番ブランチ `production` へ入れたコミット `sha` が、承認済みのリリースの PR を入れた
    正規のものなら、マージのコミットを `sha` にしたその PR。そうでなければ None（`record` と `publish` が共有する判定）。

    正規とみなすのは次のすべてを満たすとき: `sha` のメッセージの trailer（`TRAILER_PR`・`TRAILER_HEAD`）が PR と承認済みの head を
    指す・PR が本番ブランチ宛てでマージされておらず、head が trailer の head・`sha` の木が head の木と同じ・`sha` が本番ブランチの
    履歴にある（任意のコミットの本文を合わせただけでは通らない）。PR が閉じたかは問わない。`land` は本番ブランチを進めてから PR を
    閉じるため、その間に本番の push で起きた公開が PR を開いたまま読んでも取りこぼさない。"""
    got = hub.rest("GET", f"/repos/{hub.repo}/git/commits/{sha}")
    pointed = record_decide.landed_trailers(text_of(got.get("message"))) if isinstance(got, dict) else None
    if pointed is None:
        return None
    number, head = pointed
    pull = hub.pull(number)
    if pull.merged or pull.base_ref != production or pull.head_sha != head:
        return None
    tree = _tree(got)
    if not tree or tree != _tree(hub.rest("GET", f"/repos/{hub.repo}/git/commits/{head}")) or not _on_production(hub, production, sha):
        return None
    return pull._replace(merge_sha=sha)


def _landed_by(hub: GitHub, number: int, production: str, production_sha: Optional[str]) -> Optional[Pull]:
    """PR `number` を `strict` の squash で本番へ入れたコミット `production_sha` が正規のもの（`landed_squash`）なら、
    マージのコミットを `production_sha` にしたその PR。そうでなければ None。"""
    if production_sha is None:
        return None
    landed = landed_squash(hub, production, production_sha)
    return landed if landed is not None and landed.number == number else None


def released(
    hub: GitHub,
    number: int,
    production: str,
    formats: names_decide.NameFormats = names_decide.NameFormats(None),
    production_sha: Optional[str] = None,
) -> tuple[Pull, record_decide.Record, tuple[record_decide.Record, ...]]:
    """本番ブランチへマージしたリリースの PR と、そのバージョンのリリース記録と、単位ごとのリリース記録。そうでなければ `NotReleased`。

    バージョンは head の名前をリリースブランチの書式（`formats`。省けば既定の書式）で読む。書式に合わなければ `NotReleased`。
    `strict` の squash は PR を閉じる（マージ済みにならない）ので、本番へ入れたコミット `production_sha` が正規の squash
    （`landed_squash`）なら、マージしたものとして読む。単位ごとのリリース記録は、組み立てがリリースのコミットへ書いた単位ごとの
    行（単位が 2 つ以上のとき）から、その単位のバージョンとその単位の範囲の PR で組む（行が無ければ空）。"""
    pull = hub.pull(number)
    landed = pull if pull.merged else _landed_by(hub, number, production, production_sha)
    if landed is None or pull.base_ref != production:
        raise NotReleased(f"#{number} は {production} へマージしたリリースの PR でない（{pull.base_ref}・merged={pull.merged}）")
    version = formats.branch_version(landed.head_ref)
    if version is None:
        raise NotReleased(f"#{number} の head {landed.head_ref} はリリースブランチ（{formats.branch}）でない")
    commits = _release_commits(hub, landed)
    made = record_decide.Record(version, number, tuple(_included(commits, landed, production, formats)))
    by_unit = tuple(record_decide.Record(v, number, nums, unit) for unit, v, nums in record_decide.approved_units(commits, landed.head_sha))
    return landed, made, by_unit


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
    _pull, made, by_unit = released(hub, number, production, formats, production_sha)
    _require_trusted_writer(hub)
    publisher = Publisher(hub)
    out = []
    for rec in by_unit or (made,):  # 単位ごとの行があれば、PR へはその単位のバージョンの記録を書く
        body = record_decide.render_record(rec)
        for n in rec.included:
            if record_decide.has_version(_trusted_bodies(hub, n), rec.version, rec.unit):
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


def _listed(numbers: Sequence[int]) -> str:
    return ", ".join(f"#{n}" for n in numbers) if numbers else "無し"


def _no_names() -> tuple[Optional[str], names_decide.NameFormats]:
    """設定を渡さない呼び出し元の本番ブランチと書式（本番ブランチが None なので記録を持つ PR を探さない）。"""
    return None, names_decide.NameFormats(None)


def _record_holders(
    hub: GitHub, number: int, version: str, production: Optional[str], formats: names_decide.NameFormats
) -> tuple[tuple[int, ...], str]:
    """記録を持たない PR `number` をリリースの PR として読み、含めた PR のうちバージョン `version` のリリース記録を持つ PR の番号
    （`record_decide.verified_first` の順）と、持つ PR を示せないときの理由を返す（`(番号, "")` か `((), 理由)`）。本番ブランチ `production` が None
    （設定が読めない）なら何も読まずに理由を返す。

    1. 対象の選び方: 含めた PR とリリースの PR の判定は `record` と同じ `released`。単位ごとの行があればバージョンの合う単位を、
       無ければバージョンの合うリリース記録そのものを対象にする。
    2. 記録の照らし合わせ: 対象が含めた PR の各々を、書き込み先の検証と同じ `has_record_of` で照らす（単位は照らさない）。"""
    if production is None:
        return (), "ブランチ戦略の設定が読めない"
    # 1. 対象の選び方
    try:
        _pull, made, by_unit = released(hub, number, production, formats)
    except (NotReleased, RecordError) as exc:
        return (), f"#{number} はリリースの PR でない: {exc}"
    targets = [r for r in by_unit if r.version == version] or ([made] if made.version == version else [])
    if not targets:
        return (), f"#{number} はバージョン {made.version} のリリースの PR で、記録のバージョン {version} と違う"
    # 2. 記録の照らし合わせ
    included = sorted({n for r in targets for n in r.included})
    bodies = {n: _trusted_bodies(hub, n) for n in included}
    holders = [n for n in included if record_decide.has_record_of(record_decide.records(bodies[n]), version)]
    if not holders:
        return (), f"含めた PR（{_listed(included)}）のどれも v{version} のリリース記録を持たない"
    return record_decide.verified_first(holders, bodies, version), ""


def _target_reason(hub: GitHub, number: int, version: str) -> str:
    """書く先 `number` を選んだ理由（止まる文に載せる）。"""
    if record_decide.has_verification_of(_trusted_bodies(hub, number), version):
        return f"v{version} のリリース後テストの記録を既に持つ"
    return "番号が最も小さい"


def _verified_elsewhere(hub: GitHub, number: int, version: str, mine: record_decide.Record) -> tuple[int, ...]:
    """PR `number` のリリース記録 `mine` の `included` のうち、`number` 以外で同じバージョンのリリース記録と
    リリース後テストの記録を両方持つ PR（小さい順）。"""
    others = sorted({n for n in mine.included if n != number})
    bodies = {n: _trusted_bodies(hub, n) for n in others}
    return tuple(
        n
        for n in others
        if record_decide.has_record_of(record_decide.records(bodies[n]), version)
        and record_decide.has_verification_of(bodies[n], version)
    )


def _raise_without_record(
    hub: GitHub,
    number: int,
    version: str,
    found: Sequence[record_decide.Record],
    names: Callable[[], tuple[Optional[str], names_decide.NameFormats]],
) -> NoReturn:
    """バージョン `version` のリリース記録を持たない PR `number` への書き込みを止める。`names()` で本番ブランチと書式を読み、
    含めた PR が記録を持てば `RecordElsewhere`、持つ PR を示せなければ `RecordError`。"""
    production, formats = names()
    holders, reason = _record_holders(hub, number, version, production, formats)
    if holders:
        raise RecordElsewhere(
            f"#{number} はリリースの PR で、v{version} のリリース記録を持たない。記録は含めた PR の各々にある: {_listed(sorted(holders))}。"
            f"--pr に {_target_reason(hub, holders[0], version)} #{holders[0]} を渡す",
            holders,
        )
    raise RecordError(
        f"#{number} はバージョン {version} のリリース記録を持たない（持つバージョン: {_versions(found)}）。"
        f"リリース後テストの記録はリリース記録と同じ PR へ書く。{WORKFLOW_HINT}が、記録を持つ PR を示せない: {reason}"
    )


def write_verification(
    hub: GitHub,
    number: int,
    payload: Mapping[str, Any],
    names: Callable[[], tuple[Optional[str], names_decide.NameFormats]] = _no_names,
) -> str:
    """リリース後テストの記録を、同じバージョンのリリース記録を持つ PR `number` へ書く。書いたコメントの URL を返す。

    形がスキーマ `release-verification` に合わない・PR が同じバージョンの信用するリリース記録を持たない・書く token が
    信用されなければ `RecordError`。記録を持たないときだけ `names()` で本番ブランチと書式を読み、`number` がリリースの PR で
    含めた PR が記録を持てば、その番号を持つ `RecordElsewhere` で止まる（何も書かない。#3852）。記録を持つ PR でも、
    自身がリリース後テストの記録を持たず、リリース記録の `included` の別の PR が持てば、その PR を示して止まる。
    """
    errors = contracts.schema_errors(dict(payload), VERIFICATION_SCHEMA)
    if errors:
        raise RecordError(f"リリース後テストの記録の形が違う: {'; '.join(errors)}")
    version = payload["version"]
    bodies = _trusted_bodies(hub, number)
    found = record_decide.records(bodies)
    if record_decide.has_record_of(found, version):
        mine = [r for r in found if r.version == version][-1]
        kept = () if record_decide.has_verification_of(bodies, version) else _verified_elsewhere(hub, number, version, mine)
        if kept:
            raise RecordElsewhere(
                f"#{number} は v{version} のリリース後テストの記録を持たず、その記録は {_listed(kept)} にある。"
                f"記録は 1 本の PR にまとめるので、--pr に #{kept[0]} を渡す",
                kept,
            )
        _require_trusted_writer(hub)
        return Publisher(hub).comment(number, record_decide.render_verification(payload)).url
    _raise_without_record(hub, number, version, found, names)
