"""指摘を PR のインラインのスレッドにし、直した・見送った指摘へ返信して resolve する（#3939）。

状態ファイルの指摘のジャーナルとラウンドの修正の記録だけから決める。済んだこと（スレッドの最初のコメント・返信・resolve）は
状態ファイルの `threads` に指摘の番号ごとに残し、打ち直しても同じものを二重に書かない。

- 場所: 相手役が返した `path`・`line`（#3941）があればそこに付ける。無ければ指摘の `section`（`<パス>#<シンボル>`）のパスに、
  本文の先頭の `<名前>:<行>` が同じファイルを指せばその行に付ける。行に付けられなければファイルに付ける（`subject_type: file`。`GitHubPort`）。場所が分からなければスレッドを作らず（中身は記録のコメントの
  「指摘」の表に載る）、ファイルへも付けられなければ注意に残す
- 直した指摘: 直したラウンドの修正のコミットを返信し、resolve する
- 見送った指摘: 理由を返信し、resolve する
- 未解決の指摘: スレッドを開いたまま残す（次の実行が `prior_decide.carried` で引き継ぐ）
- 収束の妨げにならない重さ（`minor`・`nit`）の指摘はスレッドにしない（記録のコメントの表にだけ載る。#3941）
- 書く操作が GitHub の二次レート制限に当たったら待って打ち直し（`patient`）、スレッドを続けて作るときは間隔を空ける

PR のコメントを読む口（`Reader`）も同じ実装（`GitHubPort`）が持つ: 相手役へ渡す既存のコメントと、引き継ぐスレッド（#3941）。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, NamedTuple, Optional, Protocol, TypeVar

from ndf.contracts import limits
from ndf.domains.crossreview import criteria_decide, prior_decide, report_decide, store, throttle_decide
from ndf.domains.crossreview import finish_decide as fin

T = TypeVar("T")

# 状態ファイルのキー（指摘の番号 → {"comment": 最初のコメントの id, "replied": bool, "resolved": bool}）
THREADS = report_decide.THREADS
# 本文の先頭の `<名前>:<行>`
_LOCATION = re.compile(r"^\s*`?([\w./-]+):(\d+)")


class Port(Protocol):
    """PR のレビューのコメントを書く口（テストは偽物）。失敗は例外。`open_thread` は行に付けられなければファイルに付け、
    PR の差分に無いファイルなら None を返す。"""

    def open_thread(self, path: str, line: Optional[int], commit: str, body: str) -> Optional[int]: ...

    def reply(self, comment_id: int, body: str) -> None: ...

    def resolve(self, comment_id: int) -> None: ...


class Reader(Protocol):
    """PR のコメントを読む口（テストは偽物）。失敗は例外。`comments` は PR コメント（引き継ぎの確かめに要る記録のコメントを
    含む）、`reviews` はインラインのレビューコメントとレビュー本文（相手役へ渡す参考資料にだけ使う）。"""

    def comments(self) -> list[prior_decide.Remark]: ...

    def reviews(self) -> list[prior_decide.Remark]: ...

    def open_threads(self) -> list[prior_decide.Thread]: ...


def location(finding: Mapping[str, Any]) -> tuple[Optional[str], Optional[int]]:
    """指摘が指すファイルのパスと行（分からなければ None）。相手役が返した `path`・`line` を先に使う。"""
    given = finding.get("path")
    if isinstance(given, str) and given.strip():
        line = finding.get("line")
        return given.strip(), line if type(line) is int and line > 0 else None
    section = finding.get("section")
    path = section.split("#", 1)[0] if isinstance(section, str) and section else None
    if not path:
        return None, None
    text = finding.get("text")
    found = _LOCATION.match(text) if isinstance(text, str) else None
    if found is None:
        return path, None
    named = found.group(1)
    same = named == path or path.endswith("/" + named)
    return path, int(found.group(2)) if same else None


def _round_head(rounds: Sequence[Mapping[str, Any]], round_no: Any) -> Optional[str]:
    for r in rounds:
        if r.get("round") == round_no and isinstance(r.get("head"), str):
            return str(r["head"])
    return None


def _fix_commit(rounds: Sequence[Mapping[str, Any]], finding: Mapping[str, Any]) -> Optional[str]:
    """指摘を直したとした最初の修正のコミット。指摘のラウンド（再発したら再発したラウンド）より前の修正は使わない
    （再発する前のコミットを修正として案内しない。PR 3944 の r7-3）。"""
    fid = str(finding.get("id"))
    since = finding.get("round") if type(finding.get("round")) is int else 0
    for r in rounds:
        if type(r.get("round")) is int and r["round"] < since:
            continue
        fix = r.get("fix")
        if isinstance(fix, Mapping) and isinstance(fix.get("fixed"), list) and fid in fix["fixed"] and isinstance(fix.get("commit"), str):
            return str(fix["commit"])
    return None


def opening(finding: Mapping[str, Any]) -> str:
    """スレッドの最初のコメントの本文。"""
    severity = criteria_decide.severity_of(finding.get("severity"))
    head = f"{finding.get('by')}・ラウンド {finding.get('round')}" + (f"・{severity}" if severity else "")
    return f"{report_decide.MARKER}\n**cross-review の指摘 {finding.get('id')}**（{head}）\n\n{finding.get('text')}\n"


def closing(finding: Mapping[str, Any], rounds: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """直した・見送った指摘への返信の本文。未解決なら None。"""
    outcome = finding.get("outcome")
    if outcome == fin.FIXED:
        commit = _fix_commit(rounds, finding)
        return f"直した（修正のコミット {commit[:9]}）" if commit else "直した"
    if outcome == fin.SKIPPED:
        reason = finding.get("reason")
        return f"見送った: {reason}" if isinstance(reason, str) and reason else "見送った（理由の記録が無い）"
    return None


def sync(data: dict[str, Any], port: Port) -> list[str]:
    """スレッドを作り、閉じた指摘へ返信して resolve する。済んだことを `data[THREADS]` に書き、注意を返す。"""
    rounds = [r for r in store.list_of(data, store.ROUNDS) if isinstance(r, Mapping)]
    done = data.setdefault(THREADS, {})
    settled = data.get(report_decide.SETTLED_KEY)
    settled = set(settled) if isinstance(settled, list) else set()
    warnings: list[str] = []
    for finding in store.list_of(data, store.FINDINGS):
        if not isinstance(finding, Mapping) or not isinstance(finding.get("id"), str):
            continue
        fid = finding["id"]
        if fid not in done and not criteria_decide.blocking(criteria_decide.severity_of(finding.get("severity"))):
            # 収束の妨げにならない指摘はスレッドにしない（記録のコメントの表に載る）
            continue
        if fid not in done and fid in settled:
            # 巻き直しの前に直した・見送った指摘は新しい PR にスレッドを作らない（記録のコメントの表に載る。PR 3944 の r5-3）
            continue
        try:
            _sync_one(finding, rounds, done, port)
        except Exception as exc:  # noqa: BLE001 ネットワーク: GitHub の失敗は指摘ごとに注意へ残し、ほかの指摘を続ける
            warnings.append(f"指摘 {fid} のスレッドを書けなかった: {type(exc).__name__}: {exc}")
    return warnings


def _sync_one(finding: Mapping[str, Any], rounds: Sequence[Mapping[str, Any]], done: dict[str, Any], port: Port) -> None:
    """1 件の指摘のスレッドを作り、閉じていれば返信して resolve する。済むたびに `done` へ書く。"""
    fid = str(finding["id"])
    mark = done.setdefault(fid, {})
    if "comment" not in mark:
        created = _open(finding, rounds, port)
        if created is None:
            # 場所が分からない・差分に無い指摘はスレッドにしない。中身は記録のコメントの「指摘」の表に載る
            done.pop(fid)
            return
        mark["comment"] = created
    reply = closing(finding, rounds)
    if reply is None:
        return
    if not mark.get("replied"):
        port.reply(mark["comment"], reply)
        mark["replied"] = True
    if not mark.get("resolved"):
        port.resolve(mark["comment"])
        mark["resolved"] = True


def _open(finding: Mapping[str, Any], rounds: Sequence[Mapping[str, Any]], port: Port) -> Optional[int]:
    """スレッドを作り、最初のコメントの id を返す。場所が分からない・差分に無ければ None。付けられなければ口の例外を
    そのまま出す（呼び出し側が注意に残す）。"""
    path, line = location(finding)
    commit = _round_head(rounds, finding.get("round"))
    if path is None or commit is None:
        return None
    return port.open_thread(path, line, commit, opening(finding))


def patient(
    call: Callable[[], T],
    errors: type[Exception],
    sleep: Callable[[float], None] = time.sleep,
    block: Optional[Callable[[float], None]] = None,
) -> T:
    """`call` を打つ。GitHub の二次レート制限（`errors` の失敗の `secondary` が真）なら待って打ち直す。

    待つ前に、待ちの秒を `block`（`WriteGate.block`）へ知らせ、ほかのプロセスの書き込みも止める（#4016 の決定 7）。
    `errors` の失敗は `github.GitHubError` の属性（`secondary`・`hinted`・`retry_after`）を持つ。
    """
    attempt = 1
    while True:
        try:
            return call()
        except errors as exc:
            failed: Any = exc
            wait = throttle_decide.wait_before_retry(failed.secondary, attempt, failed.retry_after, failed.hinted)
            if wait is None:
                raise
            if block is not None:
                block(wait)
            sleep(wait)
            attempt += 1


class Pace(NamedTuple):
    """待つ関数と時計（書く要求の打ち直しと間隔に使う）。"""

    sleep: Callable[[float], None]
    clock: Callable[[], float]


class GitHubPort:
    """GitHub の REST と GraphQL で書き、読む口。書く本文は `publish.Publisher.guarded` を通す。"""

    def __init__(
        self,
        hub: Any,
        publisher: Any,
        pr: int,
        errors: type[Exception],
        pace: Optional[Pace] = None,
        block: Optional[Callable[[float], None]] = None,
    ):
        """`errors` は GitHub が断ったときの例外の型（`github.GitHubError`）。`pace` はテストが差し替える。
        `block`（`WriteGate.block`）へ二次レート制限の打ち直しの待ちを毎回知らせ、ほかのプロセスの書き込みも止める（#4016）。
        信頼できる書き手は、今の実行の認証の login（GraphQL の `viewer`。1 回だけ読む）と同じ書き手だけである。"""
        self.hub, self.publisher, self.pr, self.errors = hub, publisher, int(pr), errors
        self.block = block
        self.sleep, self.clock = pace if pace is not None else Pace(time.sleep, time.monotonic)
        self._me: Optional[str] = None
        self._pr_head: Optional[str] = None
        self._files: Optional[frozenset[str]] = None
        self._threads: Optional[dict[int, tuple[str, bool]]] = None
        self._last_post: Optional[float] = None

    def _write(self, call: Callable[[], T]) -> T:
        return patient(call, self.errors, self.sleep, self.block)

    def _safe(self, body: str) -> str:
        (safe,), _ = self.publisher.guarded(body)
        return str(safe)

    def open_thread(self, path: str, line: Optional[int], commit: str, body: str) -> Optional[int]:
        """行に付ける。行の位置を断られたら（`POSITION_REFUSED` の状態）ファイルに付け直す。ほかの失敗（二次レート制限の
        打ち直しの上限を含む）はそのまま出す（付け直しで要求の回数を増やさない）。PR の差分に無いファイルなら None。"""
        if path not in self._changed():
            return None
        if line is not None:
            try:
                return self._place(path, {"line": line, "side": "RIGHT"}, commit, body)
            except self.errors as exc:
                if int(getattr(exc, "status", 0)) != POSITION_REFUSED:
                    raise
        return self._place(path, {"subject_type": "file"}, commit, body)

    def _place(self, path: str, where: dict[str, Any], commit: str, body: str) -> int:
        """`commit` に付ける。そのコミットでパスを解決できないと断られたら（`path_unresolved`。指摘のラウンドの後に
        ファイルが動いた・消えた）、PR の今の head のコミットで付け直す。"""
        try:
            return self._post(path, where, commit, body)
        except self.errors as exc:
            if not path_unresolved(int(getattr(exc, "status", 0)), str(exc)) or self._head() == commit:
                raise
        return self._post(path, where, self._head(), body)

    def _head(self) -> str:
        """PR の今の head のコミット（1 回だけ読む）。"""
        if self._pr_head is None:
            self._pr_head = str(self.hub.rest("GET", f"/repos/{self.hub.repo}/pulls/{self.pr}")["head"]["sha"])
        return self._pr_head

    def _changed(self) -> frozenset[str]:
        """PR の変更ファイル（1 回だけ読む）。"""
        if self._files is None:
            names: set[str] = set()
            page = 1
            while True:
                rows = self.hub.rest("GET", f"/repos/{self.hub.repo}/pulls/{self.pr}/files", params={"per_page": FILES_PAGE, "page": page})
                names.update(str(r["filename"]) for r in rows)
                if len(rows) < FILES_PAGE:
                    break
                page += 1
            self._files = frozenset(names)
        return self._files

    def _post(self, path: str, where: dict[str, Any], commit: str, body: str) -> int:
        payload: dict[str, Any] = {"body": self._safe(body), "commit_id": commit, "path": path, **where}
        # スレッドを続けて作るときは間隔を空ける（二次レート制限に当たりにくくする）
        wait = throttle_decide.spacing_wait(None if self._last_post is None else self.clock() - self._last_post)
        if wait > 0:
            self.sleep(wait)
        try:
            made = self._write(lambda: self.hub.rest("POST", f"/repos/{self.hub.repo}/pulls/{self.pr}/comments", payload))
        finally:
            self._last_post = self.clock()
        # 読んだスレッドの一覧は捨てない。新しいスレッドの id は resolve で見つからないときだけ取り直す（PR 3944 の r6-8）
        return int(made["id"])

    def reply(self, comment_id: int, body: str) -> None:
        payload = {"body": self._safe(body)}
        self._write(lambda: self.hub.rest("POST", f"/repos/{self.hub.repo}/pulls/{self.pr}/comments/{int(comment_id)}/replies", payload))

    def resolve(self, comment_id: int) -> None:
        thread, resolved = self._thread_of(comment_id)
        if not resolved:
            self._write(lambda: self.hub.graphql(RESOLVE, {"id": thread}))
            if self._threads is not None:
                self._threads[comment_id] = (thread, True)

    def reviews(self) -> list[prior_decide.Remark]:
        """PR のインラインのレビューコメントとレビュー本文（書いた順）。"""
        base = f"/repos/{self.hub.repo}"
        out = [
            prior_decide.Remark("review_comment", _login(r), _text(r.get("body")), _path(r), _line(r), _text(r.get("created_at")))
            for r in self._pages(f"{base}/pulls/{self.pr}/comments")
        ]
        out += [
            prior_decide.Remark("review", _login(r), _text(r.get("body")), at=_text(r.get("submitted_at")))
            for r in self._pages(f"{base}/pulls/{self.pr}/reviews")
        ]
        return out

    def comments(self) -> list[prior_decide.Remark]:
        """PR コメント（書いた順。最終更新の時刻も持つ）。記録のコメント（`MARKER` で始まる）だけ書き手を確かめる
        （次の実行が中の未解決の指摘を引き継ぐ。ほかは要求を増やさない）。"""
        return [
            prior_decide.Remark(
                "comment",
                _login(r),
                _text(r.get("body")),
                at=_text(r.get("created_at")),
                trusted=_marked(_text(r.get("body"))) and self._trusted(r.get("user")),
                updated=_text(r.get("updated_at")),
            )
            for r in self._pages(f"/repos/{self.hub.repo}/issues/{self.pr}/comments")
        ]

    def _pages(self, path: str) -> Iterator[dict[str, Any]]:
        """REST の一覧を読み切る（`github.list-pages` のページ数を超えたら途中で切らずに止まる）。"""
        cap = int(limits.value(LIST_PAGES, "count"))
        for page in range(1, cap + 1):
            rows = self.hub.rest("GET", path, params={"per_page": FILES_PAGE, "page": page})
            yield from (r for r in rows if isinstance(r, dict))
            if len(rows) < FILES_PAGE:
                return
        raise self.errors(f"{path} の一覧が {cap} ページを超える（上限 {LIST_PAGES}）")

    def open_threads(self) -> list[prior_decide.Thread]:
        """PR のレビューのスレッド（最初のコメントの id と本文、resolve 済みか、付いた場所、書き手を信頼できるか）。
        書き手の権限は cross-review の書式（`MARKER`）で始まるスレッドだけ確かめる（ほかは使わないので要求を増やさない）。"""
        out = []
        # 読んだ一覧を resolve でも使う（起動の始めに読んだスレッドを書く側で取り直さない。PR 3944 の r6-8）
        self._threads = {}
        for node, first in self._thread_nodes():
            self._threads[int(first["databaseId"])] = (str(node["id"]), bool(node["isResolved"]))
            body = _text(first.get("body"))
            trusted = _marked(body) and self._trusted(first.get("author"))
            out.append(
                prior_decide.Thread(int(first["databaseId"]), body, bool(node["isResolved"]), node.get("path"), node.get("line"), trusted)
            )
        return out

    def _trusted(self, author: Any) -> bool:
        """書き手が今の実行の認証の login と同じか（cross-review 自身が書いたもの）。PR にコメントできるほかの人が同じ書式で
        書いた本文を、修正担当へ渡さない（PR 3944 の r1-3）。"""
        login = _text(author.get("login")) if isinstance(author, Mapping) else ""
        if self._me is None:
            self._me = _text(self.hub.graphql(VIEWER, {})["viewer"]["login"])
        return bool(login) and login == self._me

    def _thread_of(self, comment_id: int) -> tuple[str, bool]:
        if self._threads is None or comment_id not in self._threads:
            # 読んでいない・読んだ後に作ったスレッドだけ取り直す
            self._threads = self._read_threads()
        if comment_id not in self._threads:
            raise LookupError(f"コメント {comment_id} のスレッドが PR #{self.pr} に無い")
        return self._threads[comment_id]

    def _read_threads(self) -> dict[int, tuple[str, bool]]:
        return {int(first["databaseId"]): (str(node["id"]), bool(node["isResolved"])) for node, first in self._thread_nodes()}

    def _thread_nodes(self) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        """スレッドと最初のコメントの組（最初のコメントの無いスレッドは飛ばす）。"""
        after: Optional[str] = None
        while True:
            repo = self.hub.repository(THREADS_QUERY, {"pr": self.pr, "after": after})
            page = repo["pullRequest"]["reviewThreads"]
            for node in page["nodes"]:
                first = node["comments"]["nodes"]
                if first:
                    yield node, first[0]
            if not page["pageInfo"]["hasNextPage"]:
                return
            after = page["pageInfo"]["endCursor"]


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _marked(body: str) -> bool:
    """cross-review の書式（`report_decide.MARKER`）で始まる本文か。"""
    return body.lstrip().startswith(report_decide.MARKER)


def _login(row: Mapping[str, Any]) -> str:
    user = row.get("user")
    return _text(user.get("login")) if isinstance(user, Mapping) else ""


def _path(row: Mapping[str, Any]) -> Optional[str]:
    path = _text(row.get("path"))
    return path if path.strip() else None


def _line(row: Mapping[str, Any]) -> Optional[int]:
    line = row.get("line") if row.get("line") is not None else row.get("original_line")
    return line if type(line) is int else None


# 行の位置が差分に無いと断られたときの HTTP の状態（Unprocessable Entity）
POSITION_REFUSED = 422
# PR の変更ファイル・コメントの 1 ページの件数（GitHub の上限。変更ファイルは最大 3,000 件まで読める）
FILES_PAGE = 100
# 一覧を読み切るページの上限の方針
LIST_PAGES = "github.list-pages"
THREADS_QUERY = """query($owner:String!,$name:String!,$pr:Int!,$after:String){repository(owner:$owner,name:$name){
pullRequest(number:$pr){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{id isResolved path line comments(first:1){nodes{databaseId body author{login}}}}}}}}"""
# 指摘のラウンドの head でパスを解決できないと断られた文（`pull_request_review_thread.path` の `could not be resolved`）
_PATH_UNRESOLVED = "could not be resolved"


def path_unresolved(status: int, detail: str) -> bool:
    """スレッドの作成が、そのコミットでパスを解決できないと断られたか（422）。"""
    return status == POSITION_REFUSED and _PATH_UNRESOLVED in detail


# 今の実行の認証の login
VIEWER = "query{viewer{login}}"
RESOLVE = "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}"
