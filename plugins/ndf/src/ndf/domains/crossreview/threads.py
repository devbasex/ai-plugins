"""指摘を PR のインラインのスレッドにし、閉じた指摘のスレッドを resolve する（#3939・#4016）。

状態ファイルの指摘のジャーナルとラウンドの修正の記録だけから決める。済んだこと（スレッドの最初のコメント・返信・resolve）は
状態ファイルの `threads` に指摘の番号ごとに残し、打ち直しても同じものを二重に書かない。

- 場所: 相手役が返した `path`・`line`（#3941）があればそこに付ける。無ければ指摘の `section`（`<パス>#<シンボル>`）のパスに、
  本文の先頭の `<名前>:<行>` が同じファイルを指せばその行に付ける。行の分からない指摘はファイルに付ける（`subject_type: file`）。
  場所が分からない・PR の差分に無いファイルの指摘はスレッドにしない（中身は記録のコメントの「指摘」の表に載る）
- 行の分かる指摘は、付けるコミット（指摘のラウンドの head）ごとに 1 つのレビューでまとめて書く（#4016 の決定 4）。
  レビューが行の位置で断られた組だけを 1 件ずつの書き込みへ落とす（決定 5）
- この実行で閉じた（直した・見送った）指摘は、閉じた理由を最初のコメントへ入れて返信を省く（決定 6）。前の実行の
  スレッドを持つ指摘が閉じたときだけ返信する。閉じた指摘のスレッドは 1 件ずつ resolve する（決定 9）
- 書く前に PR のスレッドを読み、自分が書いた最初のコメントの指摘の番号と発生の値（`occurrence`）が指摘と同じなら、
  書かずに状態ファイルの記録を PR から戻す（保存の前に落ちた打ち直しで二重に書かない。不変条件 I4）
- 未解決の指摘: スレッドを開いたまま残す（次の実行が `prior_decide.carried` で引き継ぐ）
- 収束の妨げにならない重さ（`minor`・`nit`）の指摘はスレッドにしない（記録のコメントの表にだけ載る。#3941）
- 書く操作が GitHub の二次レート制限に当たったら待って打ち直す（`patient`）。書き込みの間隔と、内容を作る書き込みを
  数えるウィンドウの上限はマシン全体の `WriteGate`（`github.write-spacing`・`github.content-window.minute`・
  `github.content-window.hour`）が持つ

PR のコメントを読む口（`Reader`）も同じ実装（`GitHubPort`）が持つ: 相手役へ渡す既存のコメントと、引き継ぐスレッド（#3941）。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, NamedTuple, Optional, Protocol, TypeVar

from ndf.adapters.github_pulls import target_of
from ndf.contracts import limits
from ndf.domains.crossreview import criteria_decide, journal, prior_decide, report_decide, store, throttle_decide
from ndf.domains.crossreview import finish_decide as fin

T = TypeVar("T")

# 状態ファイルのキー（指摘の番号 → {"comment": 最初のコメントの id, "replied": bool, "resolved": bool}）
THREADS = report_decide.THREADS
# 指摘の発生の値（`journal` が書く）
OCCURRENCE = journal.OCCURRENCE
# 本文の先頭の `<名前>:<行>`
_LOCATION = re.compile(r"^\s*`?([\w./-]+):(\d+)")
# 最初のコメントと返信の本文に置く発生の値の行
OCCURRENCE_LINE = "<!-- ndf-occurrence: {value} -->"
_OCCURRENCE = re.compile(r"<!-- ndf-occurrence: (\S+) -->")
# 最初のコメントに閉じた理由を足すときの見出し（`finding_of` が閉じた理由の有無を読む目印）
CLOSED_HEAD = "**この実行で閉じた**"
_FINDING_ID = re.compile(r"\*\*cross-review の指摘 (\S+)\*\*")


class Placed(NamedTuple):
    """レビューに載せる 1 件（`body` は公開の検査の前の本文）。"""

    fid: str
    path: str
    line: int
    body: str


class Mark(NamedTuple):
    """最初のコメントの本文から読んだ指摘の番号・発生の値・閉じた理由を本文に持つか。"""

    fid: str
    occurrence: Optional[str]
    closed: bool


class PositionRefused(Exception):
    """レビューが行の位置で断られた（組の中のどれかの行が差分に無い。1 件ずつなら書ける）。"""


class Port(Protocol):
    """PR のレビューのコメントを書き、スレッドを読む口（テストは偽物）。失敗は例外。`open_thread` は行に付けられなければ
    ファイルに付け、PR の差分に無いファイルなら None を返す。`open_review` は行の位置で断られたら `PositionRefused`。"""

    def changed(self) -> frozenset[str]: ...

    def open_threads(self) -> list[prior_decide.Thread]: ...

    def open_review(self, commit: str, comments: Sequence[Placed]) -> int: ...

    def open_thread(self, path: str, line: Optional[int], commit: str, body: str) -> Optional[int]: ...

    def reply(self, comment_id: int, body: str) -> None: ...

    def resolve(self, comment_id: int) -> None: ...


class ReadPr(NamedTuple):
    """スレッドと同じ応答から実際に読んだ PR。`ident` は `<所有者>/<リポジトリ>#<番号>`、`branch`・`head` は head ブランチと
    その先頭のコミット、`head_repo` は head ブランチのあるリポジトリ（応答に無ければ空）。"""

    ident: str
    branch: str
    head: str
    head_repo: str


class Reader(Protocol):
    """PR のコメントを読む口（テストは偽物）。失敗は例外。`comments` は PR コメント（引き継ぎの確かめに要る記録のコメントを
    含む）、`reviews` はインラインのレビューコメントとレビュー本文（相手役へ渡す参考資料にだけ使う）。`base` は PR の
    マージ先のブランチの名前、`repo` はその PR のベースリポジトリ（`<所有者>/<リポジトリ>`）、`read_pr` は `open_threads` が
    読んだ PR（読めていなければ None。要求を足さない）。"""

    def base(self) -> str: ...

    def repo(self) -> str: ...

    def read_pr(self) -> Optional[ReadPr]: ...

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


def _occurrence(finding: Mapping[str, Any]) -> Optional[str]:
    value = finding.get(OCCURRENCE)
    return value if isinstance(value, str) and value else None


def _occurrence_line(finding: Mapping[str, Any]) -> str:
    value = _occurrence(finding)
    return "" if value is None else OCCURRENCE_LINE.format(value=value) + "\n"


def opening(finding: Mapping[str, Any], closed: Optional[str] = None) -> str:
    """スレッドの最初のコメントの本文。`closed`（`closing` の文）があれば閉じた理由を末尾に足す。"""
    severity = criteria_decide.severity_of(finding.get("severity"))
    head = f"{finding.get('by')}・ラウンド {finding.get('round')}" + (f"・{severity}" if severity else "")
    body = f"{report_decide.MARKER}\n{_occurrence_line(finding)}**cross-review の指摘 {finding.get('id')}**（{head}）\n\n{finding.get('text')}\n"
    return body if closed is None else f"{body}\n{CLOSED_HEAD}\n\n{closed}\n"


def replying(finding: Mapping[str, Any], closed: str) -> str:
    """前の実行のスレッドへの返信の本文（閉じた理由と発生の値の行）。"""
    line = _occurrence_line(finding)
    return f"{closed}\n\n{line}" if line else closed


def closing(finding: Mapping[str, Any], rounds: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """直した・見送った指摘の閉じた理由。未解決なら None。"""
    outcome = finding.get("outcome")
    if outcome == fin.FIXED:
        commit = _fix_commit(rounds, finding)
        return f"直した（修正のコミット {commit[:9]}）" if commit else "直した"
    if outcome == fin.SKIPPED:
        reason = finding.get("reason")
        return f"見送った: {reason}" if isinstance(reason, str) and reason else "見送った（理由の記録が無い）"
    return None


def review_body(count: int) -> str:
    """行の指摘をまとめたレビューの本体の本文（`MARKER` で始め、相手役へ渡す既存のコメントから外す。指摘の本文は持たない）。"""
    return f"{report_decide.MARKER}\n**cross-review のレビュー**（行の指摘 {count} 件をスレッドに書いた）\n"


def finding_of(body: str) -> Optional[Mark]:
    """最初のコメントの本文（`opening` の形）から指摘の番号・発生の値・閉じた理由の有無を読む。cross-review の書式で
    始まらない・番号の無い本文は None。"""
    if not _marked(body):
        return None
    found = _FINDING_ID.search(body)
    if found is None:
        return None
    occurrence = _OCCURRENCE.search(body)
    return Mark(found.group(1), occurrence.group(1) if occurrence else None, f"\n{CLOSED_HEAD}\n" in body)


def _replied(thread: prior_decide.Thread, occurrence: Optional[str]) -> bool:
    """スレッドに、同じ発生の値を持つ自分の返信があるか。"""
    if occurrence is None:
        return False
    line = OCCURRENCE_LINE.format(value=occurrence)
    return any(line in r for r in thread.replies)


def restored(finding: Mapping[str, Any], thread: prior_decide.Thread) -> Optional[dict[str, Any]]:
    """記録の無い指摘のスレッドを PR から戻す。自分が書いた最初のコメントの番号と発生の値がどちらも指摘と同じときだけ
    記録を返し、ほかは None（発生の値の無い指摘は戻さない。番号だけで結び付けない）。"""
    occurrence = _occurrence(finding)
    if occurrence is None or not thread.trusted:
        return None
    mark = finding_of(thread.body)
    if mark is None or mark.fid != finding.get("id") or mark.occurrence != occurrence:
        return None
    return {"comment": thread.comment, "replied": mark.closed or _replied(thread, occurrence), "resolved": thread.resolved}


def refreshed(finding: Mapping[str, Any], mark: Mapping[str, Any], thread: prior_decide.Thread) -> dict[str, Any]:
    """最初のコメントの id を持つ指摘の記録を、同じ id のスレッドの返信と resolve の状態で更新する（`comment` は変えない）。"""
    own = finding_of(thread.body)
    replied = bool(mark.get("replied")) or (own is not None and own.closed) or _replied(thread, _occurrence(finding))
    return {**mark, "replied": replied, "resolved": thread.resolved}


def _wanted(finding: Any, done: Mapping[str, Any], settled: set[str]) -> bool:
    """この実行でスレッドを扱う指摘か。最初のコメントの id を持つ指摘は扱い、無い指摘は収束の妨げになる重さで、
    巻き直しの前に閉じていないものだけ扱う（PR 3944 の r5-3）。"""
    if not isinstance(finding, Mapping) or not isinstance(finding.get("id"), str):
        return False
    if _has_comment(done.get(finding["id"])):
        return True
    return criteria_decide.blocking(criteria_decide.severity_of(finding.get("severity"))) and finding["id"] not in settled


def _has_comment(mark: Any) -> bool:
    return isinstance(mark, Mapping) and "comment" in mark


def _pending(finding: Mapping[str, Any], mark: Any, rounds: Sequence[Mapping[str, Any]]) -> bool:
    """書くことが残っているか（スレッドが無い、または閉じたのに返信か resolve が済んでいない）。"""
    if not _has_comment(mark):
        return True
    return closing(finding, rounds) is not None and not (mark.get("replied") and mark.get("resolved"))


def _failed(fid: str, exc: Exception) -> str:
    return f"指摘 {fid} のスレッドを書けなかった: {type(exc).__name__}: {exc}"


class _Sync:
    """`sync` の 1 回分の状態（指摘・済んだことの記録・注意）。"""

    def __init__(self, data: dict[str, Any], port: Port):
        self.port = port
        self.rounds = [r for r in store.list_of(data, store.ROUNDS) if isinstance(r, Mapping)]
        self.done: dict[str, Any] = data.setdefault(THREADS, {})
        settled = data.get(report_decide.SETTLED_KEY)
        settled = set(settled) if isinstance(settled, list) else set()
        self.findings = [f for f in store.list_of(data, store.FINDINGS) if _wanted(f, self.done, settled)]
        self.warnings: list[str] = []

    def match(self, present: Sequence[prior_decide.Thread]) -> None:
        """書く前に読んだスレッドで、記録の無い指摘を PR から戻し、id を持つ指摘の返信と resolve の状態を更新する。"""
        by_comment = {t.comment: t for t in present}
        for finding in self.findings:
            fid = finding["id"]
            mark = self.done.get(fid)
            if _has_comment(mark):
                thread = by_comment.get(mark["comment"])
                if thread is not None:
                    self.done[fid] = refreshed(finding, mark, thread)
                continue
            found = next((r for r in (restored(finding, t) for t in present) if r is not None), None)
            if found is not None:
                self.done[fid] = found

    def reply(self) -> None:
        """前の実行のスレッドを持ち、この実行で閉じた指摘へ返信する。"""
        for finding in self.findings:
            mark = self.done.get(finding["id"])
            closed = closing(finding, self.rounds)
            if not _has_comment(mark) or closed is None or mark.get("replied"):
                continue
            try:
                self.port.reply(mark["comment"], replying(finding, closed))
                mark["replied"] = True
            except Exception as exc:  # noqa: BLE001 ネットワーク: 指摘ごとに注意へ残し、ほかの指摘を続ける
                self.warnings.append(_failed(finding["id"], exc))

    def plan(self, changed: frozenset[str]) -> tuple[dict[str, list[Placed]], list[Mapping[str, Any]]]:
        """記録の無い指摘を、コミットごとのレビューの組と 1 件ずつ書く指摘に分ける。書かない指摘の空の記録は外す。"""
        groups: dict[str, list[Placed]] = {}
        singles: list[Mapping[str, Any]] = []
        for finding in self.findings:
            if _has_comment(self.done.get(finding["id"])):
                continue
            path, line = location(finding)
            commit = _round_head(self.rounds, finding.get("round"))
            if path is None or commit is None or path not in changed:
                # 場所が分からない・差分に無い指摘はスレッドにしない。中身は記録のコメントの「指摘」の表に載る
                self.done.pop(finding["id"], None)
            elif line is None:
                singles.append(finding)
            else:
                body = opening(finding, closing(finding, self.rounds))
                groups.setdefault(commit, []).append(Placed(finding["id"], path, line, body))
        return groups, singles

    def review(self, commit: str, group: Sequence[Placed]) -> bool:
        """組を 1 つのレビューで書く。書けたら真。行の位置で断られたら `PositionRefused` を呼び手へ出し（1 件ずつ書く）、
        ほかの失敗は組の指摘ごとに注意へ残して偽（スパムの判定・原因の分からない拒否は 1 件ずつに展開しない）。"""
        try:
            self.port.open_review(commit, group)
        except PositionRefused:
            raise
        except Exception as exc:  # noqa: BLE001 ネットワーク: 組の指摘ごとに注意へ残し、ほかの組を続ける
            self.warnings += [_failed(p.fid, exc) for p in group]
            return False
        return True

    def single(self, finding: Mapping[str, Any]) -> bool:
        """1 件をスレッドにする（行 → ファイルの付け直しは口が行う）。作れたら真。"""
        path, line = location(finding)
        commit = _round_head(self.rounds, finding.get("round"))
        closed = closing(finding, self.rounds)
        try:
            made = self.port.open_thread(str(path), line, str(commit), opening(finding, closed))
        except Exception as exc:  # noqa: BLE001 ネットワーク: 指摘ごとに注意へ残し、ほかの指摘を続ける
            self.warnings.append(_failed(finding["id"], exc))
            return False
        if made is None:
            self.done.pop(finding["id"], None)
            return False
        self.done[finding["id"]] = {"comment": made, "replied": closed is not None, "resolved": False}
        return True

    def fill(self, reviewed: Sequence[Mapping[str, Any]]) -> bool:
        """スレッドを作った後に 1 回読み直し、レビューで書いた指摘の最初のコメントの id を埋める。読めなければ偽。"""
        try:
            present = self.port.open_threads()
        except Exception as exc:  # noqa: BLE001 ネットワーク: 記録を埋めずに注意へ残し、resolve を打たない
            self.warnings.append(f"作ったスレッドを読み直せなかった（resolve は次の実行で行う）: {type(exc).__name__}: {exc}")
            return False
        for finding in reviewed:
            found = next((r for r in (restored(finding, t) for t in present) if r is not None), None)
            if found is None:
                self.warnings.append(f"指摘 {finding['id']} のスレッドを PR に見つけられなかった")
            else:
                self.done[finding["id"]] = found
        return True

    def resolve(self) -> None:
        """閉じた指摘のスレッドを 1 件ずつ resolve する。閉じた理由が PR に載った（返信できた・最初のコメントに入れた）
        スレッドだけ閉じる（理由の無いまま閉じると次の実行の引き継ぎからも外れる）。"""
        for finding in self.findings:
            mark = self.done.get(finding["id"])
            if not _has_comment(mark) or closing(finding, self.rounds) is None or mark.get("resolved") or not mark.get("replied"):
                continue
            try:
                self.port.resolve(mark["comment"])
                mark["resolved"] = True
            except Exception as exc:  # noqa: BLE001 ネットワーク: 指摘ごとに注意へ残し、ほかの指摘を続ける
                self.warnings.append(_failed(finding["id"], exc))


def sync(data: dict[str, Any], port: Port) -> list[str]:
    """指摘をスレッドにし、閉じた指摘のスレッドを resolve する（#4016 の処理の流れの 1〜7）。済んだことを `data[THREADS]` に
    書き、注意を返す。呼び手は同じ PR の公開のロックを持ち、どの指摘も発生の値を持つ（`journal.with_occurrences`）。"""
    run = _Sync(data, port)
    if not any(_pending(f, run.done.get(f["id"]), run.rounds) for f in run.findings):
        return []
    try:
        # 1・2. 変更ファイルとスレッドを書く前に 1 回ずつ読む（読めないまま書くと、保存の前に落ちた打ち直しで二重に作る）
        changed = port.changed()
        present = port.open_threads()
    except Exception as exc:  # noqa: BLE001 ネットワーク: 何も書かずに注意へ残す（打ち直せる）
        return [f"PR のスレッドを読めなかったので指摘のスレッドを書かなかった: {type(exc).__name__}: {exc}"]
    run.match(present)
    # 3. 前の実行のスレッドを持ち、この実行で閉じた指摘へ返信する
    run.reply()
    groups, singles = run.plan(changed)
    by_id = {f["id"]: f for f in run.findings}
    reviewed: list[Mapping[str, Any]] = []
    for commit, group in groups.items():
        # 4・5. コミットごとに 1 つのレビュー。行の位置で断られた組だけを 1 件ずつ書く（決定 5）
        members = [by_id[p.fid] for p in group]
        try:
            if run.review(commit, group):
                reviewed += members
        except PositionRefused:
            singles += members
    for finding in singles:
        run.single(finding)
    # 6. レビューで書いたときだけ 1 回読み直し、指摘の id を埋める（resolve に要る id も口の手元に置く）。1 件ずつの作成は
    # 作成の応答が id を返すので読み直さない（resolve に要るときだけ口が取り直す）
    if reviewed and not run.fill(reviewed):
        return run.warnings
    # 7. 閉じた指摘を 1 件ずつ resolve する
    run.resolve()
    return run.warnings


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


class GitHubPort:
    """GitHub の REST と GraphQL で書き、読む口。書く本文は `publish.Publisher.guarded` を通す。書き込みの間隔はマシン全体の
    `WriteGate`（クライアントの throttler）が持ち、この口は間を空けない。"""

    def __init__(
        self,
        hub: Any,
        publisher: Any,
        pr: int,
        errors: type[Exception],
        sleep: Callable[[float], None] = time.sleep,
        block: Optional[Callable[[float], None]] = None,
    ):
        """`errors` は GitHub が断ったときの例外の型（`github.GitHubError`）。`sleep` はテストが差し替える。
        `block`（`WriteGate.block`）へ二次レート制限の打ち直しの待ちを毎回知らせ、ほかのプロセスの書き込みも止める（#4016）。
        信頼できる書き手は、今の実行の認証の login（GraphQL の `viewer`。1 回だけ読む）と同じ書き手だけである。"""
        self.hub, self.publisher, self.pr, self.errors = hub, publisher, int(pr), errors
        self.sleep, self.block = sleep, block
        self._me: Optional[str] = None
        self._pr_head: Optional[str] = None
        self._files: Optional[frozenset[str]] = None
        self._threads: Optional[dict[int, tuple[str, bool]]] = None
        self._base: Optional[str] = None
        # スレッドと同じ応答から読んだ head ブランチ・その先頭・head のリポジトリ
        self._read: tuple[str, str, str] = ("", "", "")
        # `base` が先に読んだスレッド（次の `open_threads` が 1 回だけ使う）
        self._ahead: Optional[list[prior_decide.Thread]] = None

    def _write(self, call: Callable[[], T]) -> T:
        return patient(call, self.errors, self.sleep, self.block)

    def _safe(self, body: str) -> str:
        (safe,), _ = self.publisher.guarded(body)
        return str(safe)

    def open_review(self, commit: str, comments: Sequence[Placed]) -> int:
        """`commit` に 1 つのレビュー（`event: COMMENT`）で行に付けるコメントをまとめて書き、レビューの id を返す。本文は 1 回の
        `guarded` に通す。行の位置で断られたら `PositionRefused`（ほかの失敗はそのまま出す）。差分の判定は呼び手が済ませる。
        `event: COMMENT` のレビューは本体の `body` が要る（GitHub の必須。行のコメントの本文とは別）。"""
        bodies, _ = self.publisher.guarded(*(c.body for c in comments))
        placed = [{"path": c.path, "line": c.line, "side": "RIGHT", "body": str(b)} for c, b in zip(comments, bodies)]
        payload = {"commit_id": commit, "event": "COMMENT", "body": review_body(len(comments)), "comments": placed}
        try:
            made = self._write(lambda: self.hub.rest("POST", f"/repos/{self.hub.repo}/pulls/{self.pr}/reviews", payload))
        except self.errors as exc:
            failed: Any = exc
            if position_refused(failed.status, failed.reasons):
                raise PositionRefused(str(exc)) from exc
            raise
        return int(made["id"])

    def open_thread(self, path: str, line: Optional[int], commit: str, body: str) -> Optional[int]:
        """行に付ける。行の位置で断られたら（`position_refused`）ファイルに付け直す。ほかの失敗（スパムの判定・二次レート
        制限の打ち直しの上限を含む）はそのまま出す（付け直しで要求の回数を増やさない）。PR の差分に無いファイルなら None。"""
        if path not in self.changed():
            return None
        if line is not None:
            try:
                return self._place(path, {"line": line, "side": "RIGHT"}, commit, body)
            except self.errors as exc:
                failed: Any = exc
                if not position_refused(failed.status, failed.reasons):
                    raise
        return self._place(path, {"subject_type": "file"}, commit, body)

    def _place(self, path: str, where: dict[str, Any], commit: str, body: str) -> int:
        """`commit` に付ける。そのコミットでパスを解決できないと断られたら（`path_unresolved`。指摘のラウンドの後に
        ファイルが動いた・消えた）、PR の今の head のコミットで付け直す。"""
        try:
            return self._post(path, where, commit, body)
        except self.errors as exc:
            failed: Any = exc
            if not path_unresolved(failed.status, failed.reasons) or self._head() == commit:
                raise
        return self._post(path, where, self._head(), body)

    def _head(self) -> str:
        """PR の今の head のコミット（1 回だけ読む）。"""
        if self._pr_head is None:
            self._pr_head = str(self.hub.rest("GET", f"/repos/{self.hub.repo}/pulls/{self.pr}")["head"]["sha"])
        return self._pr_head

    def base(self) -> str:
        """PR のマージ先のブランチの名前。スレッドの一覧と同じ応答から読み、次の `open_threads` は取り直さない。"""
        if self._base is None:
            self._ahead = self.open_threads()
        if not self._base:
            raise self.errors(f"PR #{self.pr} のマージ先のブランチを読めない（応答に無い）")
        return self._base

    def repo(self) -> str:
        """PR のベースリポジトリ（`<所有者>/<リポジトリ>`。読む先のリポジトリで、要求を足さない）。"""
        return str(self.hub.repo)

    def read_pr(self) -> Optional[ReadPr]:
        """スレッドの一覧と同じ応答から読んだ PR（読んでいないか、応答に head が無ければ None。要求を足さない）。"""
        branch, head, head_repo = self._read
        return ReadPr(f"{self.hub.repo}#{self.pr}", branch, head, head_repo) if branch and head else None

    def changed(self) -> frozenset[str]:
        """PR の変更ファイル（1 回だけ読み、`open_thread` と共有する）。"""
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
        made = self._write(lambda: self.hub.rest("POST", f"/repos/{self.hub.repo}/pulls/{self.pr}/comments", payload))
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
                url=_text(r.get("html_url")),
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
        """PR のレビューのスレッド（最初のコメントの id と本文、resolve 済みか、付いた場所、書き手を信頼できるか、自分の返信）。
        書き手の権限は cross-review の書式（`MARKER`）で始まるスレッドだけ確かめる（ほかは使わないので要求を増やさない）。"""
        if self._ahead is not None:
            ahead, self._ahead = self._ahead, None
            return ahead
        out = []
        # 読んだ一覧を resolve でも使う（起動の始めに読んだスレッドを書く側で取り直さない。PR 3944 の r6-8）
        self._threads = {}
        for node, first in self._thread_nodes():
            self._threads[int(first["databaseId"])] = (str(node["id"]), bool(node["isResolved"]))
            body = _text(first.get("body"))
            trusted = _marked(body) and self._trusted(first.get("author"))
            replies = self._own_replies(node, first) if trusted else ()
            out.append(
                prior_decide.Thread(
                    int(first["databaseId"]), body, bool(node["isResolved"]), node.get("path"), node.get("line"), trusted, replies
                )
            )
        return out

    def _own_replies(self, node: Mapping[str, Any], first: Mapping[str, Any]) -> tuple[str, ...]:
        """スレッドの最後の返信のうち、今の認証の login が書いたものの本文（最初のコメントは除く）。"""
        replies = node.get("replies")
        rows = replies.get("nodes") if isinstance(replies, Mapping) else None
        if not isinstance(rows, list):
            return ()
        return tuple(
            _text(r.get("body"))
            for r in rows
            if isinstance(r, Mapping) and r.get("databaseId") != first.get("databaseId") and self._trusted(r.get("author"))
        )

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
            pull = repo["pullRequest"]
            self._base, head = target_of(pull)
            fork = pull.get("headRepository")
            head_repo = _text(fork.get("nameWithOwner")) if isinstance(fork, Mapping) else ""
            self._read = _text(pull.get("headRefName")), head, head_repo
            page = pull["reviewThreads"]
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
pullRequest(number:$pr){baseRefName headRefName headRefOid headRepository{nameWithOwner} reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}
nodes{id isResolved path line comments(first:1){nodes{databaseId body author{login}}}
replies:comments(last:20){nodes{databaseId body author{login}}}}}}}}"""
# 行の位置の誤りの理由（1 件ずつなら書ける。`pull_request_review_thread.path` の `could not be resolved` を含む）
_PATH_UNRESOLVED = "could not be resolved"
POSITION_PHRASES = ("must be part of the diff", _PATH_UNRESOLVED, "diff_hunk", "line must be")
# どれかを含む理由があれば、位置の理由があっても展開・付け直しをしない（スパムの判定・二次レート制限）
REFUSAL_PHRASES = ("too quickly", "rate limit", "spam")


