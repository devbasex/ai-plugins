"""起動の承認のハッシュと記録（#1959）。

承認の対象は呼び手（`launch_settings`）が読んだ値を引数で受け、このモジュールは `launch_settings` を読まない。承認の記録は利用者の状態の
保存先にだけ置き（リポジトリのファイルは承認の根拠にしない）、追記だけで、所有者だけが読める。同じリポジトリのキーとハッシュの最後の行が
`approve` なら承認済みである。hook からも読むので、モジュールの頭では標準ライブラリと `identity` だけを読み込む。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf import identity
from ndf.adapters import launch_records as records

APPROVALS = "launch-approvals"
QUESTIONS = "launch-questions"
ANSWERS = "launch-answers"
CANDIDATES = "launch-candidates"
REFLECTED = "launch-reflected"
# 候補のディレクトリの中身: 候補（反映先の写し）と、反映先・反映元のバージョン・作った時の候補のバージョンの記録
CANDIDATE_NAME = "candidate.json"
SOURCE_NAME = "source.json"
# 退避した候補の中の、消してよいバージョン（「承認しない」と回答したリクエストの候補のバージョン）の記録
DISCARD_NAME = "discard.json"
# 候補のディレクトリの作業中の名前の頭。作成途中（DRAFTING）、退避（REMOVING）、消す前の隔離（SETTLING）。
# 作成と掃除は候補の錠の中だけで行うので、錠の中で見つかる作成途中と隔離のディレクトリは中断の残りである
DRAFTING = ".draft-"
REMOVING = ".removing-"
SETTLING = ".settling-"
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
    last = None
    for row in records.whole_rows(state_file(APPROVALS)):
        if row.get("repo") == repo and row.get("sha256") == sha256:
            last = row.get("action")
    return last == Action.APPROVE


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


def _write_private(path: Path, data: bytes) -> None:
    """所有者だけが読めるファイルを、一時ファイルからの付け替えで書く。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_launch_record(path: Path, obj: Mapping[str, Any]) -> None:
    _write_private(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


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
    リクエストを足すまでの間に、掃除（`_sweep`・`_reclaim`）が同じ候補を退避したり消したりしないためである。戻せる候補が無ければ
    リクエストを作らずに `precondition` で止める（消えた候補の承認を問わない）。`make_question` が `sha256`（候補から求めた承認の対象の
    ハッシュ）を返せば、リクエストのハッシュにはその値を使う。候補を読んだのと同じ錠の中で求めた値だからである。
    """
    if candidate is None:
        question = make_question()
        _put_request(request, question, None)
        return question
    from ndf.contracts import internal

    with records_lock(candidate.parents[1]):
        _restore(candidate)
        if not candidate.exists():
            raise internal.InternalError("precondition", f"候補 {candidate} が無い。`draft` で候補を作り直す")
        question = make_question()
        _put_request(request, question, candidate)
    return question


def _put_request(request: Mapping[str, Any], question: Mapping[str, Any], candidate: Optional[Path]) -> None:
    target = Path(request["target"])
    row = {
        **request, **({"sha256": question["sha256"]} if "sha256" in question else {}), "at": _at_now(), "action": str(Action.APPROVE), "target": str(target),
        "base": read_launch_record(candidate.parent / SOURCE_NAME).get("base") if candidate is not None else content_version(target),
        "candidate": str(candidate) if candidate is not None else None,
        "candidate_version": content_version(candidate) if candidate is not None else None,
        "question_sha": canonical_sha(question["questions"]),
        "options": records.option_labels(question["questions"]),
        "approvals_seen": len(records.whole_rows(state_file(APPROVALS))),
    }  # fmt: skip
    _append_row(QUESTIONS, row)


def _last_labels() -> dict[Any, Any]:
    """リクエストの識別子ごとの最後の回答のラベル。回答の記録は 1 回だけ読む。"""
    return {row.get("request_id"): row.get("label") for row in records.whole_rows(state_file(ANSWERS))}


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


def _latest_request(requests: list[dict[str, Any]], request_id: str) -> Optional[int]:
    return next((i for i in reversed(range(len(requests))) if requests[i].get("request_id") == request_id), None)


def request_answer(request_id: str) -> tuple[Optional[str], Optional[str]]:
    """リクエストへの最後の回答（`approve`・`deny`・None）と承認者の名前。"""
    requests = records.whole_rows(state_file(QUESTIONS))
    index = _latest_request(requests, request_id)
    return (None, None) if index is None else _judged(_last_labels().get(request_id), requests[index])


def approve_by_answer(repo: str, sha256: str, request_id: str) -> Optional[str]:
    """承認の記録の錠の中で、リクエストの回答を設計書の「人の確認」の条件で照らし、承認の行を足して承認者を返す。使えなければ None。

    同じリクエストの承認の行があり承認済みなら、行を足さずに承認者を返す（中断の後の打ち直し）。リクエストの後の取り消しは、
    リクエストを作った時点の承認の記録の行数より後ろの行で見分ける（時刻は秒の単位なので、同じ秒の取り消しと区別できない）。
    """
    requests = records.whole_rows(state_file(QUESTIONS))
    index = _latest_request(requests, request_id)
    if index is None:
        return None
    req = requests[index]
    if (req.get("repo"), req.get("action"), req.get("sha256")) != (repo, Action.APPROVE, sha256):
        return None
    approvals = records.whole_rows(state_file(APPROVALS))
    used = [r for r in approvals if r.get("request_id") == request_id and r.get("repo") == repo and r.get("sha256") == sha256]
    if used and is_approved(repo, sha256):
        return used[-1].get("approver")
    answer, name = _judged(_last_labels().get(request_id), req)
    newer = any(r.get("target") == req.get("target") for r in requests[index + 1 :])
    moved = content_version(Path(req["target"])) not in (req.get("base"), req.get("candidate_version"))
    seen = req.get("approvals_seen")
    after = approvals[seen:] if isinstance(seen, int) and not isinstance(seen, bool) and seen >= 0 else approvals
    revoked = any(r.get("action") == Action.REVOKE and r.get("repo") == repo and r.get("sha256") == sha256 for r in after)
    if answer != "approve" or newer or moved or revoked:
        return None
    write_row(state_file(APPROVALS), approval_row(repo, sha256, Action.APPROVE, name, request_id))
    return name


def pending_request(repo: str, target: Path) -> Optional[Mapping[str, Any]]:
    """その反映先の最新のリクエストで、「承認する」と回答があり、反映（候補が無ければ承認の行）が済んでいないもの。"""
    rows = [r for r in records.whole_rows(state_file(QUESTIONS)) if r.get("repo") == repo and r.get("target") == str(target)]
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
    return not isinstance(target, str) or not Path(target).parents[1].exists()


def _candidates(home: Path) -> list[Path]:
    """そろった候補のディレクトリ。作業中の名前（頭が `.`）のものは含めない。"""
    return sorted(made for made in home.glob("*") if not made.name.startswith("."))


def _untouched(made: Path, kept: Optional[str] = None) -> bool:
    """候補が、作った時（`written`）か `kept` のバージョンのままか。作った後に直した候補（Skill か利用者が設定案を書いた候補）は掃除で消さない。"""
    now = content_version(made / CANDIDATE_NAME)
    return now is None or now in (read_launch_record(made / SOURCE_NAME).get("written"), kept)


def _discard(made: Path, kept: Optional[str] = None) -> None:
    """候補の錠の中で、直していない候補を、消す前の退避（`REMOVING` の名前）へ移す。中身はその場では消さない。

    名前を変えた後は、元のパスへの保存はディレクトリが無いので失敗し、エディタの中の設定案は残る。名前を変える前に保存された
    中身と、変えた後も開いたままのファイルへ書かれた中身は退避の中に残り、次の掃除（`_reclaim`）が比べて元の名前へ戻す。
    """
    if not _untouched(made, kept):
        return
    aside = made.with_name(REMOVING + made.name)
    try:
        os.rename(made, aside)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 退避の前に無くなった候補は、退避するものが無い
        return
    if kept is not None:
        write_launch_record(aside / DISCARD_NAME, {"kept": kept})


def _open_inodes(wanted: set[tuple[int, int]]) -> Optional[set[tuple[int, int]]]:
    """`wanted` のうち、いまどれかのプロセスが開いているもの（`(st_dev, st_ino)` の組）。`/proc` の無い環境では確かめられないので None。

    退避した候補をまとめて 1 回だけ走査する。走査するのは `/proc/<pid>/fd/` の項目の `stat` だけで、子のプロセスは起動しない。
    """
    proc = Path("/proc")
    if not (proc / "self" / "fd").is_dir():
        return None
    if not wanted:
        return set()
    return {key for key in map(_fd_target, proc.glob("[0-9]*/fd/*")) if key in wanted}


def _fd_target(fd: Path) -> Optional[tuple[int, int]]:
    """`/proc` の fd が指すファイルの `(st_dev, st_ino)`。調べる間に閉じた fd と、読めないほかのユーザーの fd は None。"""
    try:
        opened = fd.stat()
    except OSError:  # noqa: NDF401 ファイル: 調べる間に閉じた fd と、読めないほかのユーザーの fd は、候補を指さないものとして飛ばす
        return None
    return opened.st_dev, opened.st_ino


def _inodes(folder: Path) -> set[tuple[int, int]]:
    """候補のディレクトリそのものと、その中のファイルの `(st_dev, st_ino)`。ディレクトリの fd からファイルを開く書き手も見分ける。"""
    keys = set()
    for item in (folder, *folder.iterdir()):
        st = item.stat()
        keys.add((st.st_dev, st.st_ino))
    return keys


def _original_name(aside: Path) -> str:
    """退避（`.removing-<名前>`）か、消す前の隔離（`.settling-<乱数>.<名前>`）の、候補の元の名前。"""
    if aside.name.startswith(SETTLING):
        return aside.name.rsplit(".", 1)[1]
    return aside.name[len(REMOVING) :]


def _put_back(aside: Path) -> Path:
    (aside / DISCARD_NAME).unlink(missing_ok=True)
    back = aside.with_name(_original_name(aside))
    os.rename(aside, back)
    return back


def _reclaim(home: Path) -> set[str]:
    """候補の錠の中で、作業中のディレクトリを片付け、元の名前へ戻した候補の名前を返す。

    作成途中は中断の残りなので消す。前の掃除が退避した候補は、消してよいと保証できるものだけを消し、ほかは元の名前へ戻す。
    戻した候補は、呼び手がこの掃除では退避し直さない（利用者が元のパスへ保存できるようにし、判定は次の掃除でやり直す）。

    消す前に、退避した候補を誰も知らない名前（`SETTLING` と乱数）へ移してから、開いたままのプロセスを `/proc` で 1 回だけ調べ、
    その後に中身を比べる。移した後は、どのパスからも新しく開けない。そのため、調べた時点で開いているプロセスが無ければ、
    比べた後に書き込む者はいない。開いたままのプロセスがある候補と、`/proc` が無く確かめられない環境の候補は消さない。
    消すのは、作った時の中身（NDF が作った写し）か、反映した中身のままの候補だけである。
    """
    for left in home.glob(DRAFTING + "*"):
        shutil.rmtree(left, ignore_errors=True)
    settling = [_isolate(left) for left in sorted([*home.glob(REMOVING + "*"), *home.glob(SETTLING + "*")])]
    if not settling:
        return set()
    keys = {aside: _inodes(aside) for aside in settling}
    held = _open_inodes(set().union(*keys.values()))
    restored = set()
    for aside in settling:
        if held is not None and not keys[aside] & held and _untouched(aside, read_launch_record(aside / DISCARD_NAME).get("kept")):
            shutil.rmtree(aside, ignore_errors=True)
        else:
            restored.add(_put_back(aside).name)
    return restored


def _isolate(aside: Path) -> Path:
    """退避した候補を、消す前の隔離の名前（`.settling-<乱数>.<元の名前>`）へ移す。"""
    isolated = aside.with_name(f"{SETTLING}{uuid.uuid4().hex}.{_original_name(aside)}")
    os.rename(aside, isolated)
    return isolated


def _restore(candidate: Path) -> None:
    """候補の錠の中で、退避か隔離の途中で止まった候補を元の名前へ戻す（同じ候補でリクエストを作り直したとき）。"""
    home, name = candidate.parents[1], candidate.parent.name
    if candidate.parent.exists():
        return
    for aside in [home / f"{REMOVING}{name}", *home.glob(f"{SETTLING}*.{name}")]:
        if aside.exists():
            _put_back(aside)
            return


def _sweep(home: Path, restored: set[str]) -> None:
    """候補の錠の中で、反映先の worktree が無い候補と、最新のリクエストに「承認しない」と回答した候補を退避する（直したものは残す）。

    「承認しない」の判定は、候補ごとの最新のリクエストで行う。否認の後に同じ候補で作り直したリクエストが回答待ちか承認済みなら、
    その候補は消さない。どちらの場合も、片付けるのは作った時の中身のままの候補だけである。「承認しない」は反映を断る回答で、
    利用者が書いた設定案を消してよいという回答ではないので、リクエストの前に直した候補も残す。
    リクエストは同じ錠の中で作るので（`put_question`）、ここで読んだリクエストの一覧は退避を決めるまで変わらない。
    同じ錠の中の `_reclaim` が元の名前へ戻した候補（`restored`）は、この掃除では退避しない。
    """
    latest: dict[Any, dict[str, Any]] = {}
    for row in records.whole_rows(state_file(QUESTIONS)):
        if row.get("candidate") is not None:
            latest[row.get("candidate")] = row
    labels = _last_labels()
    for made in _candidates(home):
        if made.name in restored:
            continue
        req = latest.get(str(made / CANDIDATE_NAME))
        denied = req is not None and _judged(labels.get(req.get("request_id")), req)[0] == "deny"
        if denied or _worktree_gone(read_launch_record(made / SOURCE_NAME).get("target")):
            _discard(made)


def _snapshot(target: Path) -> tuple[bytes, Optional[str]]:
    """反映先の中身と、同じ読み取りから求めた反映元のバージョン。無ければ空の設定と None。"""
    try:
        data = target.read_bytes()
    except FileNotFoundError:  # 無い反映先は「無し」のバージョンとして空の設定を写す
        return b'{"version": 1}\n', None
    return data, hashlib.sha256(data).hexdigest()


def new_candidate(repo: str, target: Path) -> Path:
    """反映先の写しの候補と、反映先・反映元のバージョン・作った時の候補のバージョンの記録を作り、候補のパスを返す。

    リポジトリごとの候補の錠の中で、中断の残りを片付け、掃除（`_sweep`）をしてから作る。完了の記録も、反映先の worktree が
    無ければ消す。候補は作成途中の名前（`DRAFTING`）のディレクトリで候補と記録をそろえてから付け替え、途中で失敗すれば消す。
    写しと反映元のバージョンは反映先の同じ読み取りから作る（別に読むと、間の保存で古い写しに新しいバージョンが付く）。
    """
    home = _store(CANDIDATES) / _short_hash(repo)
    with records_lock(home):
        _sweep(home, _reclaim(home))
        for record in _store(REFLECTED).glob("*" + DONE):
            if _worktree_gone(read_launch_record(record).get("target")):
                record.unlink(missing_ok=True)
        name = uuid.uuid4().hex
        drafting, made = home / f"{DRAFTING}{name}", home / name
        data, base = _snapshot(target)
        try:
            _write_private(drafting / CANDIDATE_NAME, data)
            write_launch_record(drafting / SOURCE_NAME, {"target": str(target), "base": base, "written": hashlib.sha256(data).hexdigest()})
            os.rename(drafting, made)
        except BaseException:
            shutil.rmtree(drafting, ignore_errors=True)
            raise
    return made / CANDIDATE_NAME


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
    書けなければ片付けてから `precondition` で止める（承認の行は残る）。片付けで利用者の保存を別の名前で残したら、そのパスを
    結果の `saved` か、止める文に載せる。
    """
    from ndf.contracts import internal

    source = read_launch_record(candidate.parent / SOURCE_NAME)
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
            _after_reflect(target, candidate, rec)
            return Reflection(True, saved)
        if now != source.get("base"):
            return Reflection(False, saved)
        record()
        write_launch_record(reflected_file(target, PENDING), rec)
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
    write_launch_record(reflected_file(target, DONE), {**rec, "target": str(target)})
    reflected_file(target, PENDING).unlink(missing_ok=True)


def _after_reflect(target: Path, candidate: Path, rec: Mapping[str, Any]) -> None:
    """完了の記録を書いてから、候補の錠の中で、反映した候補と、反映元のバージョンが合わなくなった同じ反映先の候補を退避する。

    先に前の掃除が退避した候補を片付ける（`_reclaim`）。そこで元の名前へ戻した候補は、この掃除では退避しない。反映した候補は、反映した中身か作った時の中身のままなら退避する。
    ほかの候補は、作った時の中身のままのものだけを退避する。作った後に直した候補は、反映元が変わっても残す（書いた設定案を
    失わないため）。退避した候補は次の掃除が比べ直してから消す。
    """
    _write_done(target, rec)
    now = content_version(target)
    home = candidate.parent.parent
    with records_lock(home):
        restored = _reclaim(home)
        if candidate.parent.name not in restored:
            _discard(candidate.parent, rec.get("content"))
        for made in _candidates(home):
            source = read_launch_record(made / SOURCE_NAME)
            if made.name not in restored and source.get("target") == str(target) and source.get("base") != now:
                _discard(made)


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


def reflected(candidate: Path, sha256: str, request_id: Optional[str]) -> bool:
    """候補が無い打ち直しで、完了の記録と反映先の中身を照らす。何も書かない。"""
    for path in _store(REFLECTED).glob("*" + DONE):
        rec = read_launch_record(path)
        if (rec.get("candidate"), rec.get("sha256"), rec.get("request_id")) == (candidate.parent.name, sha256, request_id):
            return content_version(Path(rec["target"])) == rec.get("content")
    return False
