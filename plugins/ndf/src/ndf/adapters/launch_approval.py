"""起動の承認のハッシュと記録（#1959）。

承認の対象は呼び手（`launch_settings`）が読んだ値を引数で受け、このモジュールは `launch_settings` を読まない。承認の記録は利用者の状態の
保存先にだけ置き（リポジトリのファイルは承認の根拠にしない）、追記だけで、所有者だけが読める。同じリポジトリのキーとハッシュの最後の行が
`approve` なら承認済みである。hook からも読むので、モジュールの頭では標準ライブラリと `identity`・`launch_candidates`・`launch_records` だけを
読み込む。候補のディレクトリの読み取り・所有の確かめ・退避と中断の後の片付けは `launch_candidates` が持つ。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf import identity
from ndf.adapters import launch_candidates, launch_records as records
from ndf.adapters.launch_candidates import CANDIDATE_NAME, SOURCE_NAME, Read

APPROVALS = "launch-approvals"
QUESTIONS = "launch-questions"
ANSWERS = "launch-answers"
CANDIDATES = "launch-candidates"
REFLECTED = "launch-reflected"
# 反映の途中で反映先を退避した名前の後ろと、反映の途中の記録・完了の記録の名前の後ろ
REFLECTING = ".reflecting"
PENDING = ".pending.json"
DONE = ".done.json"
# 承認の質問の選択肢（hook `launch-answer` は、質問が示した選択肢と同じラベルだけを記録する）
APPROVE_LABEL = "承認する（承認者: {}）"
DENY_LABEL = "承認しない"
# 反映が済んでいないリクエストの続きの approve に渡す欄（--sha256・--request・--candidate の順）
CONTINUATION = ("sha256", "request_id", "candidate")
# 記録の読み書きと質問のハッシュは hook と共有する（`launch_records`）
canonical_sha = records.canonical_sha
write_row = records.write_row
TORN_END = records.TORN_END


class Action(StrEnum):
    """承認の記録の操作。"""

    APPROVE = "approve"
    REVOKE = "revoke"


def target_digest(permissions: Mapping[str, Any], files: Mapping[str, Any]) -> str:
    """承認の対象（`permissions` の全体と、パスから定義ファイルの中身への対応）のハッシュ。"""
    return canonical_sha({"permissions": permissions, "files": files})


def repo_key(root: Path) -> str:
    """リポジトリのキー: git の共通ディレクトリの実体の絶対パス（リンクされた worktree は同じキー）。git の外は `root` の実体。"""
    from ndf.adapters import git

    try:
        return str(git.Git(root).common_dir())
    except (git.GitError, OSError):
        return os.path.realpath(root)


def state_file(file_id: str) -> Path:
    """状態の保存先の記録のファイル（表 ndf-files の `file_id` の行）。"""
    from ndf.contracts import facts

    return identity.load().state_dir / facts.value("ndf-files", file_id, "path")


def is_approved(repo: str, sha256: str) -> bool:
    """同じリポジトリのキーとハッシュの最後の行が `approve` か。読む側はロックを取らない。"""
    return _Approvals(records.whole_rows(state_file(APPROVALS))).approved(repo, sha256)


def records_lock(path: Path) -> Any:
    """記録のファイルの錠（`<ファイル>.lock`）。待ちの上限は `launch.approvals-lock`。"""
    from ndf.contracts import limits

    return records.file_lock(path, limits.seconds("launch.approvals-lock"))


def _at_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def approval_row(
    repo: str, sha256: str, action: Action, approver: Optional[str] = None, request_id: Optional[str] = None
) -> dict[str, Any]:
    """承認の記録の 1 行。`approver` と `request_id` は承認の行にだけ置く。"""
    action = Action(action)
    row: dict[str, Any] = {"at": _at_now(), "repo": repo, "sha256": sha256, "action": str(action)}
    if action is Action.APPROVE:
        row.update({k: v for k, v in (("approver", approver), ("request_id", request_id)) if v is not None})
    return row


def _append_row(file_id: str, row: Mapping[str, Any]) -> None:
    path = state_file(file_id)
    with records_lock(path):
        write_row(path, row)


def append(repo: str, sha256: str, action: Action, approver: Optional[str] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """承認の記録へ 1 行足して返す。書けなければ `OSError` か錠の待ちの超過。"""
    row = approval_row(repo, sha256, action, approver, request_id)
    _append_row(APPROVALS, row)
    return row


def content_version(path: Path) -> Optional[str]:
    """ファイルの中身の SHA-256（反映元のバージョン）。無ければ None。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無い反映先は「無し」のバージョンとして照らす
        return None


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _store(file_id: str) -> Path:
    """表 ndf-files の行が指す、状態の保存先の下のディレクトリ（パスの最初の部分）。"""
    state = identity.load().state_dir
    return state / state_file(file_id).relative_to(state).parts[0]


