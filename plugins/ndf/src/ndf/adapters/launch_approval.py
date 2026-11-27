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


def approve_by_answer(repo: str, sha256: str, request_id: str) -> Optional[str]:
    """承認の記録の錠の中で、リクエストの回答を設計書の「人の確認」の条件で照らし、承認の行を足して承認者を返す。使えなければ None。

    同じリクエストの承認の行があり承認済みなら、行を足さずに承認者を返す（中断の後の打ち直し）。リクエストの後の取り消しは、
    リクエストを作った時点の承認の記録の行数より後ろの行で見分ける（時刻は秒の単位なので、同じ秒の取り消しと区別できない）。
    """
    requests = _Requests(records.whole_rows(state_file(QUESTIONS)))
    index = requests.by_id.get(request_id)
    if index is None:
        return None
    req = requests.rows[index]
    if (req.get("repo"), req.get("action"), req.get("sha256")) != (repo, Action.APPROVE, sha256):
        return None
    approvals = _Approvals(records.whole_rows(state_file(APPROVALS)))
    used = approvals.used_row(req)
    if used is not None and approvals.approved(repo, sha256):
        return used.get("approver")
    answer, name = _judged(_last_labels().get(request_id), req)
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
    """`reflect` の結果。`placed` は置いたか反映済みか。`saved` は、利用者の保存を別の名前で残したパス（残さなければ None）。

    真偽は `placed` と同じ。偽（拒否）でも `saved` があれば、呼び手は拒否の文にそのパスを載せる。
    """

    placed: bool
    saved: Optional[Path] = None

    def __bool__(self) -> bool:
        return self.placed


def reflect(candidate: Path, record: Callable[[], None], sha256: str, request_id: Optional[str] = None) -> Reflection:
    """承認の記録の錠の中で候補を反映先へ置く。置いたか反映済みなら真、反映先が変わっていたか途中で保存を見つけたら偽の結果を返す。

    先に `recover` で退避を片付け、反映先が反映元のバージョンと同じときだけ `record`（承認の行を書く）を呼んで置き換える。
    反映先がすでに候補と同じ中身（手で同じ内容を書いた・置いた直後の中断）でも `record` を呼ぶ。承認の行が無いと次の起動が止まるためで、
    `record` は承認済みなら何も書かない形にする。
    書けなければ片付けてから `precondition` で止める（承認の行は残る）。片付けで利用者の保存を別の名前で残したら、そのパスを
    結果の `saved` か、止める文に載せる。
    """
    from ndf.contracts import internal

    source = launch_candidates.record_object(launch_candidates.raw_record(candidate.parent / SOURCE_NAME))
    target = Path(str(source.get("target")))
    with records_lock(state_file(APPROVALS)):
        saved = recover(target)
        now, wanted = content_version(target), content_version(candidate)
        rec = {
            "candidate": candidate.parent.name,
            "base": source.get("base"),
            "content": wanted,
            "sha256": sha256,
            "request_id": request_id,
        }
        if now == wanted:
            record()
            _after_reflect(target, candidate, rec)
            return Reflection(True, saved)
        if now != source.get("base"):
            return Reflection(False, saved)
        record()
        launch_candidates.write_launch_record(reflected_file(target, PENDING), rec)
        try:
            placed = _replace(target, candidate, rec["base"])
        except OSError as exc:
            saved = recover(target) or saved
            kept = f"。利用者の保存を {saved} に残した" if saved is not None else ""
            raise internal.InternalError(
                "precondition", f"{target} へ反映できない（{type(exc).__name__}）{kept}。同じコマンドを打ち直す"
            ) from None
        if not placed:
            return Reflection(False, recover(target) or saved)
        _after_reflect(target, candidate, rec)
        return Reflection(True, saved)


def _replace(target: Path, candidate: Path, base: Optional[str]) -> bool:
    """置き換えの手順の 2〜5（退避・照らし直し・反映先が無いときだけ置く・もう一度照らす）。途中で保存を見つけたら偽。"""
    aside = target.with_name(target.name + REFLECTING)
    had = target.exists()
    if had:
        os.rename(target, aside)
        if content_version(aside) != base:
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
    tmp.write_bytes(candidate.read_bytes())
    try:
        os.link(tmp, target)
    except FileExistsError:  # noqa: NDF401 ファイル: 置く間に保存された反映先は残し、呼び手が片付けて止める
        return False
    finally:
        tmp.unlink()
    if had:
        if content_version(aside) != base:
            return False
        aside.unlink()
    return True


def _write_done(target: Path, rec: Mapping[str, Any]) -> None:
    launch_candidates.write_launch_record(reflected_file(target, DONE), {**rec, "target": str(target)})
    reflected_file(target, PENDING).unlink(missing_ok=True)


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

    利用者の保存を別の名前（`launch.json.saved-<値>`）で残したらそのパスを返す。
    """
    pending_path = reflected_file(target, PENDING)
    rec = read_launch_record(pending_path)
    aside = target.with_name(target.name + REFLECTING)
    saved = None
    if aside.exists():
        now, kept = content_version(target), content_version(aside)
        if now is None:
            os.rename(aside, target)
        elif now == rec.get("content"):
            if kept == rec.get("base"):
                aside.unlink()
                _write_done(target, rec)
            else:
                os.replace(aside, target)
        elif kept == rec.get("base"):
            aside.unlink()
        else:
            saved = target.with_name(f"{target.name}.saved-{uuid.uuid4().hex[:8]}")
            os.rename(aside, saved)
    pending_path.unlink(missing_ok=True)
    return saved


def _done_of(candidate: Path) -> Optional[dict[str, Any]]:
    """候補を反映した完了の記録（反映先ごとに最後の 1 件）。無ければ None。"""
    for path in _store(REFLECTED).glob("*" + DONE):
        rec = read_launch_record(path)
        if rec.get("candidate") == candidate.parent.name and isinstance(rec.get("target"), str):
            return rec
    return None


def reflected_candidate(candidate: Path) -> bool:
    """候補を反映した完了の記録があり、反映先が今もその中身か。何も書かない。

    承認の対象のハッシュは `permissions` と MCP の定義だけから求めるので、反映したかはハッシュでなく、候補の中身と反映先を結んだ完了の記録で確かめる。
    """
    return _still_placed(_done_of(candidate))


def _still_placed(rec: Optional[Mapping[str, Any]]) -> bool:
    return rec is not None and content_version(Path(str(rec["target"]))) == rec.get("content")


def reflected(candidate: Path, sha256: str, request_id: Optional[str]) -> bool:
    """候補が無い打ち直しで、完了の記録と反映先の中身を照らす。何も書かない。"""
    rec = _done_of(candidate)
    return rec is not None and (rec.get("sha256"), rec.get("request_id")) == (sha256, request_id) and _still_placed(rec)
