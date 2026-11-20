"""指摘を PR のインラインのスレッドにし、直した・見送った指摘へ返信して resolve する（#3939）。

状態ファイルの指摘のジャーナルとラウンドの修正の記録だけから決める。済んだこと（スレッドの最初のコメント・返信・resolve）は
状態ファイルの `threads` に指摘の番号ごとに残し、打ち直しても同じものを二重に書かない。

- 場所: 指摘の `section`（`<パス>#<シンボル>`）のパスに、本文の先頭の `<名前>:<行>` が同じファイルを指せばその行に付ける。
  行に付けられなければファイルに付ける（`subject_type: file`。`GitHubPort`）。場所が分からなければスレッドを作らず（中身は記録のコメントの
  「指摘」の表に載る）、ファイルへも付けられなければ注意に残す
- 直した指摘: 直したラウンドの修正のコミットを返信し、resolve する
- 見送った指摘: 理由を返信し、resolve する
- 未解決の指摘: スレッドを開いたまま残す
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Optional, Protocol

from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import report_decide, store

# 状態ファイルのキー（指摘の番号 → {"comment": 最初のコメントの id, "replied": bool, "resolved": bool}）
THREADS = "threads"
# 本文の先頭の `<名前>:<行>`
_LOCATION = re.compile(r"^\s*`?([\w./-]+):(\d+)")


class Port(Protocol):
    """PR のレビューのコメントを書く口（テストは偽物）。失敗は例外。`open_thread` は行に付けられなければファイルに付け、
    PR の差分に無いファイルなら None を返す。"""

    def open_thread(self, path: str, line: Optional[int], commit: str, body: str) -> Optional[int]: ...

    def reply(self, comment_id: int, body: str) -> None: ...

    def resolve(self, comment_id: int) -> None: ...


def location(finding: Mapping[str, Any]) -> tuple[Optional[str], Optional[int]]:
    """指摘が指すファイルのパスと行（分からなければ None）。"""
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


def _fix_commit(rounds: Sequence[Mapping[str, Any]], fid: str) -> Optional[str]:
    for r in rounds:
        fix = r.get("fix")
        if isinstance(fix, Mapping) and isinstance(fix.get("fixed"), list) and fid in fix["fixed"] and isinstance(fix.get("commit"), str):
            return str(fix["commit"])
    return None


def opening(finding: Mapping[str, Any]) -> str:
    """スレッドの最初のコメントの本文。"""
    return f"{report_decide.MARKER}\n**cross-review の指摘 {finding.get('id')}**（{finding.get('by')}・ラウンド {finding.get('round')}）\n\n{finding.get('text')}\n"


def closing(finding: Mapping[str, Any], rounds: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """直した・見送った指摘への返信の本文。未解決なら None。"""
    outcome = finding.get("outcome")
    if outcome == fin.FIXED:
        commit = _fix_commit(rounds, str(finding.get("id")))
        return f"直した（修正のコミット {commit[:9]}）" if commit else "直した"
    if outcome == fin.SKIPPED:
        reason = finding.get("reason")
        return f"見送った: {reason}" if isinstance(reason, str) and reason else "見送った（理由の記録が無い）"
    return None


def sync(data: dict[str, Any], port: Port) -> list[str]:
    """スレッドを作り、閉じた指摘へ返信して resolve する。済んだことを `data[THREADS]` に書き、注意を返す。"""
    rounds = [r for r in store.list_of(data, store.ROUNDS) if isinstance(r, Mapping)]
    done = data.setdefault(THREADS, {})
    warnings: list[str] = []
    for finding in store.list_of(data, store.FINDINGS):
        if not isinstance(finding, Mapping) or not isinstance(finding.get("id"), str):
            continue
        fid = finding["id"]
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


class GitHubPort:
    """GitHub の REST と GraphQL で書く口。書く本文は `publish.Publisher.guarded` を通す。"""

    def __init__(self, hub: Any, publisher: Any, pr: int, errors: type[Exception]):
        """`errors` は GitHub が断ったときの例外の型（`github.GitHubError`）。"""
        self.hub, self.publisher, self.pr, self.errors = hub, publisher, int(pr), errors
        self._files: Optional[frozenset[str]] = None
        self._threads: Optional[dict[int, tuple[str, bool]]] = None

    def _safe(self, body: str) -> str:
        (safe,), _ = self.publisher.guarded(body)
        return str(safe)

    def open_thread(self, path: str, line: Optional[int], commit: str, body: str) -> Optional[int]:
        """行に付ける。行が差分に無いと断られたら（`GitHubError`）ファイルに付け直す。PR の差分に無いファイルなら None。"""
        if path not in self._changed():
            return None
        if line is not None:
            try:
                return self._post(path, {"line": line, "side": "RIGHT"}, commit, body)
            except self.errors:
                return self._post(path, {"subject_type": "file"}, commit, body)
        return self._post(path, {"subject_type": "file"}, commit, body)

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
        made = self.hub.rest("POST", f"/repos/{self.hub.repo}/pulls/{self.pr}/comments", payload)
        self._threads = None
        return int(made["id"])

    def reply(self, comment_id: int, body: str) -> None:
        self.hub.rest("POST", f"/repos/{self.hub.repo}/pulls/{self.pr}/comments/{int(comment_id)}/replies", {"body": self._safe(body)})

    def resolve(self, comment_id: int) -> None:
        thread, resolved = self._thread_of(comment_id)
        if not resolved:
            self.hub.graphql(RESOLVE, {"id": thread})

    def _thread_of(self, comment_id: int) -> tuple[str, bool]:
        if self._threads is None or comment_id not in self._threads:
            self._threads = self._read_threads()
        if comment_id not in self._threads:
            raise LookupError(f"コメント {comment_id} のスレッドが PR #{self.pr} に無い")
        return self._threads[comment_id]

    def _read_threads(self) -> dict[int, tuple[str, bool]]:
        found: dict[int, tuple[str, bool]] = {}
        after: Optional[str] = None
        while True:
            repo = self.hub.repository(THREADS_QUERY, {"pr": self.pr, "after": after})
            page = repo["pullRequest"]["reviewThreads"]
            for node in page["nodes"]:
                first = node["comments"]["nodes"]
                if first:
                    found[int(first[0]["databaseId"])] = (str(node["id"]), bool(node["isResolved"]))
            if not page["pageInfo"]["hasNextPage"]:
                return found
            after = page["pageInfo"]["endCursor"]


# PR の変更ファイルの 1 ページの件数（GitHub の上限。変更ファイルは最大 3,000 件まで読める）
FILES_PAGE = 100
THREADS_QUERY = """query($owner:String!,$name:String!,$pr:Int!,$after:String){repository(owner:$owner,name:$name){
pullRequest(number:$pr){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{id isResolved comments(first:1){nodes{databaseId}}}}}}}"""
RESOLVE = "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}"