def read_launch_record(path: Path) -> dict[str, Any]:
    """記録の JSON のオブジェクト。無い・壊れていれば空（記録が無いと同じ）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reflected_file(target: Path, suffix: str) -> Path:
    """反映先ごとの反映の途中の記録（`PENDING`）か完了の記録（`DONE`）。"""
    return _store(REFLECTED) / f"{_short_hash(str(target))}{suffix}"


def put_question(
    request: Mapping[str, Any], make_question: Callable[[], Mapping[str, Any]], candidate: Optional[Path] = None
) -> Mapping[str, Any]:
    """承認のリクエスト（`request_id`・`repo`・`sha256`・反映先 `target`・会話の id `session`）と、質問の中身（`questions`）のハッシュを残し、質問を返す。

    反映元のバージョンは候補の記録か、今の反映先のバージョン。質問が示した選択肢のラベル（`options`）と、その時点の承認の記録の行数
    （`approvals_seen`。これより後ろの取り消しだけが、このリクエストの後の取り消し）も残す。候補があれば、候補の錠を取ってから、
    退避していた候補を元の名前へ戻し、`make_question`（候補を読んで質問の中身を作る）を呼び、リクエストを足す。候補を読んでから
    リクエストを足すまでの間に、掃除（`_sweep`・`launch_candidates.settle_aside`）が同じ候補を退避したり消したりしないためである。戻せる候補が無ければ
    リクエストを作らずに `precondition` で止める（消えた候補の承認を問わない）。`make_question` が `sha256`（候補から求めた承認の対象の
    ハッシュ）を返せば、リクエストのハッシュにはその値を使う。候補を読んだのと同じ錠の中で求めた値だからである。
    """
    if candidate is None:
        question = make_question()
        _put_request(request, question, None)
        return question
    from ndf.contracts import internal

    with records_lock(candidate.parents[1]):
        launch_candidates.restore_aside(candidate)
        if not candidate.exists():
            raise internal.InternalError("precondition", f"候補 {candidate} が無い。`draft` で候補を作り直す")
        question = make_question()
        _put_request(request, question, candidate)
    return question


def _put_request(request: Mapping[str, Any], question: Mapping[str, Any], candidate: Optional[Path]) -> None:
    target = Path(request["target"])
    row = {
        **request, **({"sha256": question["sha256"]} if "sha256" in question else {}), "at": _at_now(), "action": str(Action.APPROVE), "target": str(target),
        "base": launch_candidates.record_object(launch_candidates.raw_record(candidate.parent / SOURCE_NAME)).get("base") if candidate is not None else content_version(target),
        "candidate": str(candidate) if candidate is not None else None,
        "candidate_version": content_version(candidate) if candidate is not None else None,
        "question_sha": canonical_sha(question["questions"]),
        "options": records.option_labels(question["questions"]),
        "approvals_seen": len(records.whole_rows(state_file(APPROVALS))),
    }  # fmt: skip
    _append_row(QUESTIONS, row)


def _keyable(row: Mapping[str, Any], *fields: str) -> bool:
    """索引のキーに使う欄がどれも文字列か無いか。型の壊れた行（欄が配列など）は索引から外し、ほかの行の判定を続ける。"""
    return all(isinstance(row.get(field), (str, type(None))) for field in fields)


def _last_labels() -> dict[Any, Any]:
    """リクエストの識別子ごとの最後の回答のラベル。回答の記録は 1 回だけ読む。識別子の型が壊れた行は外す。"""
    return {row.get("request_id"): row.get("label") for row in records.whole_rows(state_file(ANSWERS)) if _keyable(row, "request_id")}


def _judged(label: Any, request: Mapping[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """回答のラベルを、リクエストの質問が示した選択肢と照らす（`approve`・`deny`・None と承認者の名前）。示していないラベルは None。"""
    options = request.get("options")
    if not isinstance(label, str) or not isinstance(options, list) or label not in options:
        return None, None
    if label == DENY_LABEL:
        return "deny", None
    head, _, tail = APPROVE_LABEL.partition("{}")
    if label.startswith(head) and label.endswith(tail) and len(label) > len(head) + len(tail):
        return "approve", label[len(head) : len(label) - len(tail)]
    return None, None


def request_answer(request_id: str) -> tuple[Optional[str], Optional[str]]:
    """リクエストへの最後の回答（`approve`・`deny`・None）と承認者の名前。"""
    requests = _Requests(records.whole_rows(state_file(QUESTIONS)))
    index = requests.by_id.get(request_id)
    return (None, None) if index is None else _judged(_last_labels().get(request_id), requests.rows[index])


def consent_by_answer(
    request_id: str, repo: str, sha256: str, target: Path, candidate: Optional[Path], history: Optional[_History] = None
) -> Optional[str]:
    """リクエストへの「承認する」の回答を、今回の操作（リポジトリ・承認の対象のハッシュ・反映先・候補）への人の同意として照らし、承認者を返す。

    リクエストが無い・操作が違う（別の反映先か別の候補への質問）・同じ反映先により新しいリクエストがある・回答が「承認する」でなければ None。
    承認の行を足すかとは別に、設定を書き換えてよいかを決める（承認の対象が空か承認済みでも、書き換えには毎回人の同意が要る）。
    `history` を渡せば、その索引を使い、リクエストと回答の記録を読み直さない（同じ錠の中で `approve_by_answer` と共有する）。
    """
    history = history if history is not None else _History()
    requests = history.requests
    index = requests.by_id.get(request_id)
    if index is None:
        return None
    req = requests.rows[index]
    asked = (req.get("repo"), req.get("action"), req.get("sha256"), req.get("target"), req.get("candidate"))
    if asked != (repo, Action.APPROVE, sha256, str(target), str(candidate) if candidate is not None else None) or requests.newer(index):
        return None
    answer, name = _judged(history.labels.get(request_id), req)
    return name if answer == "approve" else None


def candidate_destination(candidate: Path) -> Optional[str]:
    """候補の記録（`source.json`）が持つ反映先のパス。読めなければ None。"""
    target = _source_record(candidate).get("target")
    return target if isinstance(target, str) else None


def _source_record(candidate: Path) -> dict[str, Any]:
    return launch_candidates.record_object(launch_candidates.raw_record(candidate.parent / SOURCE_NAME))


def approve_by_answer(repo: str, sha256: str, request_id: str, history: Optional[_History] = None) -> Optional[str]:
    """承認の記録の錠の中で、リクエストの回答を設計書の「人の確認」の条件で照らし、承認の行を足して承認者を返す。使えなければ None。

    同じリクエストの承認の行があり承認済みなら、行を足さずに承認者を返す（中断の後の打ち直し）。リクエストの後の取り消しは、
    リクエストを作った時点の承認の記録の行数より後ろの行で見分ける（時刻は秒の単位なので、同じ秒の取り消しと区別できない）。
    `history` を渡せば、その索引（リクエスト・回答・承認の記録）を使い、読み直さない。
    """
    history = history if history is not None else _History()
    requests = history.requests
    index = requests.by_id.get(request_id)
    if index is None:
        return None
    req = requests.rows[index]
    if (req.get("repo"), req.get("action"), req.get("sha256")) != (repo, Action.APPROVE, sha256):
        return None
    approvals = history.approvals
    used = approvals.used_row(req)
    if used is not None and approvals.approved(repo, sha256):
        return used.get("approver")
    answer, name = _judged(history.labels.get(request_id), req)
    if answer != "approve" or _lapsed(requests, index, approvals, content_version(Path(req["target"]))):
        return None
    write_row(state_file(APPROVALS), approval_row(repo, sha256, Action.APPROVE, name, request_id))
    return name


class _Requests:
    """リクエストの履歴を 1 回だけ走査して作る索引。`by_id` はリクエストの識別子ごと、`latest` は候補ごと、`newest` は反映先ごとの最新の位置。

    キーに使う欄（識別子・反映先・候補と、承認の記録を引くリポジトリのキー・ハッシュ）の型が壊れた行は `rows` に入れない
    （位置は `rows` の中の位置）。型の検査と索引の更新は同じ 1 回の走査で行う。
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows: list[dict[str, Any]] = []
        self.by_id: dict[Any, int] = {}
        self.latest: dict[Any, int] = {}
        self.newest: dict[Any, int] = {}
        for row in rows:
            if not _keyable(row, "request_id", "target", "candidate", "repo", "sha256"):
                continue
            i = len(self.rows)
            self.rows.append(row)
            self.by_id[row.get("request_id")] = i
            self.newest[row.get("target")] = i
            if row.get("candidate") is not None:
                self.latest[row.get("candidate")] = i

    def newer(self, index: int) -> bool:
        """`rows[index]` と同じ反映先に、より新しいリクエストがあるか。"""
        return self.newest[self.rows[index].get("target")] > index  # 索引に入れた行は、どれも自分の反映先の位置を `newest` に持つ


