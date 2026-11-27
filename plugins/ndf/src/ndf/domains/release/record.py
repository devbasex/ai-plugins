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
  リリース記録を持つ PR を小さい順に示して止まる（`RecordElsewhere`。#3852。書く先を自動で選び直さない）
- 読む側（`read`）は write 以上の権限を持つ書き手の記録だけを読む
- 書く前に、書く token の書き手を読む側が信用するかを確かめ、信用されなければ 1 件も書かずに `RecordError` で止まる。
  信用されない書き手（Actions の `github.token` の github-actions[bot] など）の記録は読まれず、打ち直しで重なるため
- 閉じる手順の読み取り（`read_release`。#4580）は `record_read` にある
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, NoReturn, Optional

from ndf import contracts
from ndf.adapters.github import GitHub, Pull, text_of
from ndf.adapters.publish import Publisher
from ndf.contracts import worker_kinds
from ndf.domains.release import included_decide, names_decide, record_decide
from ndf.domains.release.record_read import Selection, candidates

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
    """渡した PR はリリースの PR で記録を持たず、含めた PR が同じバージョンのリリース記録を持つ（#3852）。
    `holders` は記録を持つ PR の番号（小さい順。先頭が書く先）、`items` は止まるときに示す PR の項目。"""

    def __init__(self, number: int, version: str, holders: tuple[int, ...]) -> None:
        super().__init__(
            f"#{number} はリリースの PR で、v{version} のリリース記録を持たない。記録は含めた PR の各々にある: {_listed(holders)}。"
            f"--pr に番号が最も小さい #{holders[0]} を渡す"
        )
        self.holders = holders
        self.items = [{"kind": "pull", "number": n} for n in holders]


class NotRecordPull(RecordElsewhere):
    """渡した PR は値のリリース記録を持つが記録の PR（含めた PR のうち番号が最も小さい PR）でない（#4580）。"""

    def __init__(self, number: int, version: str, holder: int) -> None:
        RecordError.__init__(
            self,
            f"#{number} は値 {version} の記録の PR でない。リリース後テストの記録は記録の PR（含めた PR のうち番号が最も小さい "
            f"#{holder}）へ書く。--pr に #{holder} を渡す",
        )
        self.holders = (holder,)
        self.items = [{"kind": "pull", "number": holder}]


class RetestWithoutReason(RecordError):
    """今の判定が不合格の届け先と課題の組をテストし直す行に、理由（`retest_reason`）が無い（#4580）。`items` はその組。"""

    def __init__(self, pairs: Sequence[tuple[str, int]]) -> None:
        listed = ", ".join(f"{name} の #{issue}" for name, issue in pairs)
        super().__init__(
            f"今の判定が不合格の組（{listed}）をテストし直す行に retest_reason が無い。不合格の原因がリリースした変更でなく"
            "検証の側にあり、それが直ったときだけ、理由を書いて打ち直す"
        )
        self.items = [{"kind": worker_kinds.DELIVERY, "name": name, "number": issue, "result": "failed"} for name, issue in pairs]


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


def release_commits(hub: GitHub, pull: Pull) -> list[dict]:
    """リリースの PR `pull` のコミット。マージコミットが読めなければ `RecordError`。

    PR のコミットは `pulls/<番号>/commits`（250 件で切れる）ではなく、マージコミットの第 1 親（マージ前の本番
    ブランチ）からリリースの HEAD までの compare をページ送りで読み切る。"""
    if not pull.merge_sha:
        raise RecordError(f"#{pull.number} のマージコミットが読めず、含めた PR を決められない")
    parents = hub.commit_parents(pull.merge_sha)
    if not parents:
        raise RecordError(f"#{pull.number} のマージコミット {pull.merge_sha} が親を持たない")
    return hub.compare_commits(parents[0], pull.head_sha)


def included_pulls(commits: Sequence[dict], pull: Pull, production: str, formats: names_decide.NameFormats) -> list[int]:
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


