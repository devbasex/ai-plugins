"""直さずに残した指摘の起票と、起票の記録（#3780）。

起票の記録は、元の PR（状態ファイルの `pr`）に置く専用のコメントである。Issue を作る前に、試みる指摘の識別値を書く。状態の
保存先が違う打ち直しも同じコメントを読み、URL の無い識別値は検索と一覧で確かめてから起票する（2 件作らない）。記録の URL は、
指す Issue を読んで確かめてから使う。何を起票するかと、
文と記録の形は `leftover_decide` が持つ。GitHub の失敗では指摘を失わず、行の `issue_error` と注意に残す。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Optional, Protocol

from ndf.adapters import git
from ndf.adapters.github import BOT_SUFFIX, Viewer
from ndf.adapters.github_pulls import PER_PAGE, Authored, account_id_of
from ndf.contracts import limits
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import leftover_decide, report_decide, settings, store, threads
from ndf.domains.crossreview.leftover_decide import PARKED, Filed, Listed

# 状態ファイルのキー: 起票に使ったアカウント ID・起票の記録のコメントの番号・一覧が上限を超えたときの上限の値
ACCOUNT_IDS = "issue_account_ids"
RECORD_ID = "issue_record_id"
LIST_FULL = "issue_list_full"
# 前の実行の記録のコメントから引き継いだ、直さずに残した指摘の起票の URL の並び。記録の本文は伏せてあり、伏せる値は実行する
# 環境で変わるので、本文でなく Issue の目印で指摘と照らす
CARRIED_URLS = "issue_carried_urls"
# 起票を見送った理由の状態のキー。見送りの理由はここにだけ持ち、行の `issue_error`（起票の失敗）と分ける。
# 起票先を GitHub にしない設定で見送ったときは値が null（キーが無い状態と分ける）
SKIPPED = "issue_skipped"
# 起票先を GitHub にする `.ndf/project.json` の `issues.primary` の値
GITHUB = "github"
# `issues.primary` が未設定（読めない・`unknown` を含む）のときの注意
UNDECLARED = (
    f"直さずに残した指摘を起票していない（起票するには {settings.settings_path(Path(), 'project').as_posix()} の "
    f"issues.primary を {GITHUB} に設定する）"
)
# 起票の記録の URL が指す Issue を、GraphQL でまとめて読むときの欄
ISSUE_FIELDS = "url body createdAt authorAssociation author { __typename login ... on User { databaseId } ... on Bot { databaseId } }"

Entries = dict[str, dict[str, Any]]


class Comment(NamedTuple):
    """PR のコメント（`updated_at` は GitHub が記録した更新時刻。`author_id` は作者のアカウント ID で、無ければ 0）。"""

    id: int
    body: str
    author: str
    association: str
    updated_at: str
    author_id: int = 0


class TooMany(Exception):
    """Issue の一覧が、確かめ終える前に `github.list-pages` のページ数（`cap`）を超えた。`listed` は超えるまでに読めた分。"""

    def __init__(self, cap: int, listed: Sequence[Listed] = ()):
        self.cap, self.listed = cap, list(listed)
        super().__init__(f"Issue の一覧が {cap} ページを超える（上限 {threads.LIST_PAGES}）")


class Port(Protocol):
    """起票の読み書きの口（テストは GitHub の偽物）。失敗は例外で、GitHub が断ったものは HTTP の状態 `status` を持つ。"""

    def viewer(self) -> Viewer: ...

    def trusts(self, author: str, association: str) -> bool: ...

    def pr_url(self, pr: int) -> str: ...

    def issues_url(self) -> str: ...

    def redact(self, *texts: str) -> list[str]: ...

    def comments(self, pr: int) -> Iterable[Comment]: ...

    def comment(self, comment_id: int) -> Comment: ...

    def post(self, pr: int, body: str) -> tuple[int, str]: ...

    def edit(self, comment_id: int, body: str) -> tuple[int, str]: ...

    def search(self, ident: str) -> list[Listed]: ...

    def recent(self, since: str) -> list[Listed]: ...

    def issues(self, urls: Sequence[str]) -> dict[str, Optional[Listed]]: ...

    def create(self, title: str, body: str, mark: str) -> str: ...


def primary_of(main: Optional[Path]) -> tuple[Optional[str], Optional[str]]:
    """メインディレクトリ `main` の `.ndf/project.json` の `issues.primary` と、未設定（読めない・`unknown` を含む）のときの注意。"""
    value: Any = None
    if main is not None:
        try:
            read = json.loads(settings.settings_path(main, "project").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            read = None
        issues = read.get("issues") if isinstance(read, Mapping) else None
        value = issues.get("primary") if isinstance(issues, Mapping) else None
    if isinstance(value, str) and value and value != "unknown":
        return value, None
    return None, UNDECLARED


def unconfirmed_target(main: Path, state_repo: Optional[str], make: Callable[[Path], git.Git] = git.Git) -> Optional[str]:
    """起票先が `main` の設定のリポジトリだと確かめられないときの注意（確かめられれば None）。起票先は状態ファイルの対象の
    リポジトリ（`state_repo`）だけで決め、無ければ確かめられない。`--repo` との食い違いは、呼び手が公開の前に断る。"""
    skipped = "直さずに残した指摘を起票していない"
    if not state_repo:
        return f"{skipped}（状態ファイルに対象のリポジトリが無く、元の PR のリポジトリを確かめられない）"
    try:
        own = make(main).remote_repo()
    except (git.GitError, OSError) as exc:
        return f"{skipped}（メインディレクトリのリポジトリを確かめられない: {exc}）"
    if own.casefold() != state_repo.casefold():
        return f"{skipped}（起票先 {state_repo} とメインディレクトリのリポジトリ {own} が違う）"
    return None


def candidates(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """起票の候補の行（直さずに残した未解決の指摘のうち、まだ起票しておらず、起票する種類か引き継いだ起票の URL を持つもの）。"""
    return [
        row
        for row in store.list_of(data, store.FINDINGS)
        if isinstance(row, dict)
        and row.get(PARKED)
        and row.get("outcome") == fin.UNRESOLVED
        and not row.get(Filed.URL)
        and isinstance(row.get("text"), str)
        and (leftover_decide.files_issue(row) or row.get(Filed.PRIOR))
    ]


def defer(data: dict[str, Any], rows: Iterable[dict[str, Any]], reason: Optional[str]) -> None:
    """起票を見送った理由を状態に残し、候補の行の前の起票の失敗を消す。`reason` が None（起票先を GitHub にしないと
    設定している）でもキーは null で残し、設定を変えた後の打ち直しが起票できるようにする。理由が変われば公開済みの記録は古いので、打ち直しが公開し直すようにする。"""
    if data.get(SKIPPED) != reason:
        data.pop(report_decide.PUBLISHED_KEY, None)
    data[SKIPPED] = reason
    for row in rows:
        row.pop(Filed.ERROR, None)


def _str_of(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value if isinstance(value, str) else ""


def _place(row: Mapping[str, Any]) -> str:
    """指摘の場所（`<パス>:<行>`。パスが無ければ `section`）。"""
    path, line = _str_of(row, "path"), row.get("line")
    if not path:
        return _str_of(row, "section")
    return f"{path}:{line}" if type(line) is int else path


def _refused(exc: Exception) -> bool:
    """GitHub が作成をはっきり拒んだ（4xx）失敗か。ほかは作られたかどうかが分からない。"""
    status = getattr(exc, "status", None)
    return type(status) is int and 400 <= status < 500


def _reason(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


@dataclass
class _Filing:
    """`file_issues` の 1 回分。`rows` は識別値ごとの候補の行、`errors` は起票できなかった識別値の理由。"""

    data: dict[str, Any]
    ref: store.StateRef
    port: Port
    save: Callable[[], None]
    rows: dict[str, list[dict[str, Any]]]
    errors: dict[str, str] = field(default_factory=dict)
    entries: Entries = field(default_factory=dict)
    record_id: Optional[int] = None
    # 識別値ごとの伏せた後の (本文, 起きること, 場所) と、元の PR と今の PR の URL
    texts: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)
    me: int = 0  # 実行しているアカウントの ID

    def account_ids(self) -> list[int]:
        return [x for x in store.list_of(self.data, ACCOUNT_IDS) if type(x) is int]

    def fail(self, ident: str, reason: str) -> None:
        self.errors[ident] = reason

    def done(self, ident: str, url: str) -> None:
        for row in self.rows[ident]:
            row[Filed.URL] = url
            row.pop(Filed.ERROR, None)
            row.pop(Filed.PRIOR, None)

    def read(self) -> None:
        """起票の記録を読む。`at` の無い項目（記録を書いた後に中断した）には、コメントの更新時刻を当てる。"""
        self.me = self.port.viewer().account_id
        if not self.me:
            # ID が分からないまま進むと、自分の書いた記録と Issue を見分けられず 2 件作る
            raise ValueError("実行しているアカウントの ID を読めない")
        found = self._record()
        if found is None:
            return
        self.record_id = self.data[RECORD_ID] = found.id
        self.entries = leftover_decide.record_entries(found.body, self.port.issues_url())
        for entry in self.entries.values():
            entry.setdefault("at", found.updated_at)

    def _trusts(self, author: str, author_id: int, association: str) -> bool:
        return leftover_decide.trusted(author_id, self.me, self.account_ids()) or self.port.trusts(author, association)

    def _usable(self, comment: Comment) -> bool:
        return comment.body.startswith(leftover_decide.RECORD_MARK) and self._trusts(comment.author, comment.author_id, comment.association)

    def _record(self) -> Optional[Comment]:
        """起票の記録のコメント。番号を持てばその番号で読み、無い（消された）ときだけ元の PR のコメントの一覧から探す。"""
        known = self.data.get(RECORD_ID)
        if type(known) is int:
            try:
                comment = self.port.comment(known)
            except Exception as exc:
                # 消されたコメントだけ探し直し、ほかは呼び手へ返す
                if getattr(exc, "status", None) != 404:
                    raise
            else:
                if self._usable(comment):
                    return comment
        return next((c for c in self.port.comments(self.ref.origin_pr) if self._usable(c)), None)

    def redact(self, idents: Sequence[str]) -> None:
        """起票に使う値を、全文のまま 1 回で伏せる（切り出しは伏せた後）。"""
        prs = list(dict.fromkeys((self.ref.origin_pr, self.ref.current_pr)))
        raw = [self.port.pr_url(pr) for pr in prs]
        for ident in idents:
            row = self.rows[ident][0]
            raw += [_str_of(row, "text"), _str_of(row, "impact"), _place(row)]
        safe = self.port.redact(*raw)
        self.urls = safe[: len(prs)]
        rest = safe[len(prs) :]
        self.texts = {ident: (rest[i * 3], rest[i * 3 + 1], rest[i * 3 + 2]) for i, ident in enumerate(idents)}

    def _match(self, ident: str, listed: Sequence[Listed]) -> Optional[str]:
        """この指摘を扱う、信用する作者の Issue の URL。"""
        for issue in listed:
            if leftover_decide.matches_finding(issue, ident, self.ref.origin_pr) and self._trusts(
                issue.author, issue.author_id, issue.association
            ):
                return issue.url
        return None

    def _search(self, idents: Sequence[str]) -> tuple[dict[str, str], list[str]]:
        """識別値で検索した結果を、見つかった URL と、見つからない識別値に分ける。"""
        found: dict[str, str] = {}
        missing: list[str] = []
        for ident in idents:
            try:
                url = self._match(ident, self.port.search(ident))
            except Exception as exc:  # noqa: BLE001 ネットワーク: この候補を起票せず、打ち直しで確かめる
                self.fail(ident, _reason(exc))
                continue
            if url is None:
                missing.append(ident)
            else:
                found[ident] = url
        return found, missing

    def _listed(self, idents: Sequence[str], manual: bool) -> tuple[dict[str, str], list[str]]:
        """検索に無い識別値を、試みた時刻以後に作られた Issue の一覧（1 回だけ読む）で確かめ、見つかった URL と、無いと確かめた
        識別値を返す。前の一覧が上限を超えていれば、上限を上げるか利用者が打ち直すまで読まない。"""
        if not idents:
            return {}, []
        full = self.data.get(LIST_FULL)
        cut: Optional[TooMany] = None
        try:
            if not manual and type(full) is int and int(limits.value(threads.LIST_PAGES, "count")) <= full:
                raise TooMany(full)
            listed = self.port.recent(min(self.entries[i]["at"] for i in idents))
        except TooMany as exc:
            # 読めた分の一致は使い、見つからない候補だけを保留する
            cut, listed, self.data[LIST_FULL] = exc, exc.listed, exc.cap
        except Exception as exc:  # noqa: BLE001 ネットワーク: 無いと確かめられない候補は起票しない
            for ident in idents:
                self.fail(ident, _reason(exc))
            return {}, []
        found = {i: url for i in idents if (url := self._match(i, listed)) is not None}
        missing = [i for i in idents if i not in found]
        if cut is None:
            return found, missing
        for ident in missing:
            self.fail(ident, _reason(cut))
        return found, []

    def url_of(self, ident: str) -> Optional[str]:
        """起票の記録にある Issue の URL。"""
        entry = self.entries[ident] if ident in self.entries else {}
        return str(entry["url"]) if "url" in entry else None

    def _named(self, ident: str, urls: Sequence[Optional[str]], read: Mapping[str, Optional[Listed]]) -> Optional[str]:
        """`urls` のうち、指す Issue が信用する作者のこの指摘の Issue である最初の URL。"""
        text = self.rows[ident][0]["text"]
        for url in urls:
            issue = read[url] if url else None
            if (
                issue
                and leftover_decide.names_finding(issue, ident, text)
                and self._trusts(issue.author, issue.author_id, issue.association)
            ):
                return url
        return None

    def known(self) -> None:
        """起票の記録・行の引き継ぎ（`prior_issue`）・状態の引き継ぎ（`CARRIED_URLS`）の URL が信用する作者のこの指摘の Issue なら、
        起票せず行に書く。起票の記録と行の URL が違えば捨て、起票の記録か作る候補として扱う。読めなければ起票も採用もしない。
        Issue はまとめて読む（`GitHub.nodes`）。"""
        recorded = {ident: url for ident in self.rows if (url := self.url_of(ident))}
        carried = {ident: str(row[Filed.PRIOR]) for ident, rows in self.rows.items() for row in rows if row.get(Filed.PRIOR)}
        urls = carried | recorded
        pool = [url for url in store.list_of(self.data, CARRIED_URLS) if isinstance(url, str)]
        wanted = list(dict.fromkeys([*urls.values(), *pool]))
        if not wanted:
            return
        try:
            read = self.port.issues(wanted)
            usable = {ident: url for ident in self.rows if (url := self._named(ident, [urls.get(ident), *pool], read))}
        except Exception as exc:  # noqa: BLE001 ネットワーク: 確かめられない URL は使わず、打ち直しで確かめる
            # 状態の引き継ぎの URL は、どの候補の Issue かが読むまで分からないので、全部の候補を保留する
            for ident in self.rows if pool else urls:
                self.fail(ident, _reason(exc))
            return
        for ident, rows in self.rows.items():
            for row in rows:
                row.pop(Filed.PRIOR, None)
            if ident in usable:
                self.entries.setdefault(ident, {})["url"] = usable[ident]
                self.done(ident, usable[ident])
            elif ident in recorded:
                del self.entries[ident]["url"]

    def sort(self, manual: bool) -> tuple[list[str], bool]:
        """URL の記録の無い候補を確かめ、作る識別値と、記録に無かった URL を見つけたかを返す。作成を試みた記録のある識別値は、
        扱う Issue が無いと確かめられたときだけ作る。引き継いだ URL を確かめられなかった識別値と、起票する種類を持たない
        識別値は作らない。"""
        pending = [i for i in self.rows if not self.url_of(i) and i not in self.errors]
        unknown = [i for i in pending if i in self.entries]
        if pending:
            self.redact(pending)
        searched, missing = self._search(unknown)
        listed, absent = self._listed(missing, manual)
        found = {**searched, **listed}
        for ident, url in found.items():
            self.entries[ident] = {**self.entries[ident], "url": url}
            self.done(ident, url)
        if len(found) + len(absent) == len(unknown):
            self.data.pop(LIST_FULL, None)
        fresh = [i for i in pending if i not in self.entries or i in absent]
        return [i for i in fresh if any(map(leftover_decide.files_issue, self.rows[i]))], bool(found)

    def write(self) -> str:
        """起票の記録を書き、GitHub が記録した更新時刻を返す。"""
        body = leftover_decide.record_body(self.entries, self.port.issues_url())
        if self.record_id is None:
            self.record_id, at = self.port.post(self.ref.origin_pr, body)
        else:
            self.record_id, at = self.port.edit(self.record_id, body)
        self.data[RECORD_ID] = self.record_id
        return at

    def announce(self, idents: Sequence[str]) -> Optional[str]:
        """作る前に、使うアカウント ID を状態へ残し、起票の記録へ識別値を書く。試みた時刻（この書き込みの応答の更新時刻）を返し、
        書けなければ None（作らない）。"""
        self.data[ACCOUNT_IDS] = list(dict.fromkeys([*self.account_ids(), self.me]))
        self.save()
        for ident in idents:
            self.entries[ident] = {}
        try:
            at = self.write()
        except Exception as exc:  # noqa: BLE001 ネットワーク: 記録を書けないまま作ると、打ち直しで 2 件になる
            for ident in idents:
                self.fail(ident, f"起票の記録を書けない: {_reason(exc)}")
            return None
        self.save()
        return at

    def create(self, idents: Sequence[str], at: str) -> None:
        for ident in idents:
            title, body = leftover_decide.issue_parts(*self.texts[ident], self.urls)
            try:
                url = self.port.create(title, body, leftover_decide.issue_mark(ident))
            except Exception as exc:  # noqa: BLE001 ネットワーク: 指摘ごとに失敗を残し、ほかの候補を続ける
                self.fail(ident, _reason(exc))
                if _refused(exc):
                    del self.entries[ident]
                else:
                    self.entries[ident] = {"at": at}
                continue
            self.entries[ident] = {"at": at, "url": url}
            self.done(ident, url)

    def run(self, manual: bool) -> list[str]:
        try:
            self.read()
        except Exception as exc:  # noqa: BLE001 ネットワーク: 記録を読めなければ、どの候補も起票しない
            for ident in self.rows:
                self.fail(ident, f"起票の記録を読めない: {_reason(exc)}")
            return self._finish()
        self.known()
        try:
            fresh, found = self.sort(manual)
        except Exception as exc:  # noqa: BLE001 ネットワーク: 伏せ字か作者の権限の確かめに失敗すれば起票しない
            for ident in self.rows:
                if not self.url_of(ident):
                    self.fail(ident, _reason(exc))
            return self._finish()
        at = self.announce(fresh) if fresh else None
        if at is not None:
            self.create(fresh, at)
        # 結果の書き込み（作ったとき、または作るものが無く、検索と一覧で見つけた URL を記録へ足すとき）
        return self._close(at is not None or bool(found and not fresh))

    def _close(self, record: bool) -> list[str]:
        """`record` なら起票の記録へ結果を書き、注意を返す。"""
        if record:
            try:
                self.write()
            except Exception as exc:  # noqa: BLE001 ネットワーク: 次の公開が、記録の識別値を検索と一覧で確かめる
                return [*self._finish(), f"起票の記録に結果を書けなかった: {_reason(exc)}"]
        return self._finish()

    def _finish(self) -> list[str]:
        """失敗を行に書いて保存し、理由ごとにまとめた注意を返す。"""
        reasons: dict[str, list[str]] = {}
        for ident, reason in self.errors.items():
            for row in self.rows[ident]:
                row[Filed.ERROR] = reason
                reasons.setdefault(reason, []).append(str(row.get("id")))
        self.save()
        return [f"指摘 {'・'.join(ids)} を起票できなかった: {reason}" for reason, ids in reasons.items()]


def file_issues(
    data: dict[str, Any],
    ref: store.StateRef,
    port: Port,
    save: Callable[[], None],
    *,
    manual: bool = False,
) -> list[str]:
    """起票の候補を起票し、行の `issue`・`issue_error`・`prior_issue` と状態の `issue_account_ids`・`issue_record_id`・`issue_list_full` を書いて
    注意を返す。候補が無ければ GitHub を呼ばない。呼び手は起票先が `github` のときだけ呼ぶ。`manual` は利用者が打った公開である。

    呼び手は、今の PR の番号と元の PR の番号の公開のロック（同じ番号なら 1 回）を持つ。"""
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in candidates(data):
        rows.setdefault(leftover_decide.identity(ref.origin_pr, row["text"]), []).append(row)
    if not rows:
        return []
    return _Filing(data, ref, port, save, rows).run(manual)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _author(row: Mapping[str, Any]) -> str:
    user = row.get("user")
    return _text(user.get("login")) if isinstance(user, Mapping) else ""


def _comment(row: Mapping[str, Any]) -> Comment:
    return Comment(
        int(row["id"]),
        _text(row.get("body")),
        _author(row),
        _text(row.get("author_association")),
        _text(row.get("updated_at")),
        account_id_of(row.get("user")),
    )


def _listed_of(row: Mapping[str, Any]) -> Listed:
    return Listed(
        _text(row.get("html_url")),
        _text(row.get("body")),
        _author(row),
        _text(row.get("author_association")),
        _text(row.get("created_at")),
        "/pull/" in _text(row.get("html_url")),
        account_id_of(row.get("user")),
    )


def _listed_node(node: Mapping[str, Any]) -> Listed:
    """GraphQL で読んだ Issue。bot の login は REST と同じ `<名前>[bot]` にそろえる。"""
    author = node.get("author")
    author = author if isinstance(author, Mapping) else {}
    login = _text(author.get("login"))
    if login and author.get("__typename") == "Bot":
        login += BOT_SUFFIX
    return Listed(
        _text(node.get("url")),
        _text(node.get("body")),
        login,
        _text(node.get("authorAssociation")),
        _text(node.get("createdAt")),
        False,
        account_id_of(author),
    )


class GitHubIssues:
    """GitHub の API で起票を読み書きする `Port`。Issue は `publish.Publisher` の検査を通し、記録のコメントは呼び手が
    固定の文と形を確かめた値で組んだ本文を書く。書き込みは二次レート制限なら待って打ち直す（`threads.patient`）。"""

    def __init__(
        self,
        hub: Any,
        publisher: Any,
        errors: type[Exception],
        viewer: Callable[[], Viewer],
        sleep: Callable[[float], None] = time.sleep,
        block: Optional[Callable[[float], None]] = None,
    ):
        """`errors` は GitHub が断ったときの例外の型、`viewer` は実行している利用者を読む関数（`GitHub.viewer`。1 回だけ呼ぶ）。"""
        self.hub, self.publisher, self.errors = hub, publisher, errors
        self.sleep, self.block = sleep, block
        self._read_viewer = viewer
        self._me: Optional[Viewer] = None

    def _write(self, call: Callable[[], threads.T]) -> threads.T:
        return threads.patient(call, self.errors, self.sleep, self.block)

    def viewer(self) -> Viewer:
        if self._me is None:
            self._me = self._read_viewer()
        return self._me

    def trusts(self, author: str, association: str) -> bool:
        """所有者か、対象リポジトリで write 以上の権限を持つメンバー・共同作業者か（`GitHub.trusts`）。"""
        return bool(self.hub.trusts(Authored(author, association, "")))

    def pr_url(self, pr: int) -> str:
        return f"https://github.com/{self.hub.repo}/pull/{int(pr)}"

    def issues_url(self) -> str:
        """このリポジトリの Issue の URL の、番号より前。"""
        return f"https://github.com/{self.hub.repo}/issues/"

    def redact(self, *texts: str) -> list[str]:
        return [str(t) for t in self.publisher.guarded(*texts)[0]]

    def _pages(self, path: str, params: Mapping[str, Any]) -> Iterator[list[dict[str, Any]]]:
        """REST の一覧をページごとに返し、短いページで止まる。`github.list-pages` ページを超えたら `TooMany`。"""
        cap = int(limits.value(threads.LIST_PAGES, "count"))
        for page in range(1, cap + 1):
            rows = self.hub.rest("GET", path, params={**params, "per_page": PER_PAGE, "page": page})
            yield [r for r in rows if isinstance(r, dict)]
            if len(rows) < PER_PAGE:
                return
        raise TooMany(cap)

    def comments(self, pr: int) -> Iterator[Comment]:
        """PR のコメントをページごとに読んで返す。呼び手が読むのをやめれば、後のページは取らない。"""
        for rows in self._pages(f"/repos/{self.hub.repo}/issues/{int(pr)}/comments", {}):
            yield from map(_comment, rows)

    def comment(self, comment_id: int) -> Comment:
        return _comment(self.hub.rest("GET", f"/repos/{self.hub.repo}/issues/comments/{int(comment_id)}"))

    def _send(self, method: str, path: str, body: str) -> tuple[int, str]:
        made = self._write(lambda: self.hub.rest(method, f"/repos/{self.hub.repo}/{path}", {"body": body}))
        return int(made["id"]), str(made["updated_at"])

    def post(self, pr: int, body: str) -> tuple[int, str]:
        return self._send("POST", f"issues/{int(pr)}/comments", body)

    def edit(self, comment_id: int, body: str) -> tuple[int, str]:
        return self._send("PATCH", f"issues/comments/{int(comment_id)}", body)

    def search(self, ident: str) -> list[Listed]:
        query = f'repo:{self.hub.repo} is:issue "{ident}" in:body'
        found = self.hub.rest("GET", "/search/issues", params={"q": query, "per_page": PER_PAGE})
        return [_listed_of(r) for r in found["items"] if isinstance(r, dict)]

    def recent(self, since: str) -> list[Listed]:
        """作成時刻が `since` 以後（同じ時刻を含む）の Issue と PR を、新しく作られた順に読む。ページの上限を超えれば、読めた分を
        持つ `TooMany`。"""
        out: list[Listed] = []
        try:
            for issue in self._newest():
                if issue.created_at < since:
                    break
                out.append(issue)
        except TooMany as exc:
            raise TooMany(exc.cap, out) from None
        return out

    def _newest(self) -> Iterator[Listed]:
        params = {"state": "all", "sort": "created", "direction": "desc"}
        for rows in self._pages(f"/repos/{self.hub.repo}/issues", params):
            yield from map(_listed_of, rows)

    def issues(self, urls: Sequence[str]) -> dict[str, Optional[Listed]]:
        """URL ごとに、このリポジトリの Issue ならその中身。別の場所の URL と、無い（消された・Pull Request の）番号は None。
        GraphQL でまとめて読む（`GitHub.nodes`）。"""
        prefix = self.issues_url().lower()
        numbers = {u: int(u[len(prefix) :]) for u in urls if u.lower().startswith(prefix) and u[len(prefix) :].isdecimal()}
        read = self.hub.nodes(numbers.values(), "issue", ISSUE_FIELDS) if numbers else {}
        return {u: _listed_node(read[numbers[u]]) if u in numbers and read[numbers[u]] else None for u in urls}

    def create(self, title: str, body: str, mark: str) -> str:
        return str(self._write(lambda: self.publisher.create_issue(title, body, mark=mark)).url)