class _Approvals:
    """承認の記録を 1 回だけ走査して作る索引。内容（リポジトリのキーとハッシュ）ごとの最後の操作と最後の取り消しの位置、
    リクエストごとの最後の承認の行を持つ。キーに使う欄（リクエストの識別子・リポジトリのキー・ハッシュ）の型が壊れた行は飛ばす。位置は記録の
    全体の行で数える（リクエストの `approvals_seen` は記録の全体の行数なので）。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.last_action: dict[tuple[Any, Any], Any] = {}
        self.last_revoke: dict[tuple[Any, Any], int] = {}
        self.used: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        for i, row in enumerate(rows):
            if not _keyable(row, "request_id", "repo", "sha256"):
                continue
            key = (row.get("repo"), row.get("sha256"))
            self.last_action[key] = row.get("action")
            if row.get("action") == Action.REVOKE:
                self.last_revoke[key] = i
            self.used[(row.get("request_id"), *key)] = row

    def approved(self, repo: Any, sha256: Any) -> bool:
        """同じリポジトリのキーとハッシュの最後の行が `approve` か。"""
        return self.last_action.get((repo, sha256)) == Action.APPROVE

    def used_row(self, req: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """リクエスト `req` の回答で書いた最後の承認の行（同じリクエストの識別子・リポジトリ・ハッシュ）。無ければ None。"""
        return self.used.get((req.get("request_id"), req.get("repo"), req.get("sha256")))

    def revoked_after(self, req: Mapping[str, Any]) -> bool:
        """リクエストを作った時点の承認の記録の行数（`approvals_seen`）より後ろに、同じ内容の `revoke` があるか。行数が無ければ全体を見る。"""
        seen = req.get("approvals_seen")
        start = seen if isinstance(seen, int) and not isinstance(seen, bool) and seen >= 0 else 0
        key = (req.get("repo"), req.get("sha256"))
        return key in self.last_revoke and self.last_revoke[key] >= start


def _lapsed(requests: _Requests, index: int, approvals: _Approvals, now: Optional[str]) -> bool:
    """リクエスト `requests.rows[index]` の回答が、設計書の「人の確認」の表のとおり使えなくなったか。`now` は反映先の今のバージョン。

    同じ反映先により新しいリクエストがある・反映先がリクエストの反映元のバージョンとも候補とも違う・リクエストの後に同じ内容の `revoke` がある、のどれか。
    リクエストの後の取り消しは、リクエストを作った時点の承認の記録の行数より後ろの行で見分ける（時刻は秒の単位なので、同じ秒の取り消しと
    区別できない）。`approve_by_answer` と掃除（`_live`）が同じ条件を使う。
    """
    req = requests.rows[index]
    moved = now not in (req.get("base"), req.get("candidate_version"))
    return requests.newer(index) or moved or approvals.revoked_after(req)


class _History:
    """掃除が 1 回ずつ読んだ、リクエスト・回答の記録の索引。承認の記録は、照らす候補が出たときに初めて読む（`approvals`）。"""

    def __init__(self) -> None:
        self.requests = _Requests(records.whole_rows(state_file(QUESTIONS)))
        self.labels = _last_labels()
        self._approvals: Optional[_Approvals] = None

    @property
    def approvals(self) -> _Approvals:
        if self._approvals is None:
            self._approvals = _Approvals(records.whole_rows(state_file(APPROVALS)))
        return self._approvals

    def approved(self, repo: str, sha256: str) -> bool:
        """`is_approved` と同じ判定を、この索引の承認の記録で行う。"""
        return self.approvals.approved(repo, sha256)


def answer_history() -> _History:
    """リクエスト・回答の記録を 1 回ずつ読んだ索引（承認の記録は初めて使うときに読む）。承認の記録の錠の中で
    `consent_by_answer` と `approve_by_answer` に同じものを渡し、履歴の走査を 1 回にする。"""
    return _History()


def _live(history: _History, candidate: Path, now: Optional[str], current: Callable[[], Optional[str]]) -> bool:
    """候補の最新のリクエストが、まだ人の回答でこの候補の反映まで済ませられるか。`now` は反映先の今のバージョン、`current` は候補の今のバージョンを返す。

    真になるのは、そのリクエストの反映の完了の記録が無く、次のどちらかのときである。リクエストの回答で書いた承認の行があり、その内容が
    承認済みで、反映先がリクエストの反映元のバージョンか候補の今の中身と同じ（`reflect` がこの候補を置けるか、置いたものとして済ませられる）。
    または、回答が「承認しない」でなく（回答待ちを含む）、`_lapsed` に当たらない。この候補は、人が回答した後の `approve --candidate`
    が承認を済ませるエントリポイントなので、掃除は退避も一覧への掲載もしない。
    """
    index = history.requests.latest.get(str(candidate))
    if index is None:
        return False
    req = history.requests.rows[index]
    if read_launch_record(reflected_file(Path(str(req.get("target"))), DONE)).get("request_id") == req.get("request_id"):
        return False
    approvals = history.approvals
    if approvals.used_row(req) is not None and approvals.approved(req.get("repo"), req.get("sha256")):
        return now == req.get("base") or now == current()
    if _judged(history.labels.get(req.get("request_id")), req)[0] == "deny":
        return False
    return not _lapsed(history.requests, index, approvals, now)


def pending_request(repo: str, target: Path) -> Optional[Mapping[str, Any]]:
    """その反映先の最新のリクエストで、「承認する」と回答があり、反映（候補が無ければ承認の行）が済んでいないもの。"""
    rows = [
        r for r in _Requests(records.whole_rows(state_file(QUESTIONS))).rows if r.get("repo") == repo and r.get("target") == str(target)
    ]
    if not rows or _judged(_last_labels().get(rows[-1].get("request_id")), rows[-1])[0] != "approve":
        return None
    req = rows[-1]
    done = read_launch_record(reflected_file(target, DONE))
    if done.get("request_id") == req["request_id"]:
        return None
    if req.get("candidate") is None and any(r.get("request_id") == req["request_id"] for r in records.whole_rows(state_file(APPROVALS))):
        return None
    return req


def _worktree_gone(target: object) -> bool:
    """反映先の worktree（`<worktree>/.ndf/launch.json` の 2 つ上）が無いか。"""
    parents = Path(target).parents if isinstance(target, str) else ()
    return len(parents) < 2 or not parents[1].exists()


def _candidates(home: Path) -> list[Path]:
    """そろった候補のディレクトリ。作業中の名前（頭が `.`）のものは含めない。"""
    return sorted(made for made in home.glob("*") if not made.name.startswith("."))


@dataclass(frozen=True)
class _Due:
    """片付けの条件に当たる候補の判定。`stamp` は判定の時の候補のディレクトリの状態、`written` は作った時の候補のバージョン、
    `kept` はそのほかに比べるバージョン、`current` は判定で読んだ候補のバージョン（`read` が偽なら判定では読んでいない）。"""

    reason: str
    stamp: frozenset[tuple[str, int, int, int]]
    written: Any
    kept: Optional[str] = None
    read: bool = False
    current: Optional[str] = None


def _sweep_reasons(home: Path) -> dict[Path, _Due]:
    """片付けの条件に当たる候補と、その判定。

    上から順に当てはめ、最初に当たった理由だけを使う。最新のリクエストに「承認しない」と回答した（`denied`）。反映先の worktree が
    無い（`worktree_gone`）。反映先が反映元のバージョンから変わり、候補の中身が反映先の今の中身と一致する（`reflected`。比べる
    バージョンに反映先の今の中身を足す。反映の完了の記録は照らさない）。反映先が反映元のバージョンから変わった（`outdated`。この
    候補では `reflect` が拒む）。後の 2 つは、否認の後に作り直したリクエストの候補にも当てはめる。ただし、まだ人の回答で
    承認を済ませられるリクエスト（`_live`。回答待ちを含む）の候補は、どちらの理由にもしない。「承認しない」の判定は
    候補ごとの最新のリクエストで行う。直したかどうかはここでは見ない（片付ける側の `launch_candidates.set_aside` か、読んだ `current` で見る）。

    リクエストの否認、worktree の有無、反映先のバージョンから、片付けの対象になり得る候補を絞り、当たらない候補のディレクトリは
    調べない。候補の中身が要る判定（承認済みのリクエストがまだ使えるか、`reflected` か `outdated` か）では、先に NDF が作ったもの
    だけかを確かめ（`launch_candidates.ownership`）、候補を読んでから決める。そうでない候補と、過去の反映先か候補が普通のファイルでなく読めない候補は外す。各候補の
    `source.json` は 1 回だけ読む。リクエストの履歴・回答の履歴・承認の記録・候補の一覧は 1 回ずつ読み、反映先のバージョンは反映先ごとに
    1 回だけ求める。
    """
    history = _History()
    versions: dict[str, tuple[bool, Optional[str]]] = {}
    reasons: dict[Path, _Due] = {}
    for made in _candidates(home):
        raw = launch_candidates.raw_record(made / SOURCE_NAME)
        source = launch_candidates.record_object(raw)
        index = history.requests.latest.get(str(made / CANDIDATE_NAME))
        req = None if index is None else history.requests.rows[index]
        if req is not None and _judged(history.labels.get(req.get("request_id")), req)[0] == "deny":
            due = _inspect(made, raw, source, "denied")
        elif _worktree_gone(source.get("target")):
            due = _inspect(made, raw, source, "worktree_gone")
        else:
            due = _moved_target(made, raw, source, versions, history)
        if due is not None:
            reasons[made] = due
    return reasons


def _inspect(made: Path, raw: Optional[Read], source: Mapping[str, Any], reason: str) -> Optional[_Due]:
    """条件に当たった候補が NDF の作ったものだけなら、その判定（候補はまだ読まない）。そうでなければ None。"""
    owned = launch_candidates.ownership(made, {SOURCE_NAME: raw})
    return None if owned is None else _Due(reason, owned.stamp, source.get("written"))


def _moved_target(
    made: Path,
    raw: Optional[Read],
    source: Mapping[str, Any],
    versions: dict[str, tuple[bool, Optional[str]]],
    history: _History,
) -> Optional[_Due]:
    """反映先が反映元のバージョンから変わった候補の判定（`reflected` か `outdated`）。変わっていないか、反映先か候補を読めなければ None。

    反映先のバージョンは `versions` に残し、同じ反映先の候補では読み直さない。まだ人の回答でこの候補の反映まで済ませられるリクエスト
    （`_live`。回答待ちと、回答で書いた承認の行があり反映が済んでいないリクエストを含む）の候補は、どちらの理由にもしない（人が回答した後の
    `approve --candidate` がこの候補で承認を済ませる）。`_live` は履歴と反映先だけで決まれば候補を読まず、承認済みのリクエストで
    候補の今のバージョンが結果を左右するときだけ `look` を呼ぶ。`look` は NDF の作ったものだけと確かめてから候補を読み、結果を
    残すので、続く判定では読み直さない。
    """
    target = str(source.get("target"))
    if target not in versions:
        versions[target] = launch_candidates.regular_version(Path(target), strict=False)
    readable, now = versions[target]
    if not readable or now == source.get("base"):
        return None
    looked: list[tuple[Optional[_Due], bool, Optional[str]]] = []

    def look() -> tuple[Optional[_Due], bool, Optional[str]]:
        if not looked:
            owned = _inspect(made, raw, source, "outdated")
            looked.append((owned, *((False, None) if owned is None else launch_candidates.stamped_version(made, owned.stamp))))
        return looked[0]

    if _live(history, made / CANDIDATE_NAME, now, lambda: look()[2]):
        return None
    due, ok, current = look()
    if due is None or not ok:
        return None
    if now is not None and current == now:
        return _Due("reflected", due.stamp, due.written, now, True, current)
    return _Due("outdated", due.stamp, due.written, None, True, current)


def _listed(reasons: Mapping[Path, _Due], left_out: set[Path]) -> list[dict[str, str]]:
    """元のパスに残った片付けの条件に当たる候補のうち、利用者が消しても設定案と利用者の項目を失わないもの（`cleanup_targets` の形）。

    `left_out`（退避した候補と、開いたままのファイルなどで同じ掃除が元の名前へ戻した候補）と、直した候補は入れない。返す前に、
    候補のディレクトリの項目名と、各項目の inode・大きさ・更新時刻を判定の時と照らし、どれかが変わった候補（判定の後に保存された
    候補、隣に別案が置かれた候補、同じ大きさと更新時刻の別のファイルへ置き換わった候補）も入れない。判定で読んだ候補のバージョンがあれば、読み直さずに使う。読めない候補は入れない。
    """
    listed = []
    for made, due in reasons.items():
        if made in left_out:
            continue
        ok, now = (True, due.current) if due.read else launch_candidates.stamped_version(made, due.stamp)
        if ok and launch_candidates.unedited(now, due.written, due.kept) and launch_candidates.same_stamp(made, due.stamp):
            listed.append({"candidate": str(made / CANDIDATE_NAME), "reason": due.reason})
    return listed


def _sweep(home: Path, restored: set[str]) -> list[dict[str, str]]:
    """候補の錠の中で、`_sweep_reasons` の候補のうち、作った時の中身のままのものを退避し、元のパスに残った候補の一覧を返す。

    「承認しない」は反映を断る回答で、利用者が書いた設定案を消してよいという回答ではないので、リクエストの前に直した候補も残す。
    リクエストは同じ錠の中で作るので（`put_question`）、ここで読んだリクエストの一覧は退避を決めるまで変わらない。
    同じ錠の中の `launch_candidates.settle_aside` が元の名前へ戻した候補（`restored`）は、この掃除では退避せず、一覧にも載せない。開いたままの
    ファイルを見つけて戻した候補を利用者が消すと、その後のエディタの保存が失われるためである。返す一覧は `cleanup_targets` と同じ
    形で、同じ判定の結果から作る（履歴と候補を読み直さない）。退避の直前には、`launch_candidates.set_aside` が候補をもう一度読んで確かめる。
    """
    reasons = _sweep_reasons(home)
    back = {made for made in reasons if made.name in restored}
    gone = {made for made, due in reasons.items() if made not in back and launch_candidates.set_aside(made, due.kept)}
    return _listed(reasons, gone | back)


def cleanup_targets(repo: str) -> list[dict[str, str]]:
    """片付けの条件に当たるが元のパスに残っている候補のうち、利用者が消しても失うものの無い候補と、その理由。

    各要素は `candidate`（候補ファイルのパス）と `reason`（`denied`・`worktree_gone`・`reflected`・`outdated`）を持つ。対象が無ければ
    空のリスト。`show` はこれを結果に載せる（`draft` は `new_candidate` が返す同じ形の一覧を載せる）。`/proc` の無い環境では掃除が
    候補を退避しないので、利用者はこの一覧を見て不要な候補を自分で消す。消す前に、その候補を開いているエディタを閉じる
    （開いたまま消すと、その後の保存で設定案を失う）。何も書かない。
    """
    home = _store(CANDIDATES) / _short_hash(repo)
    return _listed(_sweep_reasons(home), set())


def _snapshot(target: Path) -> tuple[bytes, Optional[str]]:
    """反映先の中身と、同じ読み取りから求めた反映元のバージョン。無ければ空の設定と None。"""
    try:
        data = target.read_bytes()
    except FileNotFoundError:  # 無い反映先は「無し」のバージョンとして空の設定を写す
        return b'{"version": 1}\n', None
    return data, hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Draft:
    """`new_candidate` の結果。`candidate` は作った候補のパス、`cleanup_targets` は掃除の後も元のパスに残った候補の一覧
    （`cleanup_targets` 関数と同じ形。作った候補は入らない）。"""

    candidate: Path
    cleanup_targets: list[dict[str, str]]


def new_candidate(repo: str, target: Path) -> Draft:
    """反映先の写しの候補と、反映先・反映元のバージョン・作った時の候補のバージョンの記録を作り、候補のパスと残った候補の一覧を返す。

    リポジトリごとの候補の錠の中で、中断の残りを片付け、掃除（`_sweep`）をしてから作る。完了の記録も、反映先の worktree が
    無ければ消す。候補は作成途中の名前（`DRAFTING`）のディレクトリで候補と記録をそろえてから付け替え、途中で失敗すれば消す。
    作成途中のディレクトリは、乱数の名前で作り、作る前後に隣の記録へ名前と実体を書く（`launch_candidates.start_draft`）。消すのは
    その記録の実体と合うディレクトリの、NDF が作ったファイルだけである（`launch_candidates.reclaim_drafts`）。
    写しと反映元のバージョンは反映先の同じ読み取りから作る（別に読むと、間の保存で古い写しに新しいバージョンが付く）。
    """
    home = _store(CANDIDATES) / _short_hash(repo)
    with records_lock(home):
        leftovers = _sweep(home, launch_candidates.settle_aside(home))
        for record in _store(REFLECTED).glob("*" + DONE):
            if _worktree_gone(read_launch_record(record).get("target")):
                record.unlink(missing_ok=True)
        name = uuid.uuid4().hex
        made = home / name
        data, base = _snapshot(target)
        drafting = launch_candidates.start_draft(home, name)
        try:
            launch_candidates.replace_private(drafting / CANDIDATE_NAME, data)
            launch_candidates.write_launch_record(
                drafting / SOURCE_NAME, {"target": str(target), "base": base, "written": hashlib.sha256(data).hexdigest()}
            )
            launch_candidates.finish_draft(drafting, made)
        except BaseException:
            launch_candidates.reclaim_drafts(home)
            raise
    return Draft(made / CANDIDATE_NAME, leftovers)


@dataclass(frozen=True)
class Reflection:
    """`reflect` と `finished` の結果。`placed` は置いたか反映済みか。真偽は `placed` と同じ。

    `refusal` は、反映先か候補の中身が呼び手の照らしたものと違って拒んだときの理由。利用者の保存を別の名前で残したパスは、
    結果でなく呼び手が渡す `kept` に足す（拒否・成功・例外のどれで終わっても、呼び手がすべてのパスを結果の文に載せるため）。
    """

    placed: bool
    refusal: Optional[str] = None

    def __bool__(self) -> bool:
        return self.placed


def reflect(
    candidate: Path,
    record: Callable[[], Optional[Mapping[str, Any]]],
    approval: tuple[str, Optional[str]],
    *,
    data: bytes,
    expected: Path,
    kept: list[Path],
) -> Reflection:
    """承認の記録の錠の中で候補の中身 `data` を反映先へ置く。置いたか反映済みなら真、反映先が変わっていたか途中で保存を見つけたら偽の結果を返す。

    `data` は呼び手が承認の対象のハッシュを照らした候補のバイト列で、置くのはこのバイト列である。錠の中で、候補の記録の反映先が
    `expected`（今の作業場所の `.ndf/launch.json`）と違うか、候補の今の中身が `data` と違えば、何も書かずに `refusal` 付きの偽を返す。
    先に `recover` で退避と反映の途中の記録を片付ける。前の反映の一時ファイルの片付けが済まず途中の記録が残れば、
    その記録を上書きも削除もせずに `precondition` で止める（`_recover_into`）。反映先が候補と同じ中身なら、反映元のバージョンとも同じでも置き換えず、次のように扱う。
    `recover` か前の実行が、この操作（候補・反映先・承認の対象のハッシュ・リクエスト）の完了の記録を確定していれば、`record` を呼ばずに
    その記録を残して真を返す（中断の後の打ち直し。後から同じ反映先に新しいリクエストができても、反映済みの操作を拒まない）。
    そうでなければ（利用者が候補と同じ中身を手で保存したなど）`record` を呼ぶ。承認の行が無いと次の起動が止まるためで、
    `record` は承認済みなら何も書かない形にする。反映先が反映元のバージョンと同じときだけ `record` を呼んで置き換える。`record` が返した欄（書き換えに同意した人 `approver` と確認の経路 `route`）は、
    承認の行を書いたかにかかわらず、反映の途中の記録と完了の記録に入れる（だれがどの経路で書き換えを認めたかを残す）。
    書けなければ片付けてから `precondition` で止める（承認の行は残る）。置き換えの途中で止まったら片付け、完了にするのは
    片付けが完了の記録を書いたとき（反映先が NDF の置いた候補のままの、手順 5 の停止）だけである。片付けで利用者の保存を別の名前で
    残したら、そのパスを `kept` に足す（`record` が例外で止めた場合も足した後なので、呼び手が止める文に載せられる）。完了の記録が
    前の実行で残したパスを持っていれば、それも足す。
    `approval` は、承認の対象のハッシュとリクエスト（端末の経路は None）の組で、この操作を見分ける。
    """
    from ndf.contracts import internal

    sha256, request_id = approval
    wanted = hashlib.sha256(data).hexdigest()
    with records_lock(state_file(APPROVALS)):
        source = _source_record(candidate)
        if source.get("target") != str(expected):
            return Reflection(
                False,
                refusal=f"候補の反映先（{source.get('target')}）が、この作業場所の {expected} と違う。この作業場所で draft から作り直す",
            )
        if content_version(candidate) != wanted:
            return Reflection(False, refusal="ハッシュを照らした後に候補が変わった。show で示し直して承認を取り直す")
        target = expected
        _recover_into(target, kept)
        now = content_version(target)
        rec = {
            "candidate": candidate.parent.name,
            "base": source.get("base"),
            "content": wanted,
            "sha256": sha256,
            "request_id": request_id,
        }
        if now == wanted:
            done = _done_for(candidate, target, sha256, request_id, wanted)
            if done is not None:  # 中断の前に置いた同じ操作の完了。同意した人と経路と、別の名前で残した保存は記録のまま引き継ぐ
                _note_saved(done, kept)
                _tidy_after_reflect(target, candidate, done)
                return Reflection(True)
            rec.update(record() or {})
            _after_reflect(target, candidate, rec)
            return Reflection(True)
        if now != source.get("base"):
            return Reflection(False)
        rec.update(record() or {})
        try:
            placed = _place(target, data, rec)
        except OSError as exc:
            _recover_into(target, kept)
            raise internal.InternalError(
                "precondition", f"{target} へ反映できない（{type(exc).__name__}）。同じコマンドを打ち直す"
            ) from None
        if not placed:
            # 片付けが完了の記録を書くのは、反映先が NDF の置いた候補のファイルのままのとき（手順 5 の停止）だけである。
            # 手順 3・4 の停止では候補を置いていないので、反映先が候補と同じ中身でも完了にしない
            _recover_into(target, kept)
            done = _done_for(candidate, target, sha256, request_id, wanted)
            if done is None:
                return Reflection(False)
            _tidy_after_reflect(target, candidate, done)
            return Reflection(True)
        _after_reflect(target, candidate, rec)
        return Reflection(True)


def _recover_into(target: Path, kept: list[Path]) -> None:
    """`recover` を呼び、利用者の保存を別の名前で残したらそのパスを `kept` に足す。一時ファイルの片付けが済んでいなければ止める。

    `recover` は、設定案の写しを移す先を記録できずに一時ファイルを元の名前に残したとき、途中の記録を消さずに残す。
    そのまま次の反映へ進むと、`_place` が同じ記録を新しい一時ファイルの名前で上書きするか、`_after_reflect` が記録を消し、
    残った一時ファイルを次の片付けで特定できなくなる。そこで、途中の記録が残っていれば、`kept` に足した後で `precondition` で止める
    （記録と一時ファイルはそのまま残し、打ち直しの `recover` が片付け直す）。
    """
    from ndf.contracts import internal

    saved = recover(target)
    if saved is not None:
        kept.append(saved)
    pending = reflected_file(target, PENDING)
    if pending.exists():
        left = read_launch_record(pending).get("placing")
        # 残るのは、片付けの進み具合を途中の記録へ書けなかったとき（`_note`）なので、案内するのは記録の保存先である
        raise internal.InternalError(
            "precondition",
            f"前の反映の一時ファイル（{left}）を片付ける途中で、途中の記録 {pending} を更新できなかった。"
            f"一時ファイルと記録は残した。{pending.parent} に書き込めるか確かめてから、同じコマンドを打ち直す",
        )


def _place(target: Path, data: bytes, rec: dict[str, Any]) -> bool:
    """置き換えの手順の 1〜5。途中で保存を見つけたら偽。

    置く一時ファイルを先に作り、その実体（`st_dev`・`st_ino`）を `rec` の `placed` に入れてから反映の途中の記録を書く。中断の後の片付け
    （`recover`）は、反映先がこの実体のままかで、NDF が候補を置いた後の停止か（手順 5 か、置いた直後の中断）を見分ける。中身だけで
    見分けると、手順 4 の間に利用者が候補と同じ中身を手で保存したときも、置いた後の停止として完了にしてしまうためである。

    一時ファイルは乱数の名前（`.launch.json.placing-<値>`）で、作る前にその名前を途中の記録の `placing` に書き、名前が空いている
    ときだけ成功する作り方（`O_EXCL`）で作る。名前が重なれば、そのファイルには触れずに別の値で取り直す。中身は実体を途中の記録に
    書いた後に書く。プロセスが強制終了しても、次の `recover` が途中の記録から一時ファイルを特定し、NDF が作ったと確かめられる
    ものだけを消す（`_drop_placing`）。例外で止まったときの片付けも、呼び手の `recover` に任せる。ただし、実体を途中の記録へ書く処理が
    例外になったときは、記録から実体を確かめられないので、この実行が手元に持つ実体でその場で消す。通常の終わりでも名前を直接は消さず、
    その名前が作った実体のままのときだけ `_drop_placing` で消す（反映の間に利用者が同じ名前へ保存したファイルを消さないため）。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = reflected_file(target, PENDING)
    while True:
        tmp = target.with_name(f"{_PLACING_HEAD.format(target.name)}{uuid.uuid4().hex}")
        rec.pop("placed", None)
        rec["placing"] = str(tmp)
        launch_candidates.write_launch_record(pending, rec)  # 作る前に名前を残す（強制終了の後に特定するため）
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:  # noqa: NDF401 ファイル: 利用者の同じ名前のファイルには触れず、別の値で取り直す
            continue
        break
    with os.fdopen(fd, "wb") as out:
        made = os.fstat(out.fileno())
        rec["placed"] = [made.st_dev, made.st_ino]
        try:
            launch_candidates.write_launch_record(pending, rec)
        except BaseException:
            # 実体を記録に書けなければ、次の `recover` はこのファイルを NDF が作ったと確かめられない。
            # この実行は作った実体を手元に持っているので、その実体のままのときだけここで消してから止める。
            # 中身をまだ書いていない空のファイルなので、移す先を記録できなくても移して消す
            _drop_placing(target, rec, filled=False)
            raise
        out.write(data)
    placed = _replace(target, tmp, rec["base"])
    # 名前を直接消さない。反映の間に利用者が同じ名前へ保存していたら、作った実体と違うので残す
    if not _drop_placing(target, rec):
        # 設定案の写しを移す先を記録できなかった。写しは元の名前のまま残し、呼び手の `recover` が途中の記録から片付け直す
        raise OSError("置き換えの一時ファイルを移す先を、反映の途中の記録へ書けない")
    return placed