def pinned_included(hub: GitHub, number: int, production: str, formats: names_decide.NameFormats) -> list[int]:
    """開いている版上げの PR `number` の今の HEAD の含む PR（リリース記録の書き先と同じ、組み立てのコミットの行の番号）。

    組み立ての後に積んだコミットが、承認資料の収集の規則（`included_decide`）で行に無い PR かスプリントのマージを足して
    いれば `RecordError`（行は書き換わらないので、承認資料と記録の書き先がずれる）。行が無いときも `RecordError`。"""
    pull = hub.pull(number)
    commits = hub.compare_commits(pull.base_ref, pull.head_sha)
    approved = record_decide.approved_included(commits, pull.head_sha)
    after = record_decide.subjects_after_approved(commits, pull.head_sha)
    if approved is None or after is None:
        raise RecordError(f"#{number} に組み立てが書いた含む PR の行が無い（版上げの PR を渡す）")
    added = sorted(set(included_decide.numbers(after, (production,), formats.is_release_branch)) - set(approved))
    merged = [s for s in after if included_decide.is_unit_merge(s, formats.is_release_branch)]
    if added or merged:
        found = ", ".join([*(f"#{n}" for n in added), *merged])
        steps = (
            "組み立ての対象のブランチに入れる変更（機能の修正など）だけを、ファイル名でなく変更の目的で選んで退避し"
            "（版上げのコミット・CHANGELOG の本文・guide に従った追記など、このバージョンのために作った内容は退避せず、組み立て直した後に作り直す）、"
            "版上げの PR を閉じてそのブランチを消し（削除は戻せないので、方法にかかわらず実行の前に人の承認を得る）、"
            "退避した変更を組み立ての対象のブランチ宛ての PR にしてプロジェクトの手順でレビュー・マージし（直接コミットや直接の push はしない）、"
            "マージを確かめてから assemble で組み立て直す"
        )
        raise RecordError(f"#{number} に組み立ての後で含む PR が増えた（{found}）。{steps}")
    return approved


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


def landed_commit(hub: GitHub, number: int, production: str, production_sha: Optional[str]) -> tuple[Pull, str]:
    """確かめ 1。本番ブランチ `production` へ入った PR `number`（マージ済みか、`production_sha` が正規の squash）と、反映したコミット
    （GitHub が返さなければ空）。head の名前は問わない。入っていなければ `NotReleased`。"""
    pull = hub.pull(number)
    landed = pull if pull.merged else _landed_by(hub, number, production, production_sha)
    if landed is None or pull.base_ref != production:
        raise NotReleased(f"#{number} は {production} へマージした PR でない（{pull.base_ref}・merged={pull.merged}）")
    return landed, landed.merge_sha


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
    landed, _commit = landed_commit(hub, number, production, production_sha)
    version = formats.branch_version(landed.head_ref)
    if version is None:
        raise NotReleased(f"#{number} の head {landed.head_ref} はリリースブランチ（{formats.branch}）でない")
    commits = release_commits(hub, landed)
    made = record_decide.Record(version, number, tuple(included_pulls(commits, landed, production, formats)))
    by_unit = tuple(record_decide.Record(v, number, nums, unit) for unit, v, nums in record_decide.approved_units(commits, landed.head_sha))
    return landed, made, by_unit


def _trusted_bodies(hub: GitHub, number: int) -> list[str]:
    """PR `number` のコメントのうち、信用する書き手（`GitHub.trusts`）の本文（古い順）。"""
    return [c.body for c in hub.comments(number) if hub.trusts(c)]


def require_trusted_writer(hub: GitHub) -> None:
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
    require_trusted_writer(hub)
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
    （小さい順）と、持つ PR を示せないときの理由を返す（`(番号, "")` か `((), 理由)`）。本番ブランチ `production` が None
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
    holders = tuple(n for n in included if record_decide.has_record_of(record_decide.records(_trusted_bodies(hub, n)), version))
    if not holders:
        return (), f"含めた PR（{_listed(included)}）のどれも v{version} のリリース記録を持たない"
    return holders, ""


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
        raise RecordElsewhere(number, version, holders)
    raise RecordError(
        f"#{number} はバージョン {version} のリリース記録を持たない（持つバージョン: {_versions(found)}）。"
        f"リリース後テストの記録はリリース記録と同じ PR へ書く。{WORKFLOW_HINT}が、記録を持つ PR を示せない: {reason}"
    )