def _mentions(reasons: Sequence[str], phrases: Sequence[str]) -> bool:
    """理由のどれかが語のどれかを含むか（理由ごとに 1 回だけ小文字にする）。"""
    return any(p in low for low in (r.lower() for r in reasons) for p in phrases)


def _refused(reasons: Sequence[str]) -> bool:
    return _mentions(reasons, REFUSAL_PHRASES)


def _unprocessable(status: int, reasons: Sequence[str], phrases: Sequence[str]) -> bool:
    """422 で、理由のどれかが `phrases` の語を含み、どれもスパム・二次レート制限の語を含まないか。"""
    return status == POSITION_REFUSED and _mentions(reasons, phrases) and not _refused(reasons)


def position_refused(status: int, reasons: Sequence[str]) -> bool:
    """書き込みが行の位置の誤りだけで断られたか（422 で、理由のどれかが位置の語を含み、どれもスパム・二次レート制限の語を
    含まない）。`reasons` は `GitHubError.reasons`（表示用に切り詰める前の理由のすべて）で、表示の文は見ない。"""
    return _unprocessable(status, reasons, POSITION_PHRASES)


def path_unresolved(status: int, reasons: Sequence[str]) -> bool:
    """スレッドの作成が、そのコミットでパスを解決できないと断られたか（422。スパム・二次レート制限の理由が並べば偽）。"""
    return _unprocessable(status, reasons, (_PATH_UNRESOLVED,))


# 今の実行の認証の login
VIEWER = "query{viewer{login}}"
RESOLVE = "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}"