# 置き換えの一時ファイルの名前の頭（後ろに乱数の値が付く）
_PLACING_HEAD = ".{}.placing-"
# 消す前に一時ファイルを移す、NDF 専用のディレクトリの名前の頭（後ろに乱数の値が付く）
_DROPPING_HEAD = ".{}.dropping-"
# 専用のディレクトリの中で、移した一時ファイルに付ける名前
_DROPPING_FILE = "placing"


def _drop_placing(target: Path, rec: dict[str, Any], *, filled: bool = True) -> bool:
    """途中の記録 `rec` が名前を残した一時ファイルのうち、NDF が作ったと確かめられるものを消す。片付け直しが要るときだけ偽を返す。

    確かめる条件は、記録した実体（`placed`）と同じことだけである。実体を記録する前に止まった場合は、その名前のファイルが空でも消さない。
    名前が重なった利用者の空のファイルと見分けられないためで、NDF が作った空のファイルが残っても中身（設定案の写し）は無い。
    名前が記録に無い・反映先と別のディレクトリ・頭が違う・実体が違うファイルも消さない（利用者の作ったものかもしれないため）。
    対象は、記録した一時ファイルの名前と、記録した専用のディレクトリ（`dropping`）の中の 1 つだけで、ほかは走査しない。
    専用のディレクトリは、前の実行が消す前に移した直後に強制終了した分を片付けるために先に見る（`_empty_box`）。
    実体が記録に無ければ、どれも消さない。

    `filled` は、一時ファイルに設定案の写しを書いたかもしれないか。偽（中身を書く前の空のファイル）なら、移す先を記録できなくても
    移して消す。真なら、移す先の名前と実体を途中の記録へ書けたときだけ移し、書けなければ元の名前のまま残して偽を返す
    （移した後に強制終了すると、次の片付けが写しを見つけられないため）。
    """
    name = rec.get("placing")
    placed = rec.get("placed")
    if not isinstance(name, str) or placed is None:
        return True
    tmp = Path(name)
    if not _ours(target, tmp, _PLACING_HEAD):
        return True
    _empty_box(target, rec, placed)  # 前の実行が移した後に止まった分
    return _drop_if_made(target, tmp, rec, placed, filled)