def write_verification(
    hub: GitHub,
    number: int,
    payload: Mapping[str, Any],
    names: Callable[[], tuple[Optional[str], names_decide.NameFormats]] = _no_names,
    unversioned: bool = False,
) -> str:
    """リリース後テストの記録を、同じバージョンのリリース記録を持つ PR `number` へ書く。書いたコメントの URL を返す。

    形がスキーマ `release-verification` に合わない・PR が同じバージョンの信用するリリース記録を持たない・書く token が
    信用されなければ `RecordError`。記録を持たないときだけ `names()` で本番ブランチと書式を読み、`number` がリリースの PR で
    含めた PR が記録を持てば、その番号を持つ `RecordElsewhere` で止まる（何も書かない。#3852）。
    `unversioned`（バージョン番号を持たないリポジトリ）なら `_check_unversioned` の確かめを通してから書く（#4580）。
    """
    errors = contracts.schema_errors(dict(payload), VERIFICATION_SCHEMA)
    if errors:
        raise RecordError(f"リリース後テストの記録の形が違う: {'; '.join(errors)}")
    if unversioned:
        _check_unversioned(hub, number, payload)
    else:
        version = payload["version"]
        found = record_decide.records(_trusted_bodies(hub, number))
        if not record_decide.has_record_of(found, version):
            _raise_without_record(hub, number, version, found, names)
    require_trusted_writer(hub)
    return Publisher(hub).comment(number, record_decide.render_verification(payload)).url


def _check_unversioned(hub: GitHub, number: int, payload: Mapping[str, Any]) -> None:
    """バージョン番号を持たないリポジトリのテストの記録を書く前の確かめ（#4580）。どれかに当たれば `RecordError` で何も書かない。

    1. `environments` がある
    2. 同じ値・同じ単位のリリース記録が PR `number` にある。値に単位が 2 つ以上あれば `unit` が要る
    3. PR `number` が記録の PR（代表の `included` のうち最小）である。違えば `NotRecordPull`
    4. `environments` のどれも、正の一覧にあり反映の記録がある（`record_decide.complete_of` で数える記録）
    5. 今の判定（`record_decide.pair_verdict`）が不合格の組の行は `retest_reason` を持つ。無ければ `RetestWithoutReason`"""
    version, unit, environments = payload["version"], payload.get("unit"), payload.get("environments")
    _require_environments(environments)
    got = candidates(hub, number, Selection(version, None, None))
    _require_unit(got, version, unit)
    targets = got.authoritative.get(version)
    mine = [r for r in got.releases if r.unit == unit]
    rep = _unversioned_representative(number, version, unit, mine, targets)
    _require_record_pull(number, version, rep)
    _require_reflected(environments, mine, rep, targets)
    _require_retest_reasons(got, unit, environments, payload["issues"])


def _require_environments(environments: Any) -> None:
    """確かめ 1: `environments` がある。"""
    if not environments:
        raise RecordError("バージョンの保存先が none のリポジトリのリリース後テストの記録には environments（テストした届け先）が要る")


def _require_unit(got: Any, version: str, unit: Optional[str]) -> None:
    """確かめ 2 の前半: 値に単位があれば `unit` が要る。"""
    units = sorted({r.unit for r in got.releases if r.unit is not None})
    if unit is None and units:
        raise RecordError(f"値 {version} のリリースは単位を持つ（{', '.join(units)}）。unit にテストした単位の名前を書く")


def _unversioned_representative(number: int, version: str, unit: Optional[str], mine: list, targets: Any) -> Any:
    """確かめ 2 の後半: 同じ値・同じ単位のリリース記録の代表を返す。無ければ `RecordError`。"""
    rep = record_decide.representative(mine, targets)
    if rep is None or rep.targets is None or targets is None:
        where = f"値 {version}" + (f"・単位 {unit}" if unit is not None else "")
        raise RecordError(f"#{number} は{where}のバージョンの無いリリース記録を持たない。先に release-steps record --version を打つ")
    return rep


def _require_record_pull(number: int, version: str, rep: Any) -> None:
    """確かめ 3: PR `number` が記録の PR（代表の `included` のうち最小）である。"""
    if number != min(rep.included):
        raise NotRecordPull(number, version, min(rep.included))


def _require_reflected(environments: Any, mine: list, rep: Any, targets: Any) -> None:
    """確かめ 4: `environments` のどれも、正の一覧にあり反映の記録がある。"""
    _complete, missing = record_decide.complete_of(mine, rep, targets)
    outside = [e for e in environments if e not in targets or e in missing]
    if outside:
        raise RecordError(f"反映の記録が無い届け先がある（{', '.join(outside)}）。先にその届け先の release-steps record を打つ")


def _require_retest_reasons(got: Any, unit: Optional[str], environments: Any, issues: Any) -> None:
    """確かめ 5: 今の判定が不合格の組の行は `retest_reason` を持つ。"""
    checks = [v for v in got.verifications if v.get("unit") == unit]
    failed = [
        (e, row["number"])
        for e in environments
        for row in issues
        if not row.get("retest_reason") and record_decide.pair_verdict(checks, e, row["number"]) == "failed"
    ]
    if failed:
        raise RetestWithoutReason(failed)