def _ours(target: Path, path: Path, head: str) -> bool:
    """`path` が反映先と同じディレクトリにあり、NDF が付ける頭 `head` の名前か。"""
    return path.parent == target.parent and path.name.startswith(head.format(target.name))


def _note(target: Path, rec: Mapping[str, Any]) -> bool:
    """片付けの進み具合を反映の途中の記録に書く。書けたら真。"""
    try:
        launch_candidates.write_launch_record(reflected_file(target, PENDING), rec)
    except OSError:  # noqa: NDF401 ファイル: 書けたかどうかで、呼び手が写しを移すかを決める
        return False
    return True


def _empty_box(target: Path, rec: Mapping[str, Any], placed: Any) -> None:
    """途中の記録の専用のディレクトリ（`dropping`）が、記録した実体（`box`）のままなら、中の一時ファイルを確かめて消し、空になったディレクトリを消す。

    ディレクトリの作成元は、名前と空であることでなく、作った直後に途中の記録へ書いた実体（`st_dev`・`st_ino`）で確かめる。実体が記録に
    無い・違う・ディレクトリでない（シンボリックリンクを含む）ものは、利用者の作ったものかもしれないので触れない。中身が残るディレクトリ
    （実体が違って戻せなかった利用者のファイルを含む）も残す。
    """
    name, made = rec.get("dropping"), rec.get("box")
    if not isinstance(name, str) or made is None or not _ours(target, Path(name), _DROPPING_HEAD):
        return
    box = Path(name)
    try:
        found = os.lstat(box)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 移した後に止まった分は無い
        return
    if not stat.S_ISDIR(found.st_mode) or made != [found.st_dev, found.st_ino]:
        return
    inside = box / _DROPPING_FILE
    if _made(inside, placed):
        inside.unlink(missing_ok=True)
    try:
        os.rmdir(box)
    except OSError:  # noqa: NDF401 ファイル: 中身が残るディレクトリは消さない
        return


def _made(path: Path, placed: Any) -> bool:
    """`path` が記録した実体 `placed`（`st_dev`・`st_ino`）か。"""
    try:
        found = os.lstat(path)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無ければ NDF の作ったものも無い
        return False
    return placed == [found.st_dev, found.st_ino]


def _drop_if_made(target: Path, path: Path, rec: dict[str, Any], placed: Any, filled: bool) -> bool:
    """`path` が記録した実体なら消す。共有のディレクトリの名前を確かめた結果に頼らないよう、専用のディレクトリへ移してから確かめ直して消す。

    専用のディレクトリは乱数の名前で、名前が空いているときだけ成功する `os.mkdir` で作り（所有者だけが使える権限）、名前が重なれば、
    そのディレクトリには触れずに別の値で取り直す。作った後、名前と実体を 1 回の書き込みで途中の記録の `dropping`・`box` に書き、
    書けてから移す。移した直後に強制終了しても、次の `recover` が記録から作成元を確かめて片付けられる（`_empty_box`）。
    書けなければ、`filled` が真（設定案の写しかもしれない）なら移さずに作ったディレクトリを消し、偽を返す。偽（空のファイル）なら移して消す。
    利用者のエディタなどは専用のディレクトリの中へ書かないので、移した後に確かめたファイルと消すファイルは同じになる。確かめてから
    移すまでの間に `path` の名前が利用者のファイルへ置き換わっていれば、移した先で実体が違うと分かるので消さず、元の名前へ戻す
    （元の名前が埋まっていれば専用のディレクトリの中に残す）。
    """
    if not _made(path, placed):
        return True
    while True:
        box = target.with_name(f"{_DROPPING_HEAD.format(target.name)}{uuid.uuid4().hex}")
        try:
            os.mkdir(box, 0o700)
        except FileExistsError:  # noqa: NDF401 ファイル: 利用者の同じ名前のものには触れず、別の値で取り直す
            continue
        break
    found = os.lstat(box)
    rec["dropping"], rec["box"] = str(box), [found.st_dev, found.st_ino]
    if not _note(target, rec) and filled:  # 移す前に、作成元を確かめる名前と実体を残す
        os.rmdir(box)
        del rec["dropping"], rec["box"]
        return False
    inside = box / _DROPPING_FILE
    try:
        os.rename(path, inside)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 確かめた後に消えていれば片付けるものも無い
        pass
    else:
        if not _made(inside, placed):
            try:
                os.link(inside, path, follow_symlinks=False)  # 名前が空いているときだけ戻し、置かれた利用者のファイルを上書きしない
            except OSError:  # noqa: NDF401 ファイル: 戻せない利用者のファイルは、専用のディレクトリの中に残す
                return True
        inside.unlink()
    os.rmdir(box)
    del rec["dropping"], rec["box"]  # 片付け終えたディレクトリは、完了の記録に残さない
    return True


def _replace(target: Path, tmp: Path, base: Optional[str]) -> bool:
    """置き換えの手順の 2〜5（退避・照らし直し・反映先が無いときだけ `tmp` を置く・もう一度照らす）。途中で保存を見つけたら偽。"""
    aside = target.with_name(target.name + REFLECTING)
    had = target.exists()
    if had:
        os.rename(target, aside)
        if content_version(aside) != base:
            return False
    try:
        os.link(tmp, target)
    except FileExistsError:  # noqa: NDF401 ファイル: 置く間に保存された反映先は残し、呼び手が片付けて止める
        return False
    if had:
        if content_version(aside) != base:
            return False
        aside.unlink()
    return True


def _placed(target: Path, rec: Mapping[str, Any]) -> bool:
    """反映先が、反映の途中の記録 `rec` の操作で NDF が置いた候補のファイル（同じ実体で、中身も候補のまま）か。"""
    try:
        found = os.lstat(target)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 反映先が無ければ、置いた候補は残っていない
        return False
    return rec.get("placed") == [found.st_dev, found.st_ino] and content_version(target) == rec.get("content")


def _note_saved(rec: Mapping[str, Any], kept: list[Path]) -> None:
    """記録 `rec` が持つ、利用者の保存を別の名前で残したパスを `kept` に足す（まだ載っていなければ）。"""
    noted = _noted_saved(rec)
    if noted is not None and noted not in kept:
        kept.append(noted)


def _noted_saved(rec: Mapping[str, Any]) -> Optional[Path]:
    """記録 `rec` の `saved`（利用者の保存を別の名前で残したパス）。無いか、そのファイルがもう無ければ None。"""
    noted = rec.get("saved")
    return Path(noted) if isinstance(noted, str) and os.path.lexists(noted) else None


def _same_operation(rec: Mapping[str, Any], candidate: Path, sha256: str, request_id: Optional[str], content: str) -> bool:
    """反映の途中か完了の記録 `rec` が、この操作（候補・承認の対象のハッシュ・リクエスト・置く中身）のものか。"""
    return (rec.get("candidate"), rec.get("sha256"), rec.get("request_id"), rec.get("content")) == (
        candidate.parent.name,
        sha256,
        request_id,
        content,
    )


def _done_for(candidate: Path, target: Path, sha256: str, request_id: Optional[str], content: str) -> Optional[dict[str, Any]]:
    """反映先 `target` の完了の記録がこの操作のもので、反映先が今もその中身なら、その記録。そうでなければ None。"""
    rec = read_launch_record(reflected_file(target, DONE))
    if rec.get("target") != str(target) or not _same_operation(rec, candidate, sha256, request_id, content):
        return None
    return rec if content_version(target) == content else None


def finished(candidate: Path, target: Path, sha256: str, request_id: Optional[str], data: bytes, kept: list[Path]) -> Reflection:
    """端末で人の確認を求める前に、この操作の反映が中断の前に済んでいるかを確かめる。済んでいれば候補を片付けて真の結果を返す。

    承認の記録の錠の中で、反映先が候補の中身 `data` と同じで、反映の途中の記録がこの操作のものなら `recover` で完了の記録を確定する。
    退避に利用者の保存があっても、反映先が NDF の置いた候補のままなら、保存を別の名前で残して途中の記録の同意から完了にする。
    反映先が別の中身なら何も片付けない（利用者の保存を残した場所は、続く `reflect` の `recover` が結果に載せる）。
    完了の記録がこの操作のものなら真を返す。端末で名前とハッシュを入れ直させず、同じ反映先の新しいリクエストで拒みもしない。
    別の名前で残した保存のパスは、この実行で残したものも、完了の記録が持つ前の実行のものも、`kept` に足して呼び手へ返す（真偽にかかわらず）。
    """
    content = hashlib.sha256(data).hexdigest()
    with records_lock(state_file(APPROVALS)):
        if content_version(target) != content:
            return Reflection(False)
        if _same_operation(read_launch_record(reflected_file(target, PENDING)), candidate, sha256, request_id, content):
            _recover_into(target, kept)
        done = _done_for(candidate, target, sha256, request_id, content)
        if done is None:
            return Reflection(False)
        _note_saved(done, kept)
        _tidy_after_reflect(target, candidate, done)
        return Reflection(True)


def _write_done(target: Path, rec: Mapping[str, Any]) -> None:
    """完了の記録を書く。反映の途中の記録は消さない。

    途中の記録は置き換えの一時ファイルの名前を持つので、一時ファイルの片付けが済んだ後に呼び手が消す。完了を書いた直後に
    中断しても、次の `recover` が一時ファイルを特定できるようにするためである。
    """
    launch_candidates.write_launch_record(reflected_file(target, DONE), {**rec, "target": str(target)})


def _candidate_version(made: Path) -> Optional[str]:
    """候補の今のバージョン（`launch_candidates.regular_version`）。読めなければ None。"""
    return launch_candidates.regular_version(made / CANDIDATE_NAME)[1]


def _after_reflect(target: Path, candidate: Path, rec: Mapping[str, Any]) -> None:
    """完了の記録を書いてから、候補の錠の中で、反映した候補と、反映元のバージョンが合わなくなった同じ反映先の候補を退避する。

    先に前の掃除が退避した候補を片付ける（`launch_candidates.settle_aside`）。そこで元の名前へ戻した候補は、この掃除では退避しない。反映した候補は、反映した中身か作った時の中身のままなら退避する。
    ほかの候補は、作った時の中身のままのものだけを退避する。作った後に直した候補は、反映元が変わっても残す（書いた設定案を
    失わないため）。まだ人の回答で承認を済ませられるリクエストの候補（`_live`）も、`draft` の掃除と同じく退避しない。退避した候補は次の掃除が比べ直してから消す。
    """
    _write_done(target, rec)
    reflected_file(target, PENDING).unlink(missing_ok=True)  # 一時ファイルは `_place` が片付け済み
    _tidy_after_reflect(target, candidate, rec)


def _tidy_after_reflect(target: Path, candidate: Path, rec: Mapping[str, Any]) -> None:
    """`_after_reflect` のうち、完了の記録を書いた後の候補の片付け（完了の記録は書き直さない）。"""
    now = content_version(target)
    home = candidate.parent.parent
    with records_lock(home):
        restored = launch_candidates.settle_aside(home)
        if candidate.parent.name not in restored:
            launch_candidates.set_aside(candidate.parent, rec.get("content"))
        history: Optional[_History] = None
        for made in _candidates(home):
            if made.name in restored or made == candidate.parent:
                continue
            source = launch_candidates.record_object(launch_candidates.raw_record(made / SOURCE_NAME))
            if source.get("target") != str(target) or source.get("base") == now:
                continue
            history = history or _History()  # 照らす候補が出たときに初めて読む
            if not _live(history, made / CANDIDATE_NAME, now, functools.partial(_candidate_version, made)):
                launch_candidates.set_aside(made)


def recover(target: Path) -> Optional[Path]:
    """錠の中で、退避（`launch.json.reflecting`）と反映の途中の記録を設計書の「退避が残っている場合の判定」の表で片付ける。

    利用者の保存を別の名前（`launch.json.saved-<値>`）で残したらそのパスを返す。反映先が NDF の置いた候補のファイルのまま
    （`_placed`。途中の記録の実体と同じで、中身も候補のまま）なら、反映は済んでいるので、途中の記録（同意した人と確認の経路を含む）
    から完了の記録を書く。退避が無い場合（反映先が無い状態から置いた・退避を消した直後の中断）も、退避に利用者の保存があって
    別の名前で残した場合も同じである。途中の記録は人の同意を確かめた後にだけ書くので、記録の承認者をそのまま引き継げる。
    中身が候補と同じでも、NDF の置いたファイルでなければ（手順 4 の間の手の保存）利用者の保存として扱い、完了にしない。
    利用者のエディタは錠を取らないので、照らした後に反映先へ保存が入ることがある。退避を反映先へ戻すときは反映先を上書きせず、
    反映先を消すことも一時の名前へ移すこともしない。別の名前は、退避を消す前に途中の記録の `saved` に書き、完了の記録にも残す。
    退避を消した後に中断しても、次の片付けと打ち直しがそのパスを結果に載せられるようにするためである。
    最後に、途中の記録が名前を残した置き換えの一時ファイルのうち、NDF が作ったと確かめられるものを消す（`_drop_placing`。強制終了した
    実行の設定案の写しを作業場所に残さず、利用者の作ったファイルには触れない）。途中の記録は完了の記録を書いた後も残し、
    一時ファイルの片付けが済んでから消す（片付けの前に再び中断しても、次の片付けが一時ファイルの名前を読めるようにするため）。
    片付けが済まず途中の記録を残したことは、記録が残っていることで呼び手に伝わる。反映へ進む呼び手（`_recover_into`）は、
    それを見て、記録を上書きも削除もせずに止める。
    """
    pending_path = reflected_file(target, PENDING)
    rec = read_launch_record(pending_path)
    aside = target.with_name(target.name + REFLECTING)
    saved = None
    if aside.exists():
        kept = content_version(aside)
        if content_version(target) is None:
            saved = _link_back(aside, target, rec)
        elif _placed(target, rec):
            if kept == rec.get("base"):
                aside.unlink()
            else:
                saved = _keep_as_saved(aside, target, rec)
            _write_done(target, rec)
        elif kept == rec.get("base"):
            aside.unlink()
        else:
            saved = _keep_as_saved(aside, target, rec)
    else:
        saved = _noted_saved(rec)  # 退避を別の名前で残し、退避を消した直後の中断
        if _placed(target, rec):
            _write_done(target, rec)
    # 置き換えの途中で止まったか強制終了した実行の一時ファイル。写しを移す先を記録できず残したときは、
    # 次の片付けが名前を読めるよう途中の記録を消さない
    if _drop_placing(target, rec):
        pending_path.unlink(missing_ok=True)
    return saved


def _keep_as_saved(path: Path, target: Path, rec: Optional[dict[str, Any]] = None) -> Path:
    """利用者の保存 `path` を `launch.json.saved-<値>` へ名前を変えて残し、そのパスを返す。

    同じ名前の保存が既にあれば上書きしないよう、名前が空いているときだけ成功する `os.link` で置き、重なれば別の値で取り直す。
    反映の途中の記録 `rec` を渡せば、`path` を消す前に、使う名前を途中の記録の `saved` に書く。前の実行が名前を書いた後に中断して
    いれば、その名前を使い直す（同じ保存を 2 つの名前で残さない）。
    """
    planned = rec.get("saved") if rec else None
    while True:
        if isinstance(planned, str) and Path(planned).parent == target.parent and Path(planned).name.startswith(f"{target.name}.saved-"):
            saved = Path(planned)
        else:
            saved = target.with_name(f"{target.name}.saved-{uuid.uuid4().hex[:8]}")
        planned = None
        if rec and rec.get("saved") != str(saved):
            rec["saved"] = str(saved)
            launch_candidates.write_launch_record(reflected_file(target, PENDING), rec)
        try:
            os.link(path, saved)
        except FileExistsError:  # noqa: NDF401 ファイル: 前に残した保存と名前が重なれば、別の値で取り直す
            if not _same_file(path, saved):
                continue
        path.unlink()
        return saved


def _same_file(a: Path, b: Path) -> bool:
    """`a` と `b` が同じ実体か（前の実行が `os.link` で残した後に中断した名前か）。"""
    try:
        x, y = os.lstat(a), os.lstat(b)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 片方が無ければ同じ実体ではない
        return False
    return (x.st_dev, x.st_ino) == (y.st_dev, y.st_ino)


def _link_back(path: Path, target: Path, rec: Optional[dict[str, Any]] = None) -> Optional[Path]:
    """`path` を反映先の名前へ戻す。反映先が既にあれば（照らした後の利用者の保存）上書きせず、`path` を別の名前で残してそのパスを返す。"""
    try:
        os.link(path, target)
    except FileExistsError:  # 照らした後に保存された反映先は残し、戻す側を別の名前で残す
        return _keep_as_saved(path, target, rec)
    path.unlink()
    return None


def reflected_candidate(candidate: Path, target: Path) -> bool:
    """反映先 `target` の最後の完了の記録がこの候補のもので、反映先が今もその中身か。候補が残っていれば、候補もその中身か。何も書かない。

    承認の対象のハッシュは `permissions` と MCP の定義だけから求めるので、反映したかはハッシュでなく、候補の中身と反映先を結んだ完了の記録で確かめる。
    候補を削除できない環境では、利用者が反映の後に残った候補を編集できるので、候補の今の中身も記録した中身と照らす。
    """
    rec = read_launch_record(reflected_file(target, DONE))
    if rec.get("candidate") != candidate.parent.name or rec.get("target") != str(target) or not _still_placed(rec):
        return False
    return not candidate.exists() or content_version(candidate) == rec.get("content")


def _still_placed(rec: Optional[Mapping[str, Any]]) -> bool:
    return rec is not None and content_version(Path(str(rec["target"]))) == rec.get("content")


@dataclass(frozen=True)
class Reflected:
    """`reflected` が照らした完了の記録から読んだ結果。`saved` は、その反映で利用者の保存を別の名前で残したパス（無ければ None）。"""

    saved: Optional[Path] = None


def reflected(candidate: Path, target: Path, sha256: str, request_id: Optional[str]) -> Optional[Reflected]:
    """候補が無い打ち直しで、今の作業場所の反映先 `target` の完了の記録と反映先の中身を照らす。済んでいなければ None。何も書かない。

    ほかの作業場所の完了の記録は見ない（別の worktree で打ち直したとき、その作業場所へ反映していないのに済んだとしないため）。
    別の名前で残した保存のパスは、照らしたのと同じ 1 回の読み取りから返す。読み直すと、間に同じ反映先の次の反映が完了したとき、
    この操作の結果に次の操作の保存先を付けてしまうためである。
    """
    rec = read_launch_record(reflected_file(target, DONE))
    if rec.get("candidate") != candidate.parent.name or rec.get("target") != str(target):
        return None
    if (rec.get("sha256"), rec.get("request_id")) != (sha256, request_id) or not _still_placed(rec):
        return None
    return Reflected(_noted_saved(rec))
