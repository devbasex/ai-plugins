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
import stat
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf import identity

APPROVALS = "launch-approvals"
QUESTIONS = "launch-questions"
ANSWERS = "launch-answers"
CANDIDATES = "launch-candidates"
REFLECTED = "launch-reflected"
# 候補のディレクトリの中身: 候補（反映先の写し）と、反映先・反映元のバージョンの記録
CANDIDATE_NAME = "candidate.json"
SOURCE_NAME = "source.json"
# 反映の途中で反映先を退避した名前の後ろと、反映の途中の記録・完了の記録の名前の後ろ
REFLECTING = ".reflecting"
PENDING = ".pending.json"
DONE = ".done.json"
# 承認の質問の選択肢（hook `launch-answer` は選んだラベルをそのまま記録する）
APPROVE_LABEL = "承認する（承認者: {}）"
DENY_LABEL = "承認しない"
# 反映が済んでいないリクエストの続きの approve に渡す欄（--sha256・--request・--candidate の順）
CONTINUATION = ("sha256", "request_id", "candidate")


class Action(StrEnum):
    """承認の記録の操作。"""

    APPROVE = "approve"
    REVOKE = "revoke"


def canonical_sha(value: Any) -> str:
    """キーの順にそろえた JSON の SHA-256。字下げとキーの順の違いでは変わらない。"""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def _private_to_me(fd: int) -> bool:
    """開いた記録のファイルが、このユーザーの持ち物の普通のファイルで、ほかのユーザーが読み書きできないか。

    `os.open` の `0o600` は作るときにしか効かないため、復元などで権限が広がった既存のファイルは開いた後に確かめる。
    """
    st = os.fstat(fd)
    return stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077


# 中断した追記の末尾を閉じるバイト列。UTF-8 として復号できない 1 バイトを含むので、閉じた行は JSON を書き終えていても読まれない
TORN_END = b"\xff\n"


def _whole_rows(path: Path) -> list[dict[str, Any]]:
    """改行で終わる行だけを読み、壊れた行を飛ばす。読めないか所有者だけの形でなければ空（承認していないと扱う）。

    バイト列のまま改行で区切り、行ごとに復号する。1 行の書きかけ（多バイト文字の途中で切れた行など）でほかの行を捨てないためである。
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return []
    try:
        with os.fdopen(fd, "rb") as f:
            if not _private_to_me(f.fileno()):
                return []
            data = f.read()
    except OSError:
        return []
    rows = (_parsed_row(line) for line in data.split(b"\n")[:-1])  # 最後の要素は改行で終わらない書きかけか空
    return [row for row in rows if row is not None]


def _parsed_row(line: bytes) -> Optional[dict[str, Any]]:
    """1 行の JSON のオブジェクト。書きかけや壊れた行は None（読む側はロックを取らないため、追記の途中の行がありうる）。"""
    try:
        row = json.loads(line.decode("utf-8"))
    except ValueError:  # noqa: NDF401 ファイル: 読む側はロックを取らないので、追記の途中の行・復号できない行・壊れた行は飛ばす
        return None
    return row if isinstance(row, dict) else None


def is_approved(repo: str, sha256: str) -> bool:
    """同じリポジトリのキーとハッシュの最後の行が `approve` か。読む側はロックを取らない。"""
    last = None
    for row in _whole_rows(state_file(APPROVALS)):
        if row.get("repo") == repo and row.get("sha256") == sha256:
            last = row.get("action")
    return last == Action.APPROVE


def records_lock(path: Path) -> Any:
    """記録のファイルの錠（`<ファイル>.lock`）。待ちの上限は `launch.approvals-lock`。"""
    from filelock import FileLock

    from ndf.contracts import limits

    path.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(path) + ".lock", timeout=limits.seconds("launch.approvals-lock"))


def write_row(path: Path, row: Mapping[str, Any]) -> None:
    """1 行を追記する（錠は呼び手が持つ）。ファイルは所有者だけが読める（0600）。

    既存のファイルが所有者だけの形でなければ書かずに `PermissionError` を投げる（承認者名とリポジトリのパスをほかのユーザーへ見せない）。
    末尾が改行で終わらない書きかけ（中断した追記）が残っていれば、先に `TORN_END` で閉じて次の行と分ける。つなげると読み手が両方を捨て、
    打ち直した操作が反映されない。改行だけで閉じると、JSON を書き終えたところで中断した行が、次の無関係な追記で有効になる。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+b") as f:
        if not _private_to_me(f.fileno()):
            raise PermissionError(f"{path} が所有者だけの読み書きでない。所有者を自分にし、`chmod 600` にしてから打ち直す")
        size = os.fstat(f.fileno()).st_size
        torn = size > 0 and os.pread(f.fileno(), 1, size - 1) != b"\n"
        f.write((TORN_END if torn else b"") + (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))


def _at_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def approval_row(repo: str, sha256: str, action: Action, approver: Optional[str] = None, request_id: Optional[str] = None) -> dict[str, Any]:
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


def put_question(request: Mapping[str, Any], question: Mapping[str, Any], candidate: Optional[Path] = None) -> None:
    """承認のリクエスト（`request_id`・`repo`・`sha256`・反映先 `target`・会話の id `session`）と、質問の中身（`questions`）のハッシュを残す。

    反映元のバージョンは候補の記録か、今の反映先のバージョン。
    """
    target = Path(request["target"])
    row = {
        **request, "at": _at_now(), "action": str(Action.APPROVE), "target": str(target),
        "base": read_launch_record(candidate.parent / SOURCE_NAME).get("base") if candidate is not None else content_version(target),
        "candidate": str(candidate) if candidate is not None else None,
        "candidate_version": content_version(candidate) if candidate is not None else None,
        "question_sha": canonical_sha(question["questions"]),
    }  # fmt: skip
    _append_row(QUESTIONS, row)


def request_answer(request_id: str) -> tuple[Optional[str], Optional[str]]:
    """リクエストへの最後の回答（`approve`・`deny`・None）と承認者の名前。"""
    label = None
    for row in _whole_rows(state_file(ANSWERS)):
        if row.get("request_id") == request_id:
            label = row.get("label")
    head, _, tail = APPROVE_LABEL.partition("{}")
    if isinstance(label, str) and label.startswith(head) and label.endswith(tail) and len(label) > len(head) + len(tail):
        return "approve", label[len(head) : len(label) - len(tail)]
    return ("deny", None) if label == DENY_LABEL else (None, None)


def approve_by_answer(repo: str, sha256: str, request_id: str) -> Optional[str]:
    """承認の記録の錠の中で、リクエストの回答を設計書の「人の確認」の条件で照らし、承認の行を足して承認者を返す。使えなければ None。

    同じリクエストの承認の行があり承認済みなら、行を足さずに承認者を返す（中断の後の打ち直し）。
    """
    requests = _whole_rows(state_file(QUESTIONS))
    index = next((i for i in reversed(range(len(requests))) if requests[i].get("request_id") == request_id), None)
    if index is None:
        return None
    req = requests[index]
    if (req.get("repo"), req.get("action"), req.get("sha256")) != (repo, Action.APPROVE, sha256):
        return None
    approvals = _whole_rows(state_file(APPROVALS))
    used = [r for r in approvals if r.get("request_id") == request_id and r.get("repo") == repo and r.get("sha256") == sha256]
    if used and is_approved(repo, sha256):
        return used[-1].get("approver")
    answer, name = request_answer(request_id)
    newer = any(r.get("target") == req.get("target") for r in requests[index + 1 :])
    moved = content_version(Path(req["target"])) not in (req.get("base"), req.get("candidate_version"))
    revoked = any(
        r.get("action") == Action.REVOKE and r.get("repo") == repo and r.get("sha256") == sha256 and str(r.get("at")) >= req["at"] for r in approvals
    )
    if answer != "approve" or newer or moved or revoked:
        return None
    write_row(state_file(APPROVALS), approval_row(repo, sha256, Action.APPROVE, name, request_id))
    return name


def pending_request(repo: str, target: Path) -> Optional[Mapping[str, Any]]:
    """その反映先の最新のリクエストで、「承認する」と回答があり、反映（候補が無ければ承認の行）が済んでいないもの。"""
    rows = [r for r in _whole_rows(state_file(QUESTIONS)) if r.get("repo") == repo and r.get("target") == str(target)]
    if not rows or request_answer(str(rows[-1].get("request_id")))[0] != "approve":
        return None
    req = rows[-1]
    done = read_launch_record(reflected_file(target, DONE))
    if done.get("request_id") == req["request_id"]:
        return None
    if req.get("candidate") is None and any(r.get("request_id") == req["request_id"] for r in _whole_rows(state_file(APPROVALS))):
        return None
    return req


def _worktree_gone(target: object) -> bool:
    """反映先の worktree（`<worktree>/.ndf/launch.json` の 2 つ上）が無いか。"""
    return not isinstance(target, str) or not Path(target).parents[1].exists()


def new_candidate(repo: str, target: Path) -> Path:
    """反映先の写しの候補と、反映先・反映元のバージョンの記録を作り、候補のパスを返す。

    先に、反映先の worktree が無い候補と完了の記録、「承認しない」と回答たリクエストの候補を消す。
    """
    home = _store(CANDIDATES) / _short_hash(repo)
    denied = {r.get("candidate") for r in _whole_rows(state_file(QUESTIONS)) if request_answer(str(r.get("request_id")))[0] == "deny"}
    for made in sorted(home.glob("*")):
        if _worktree_gone(read_launch_record(made / SOURCE_NAME).get("target")) or str(made / CANDIDATE_NAME) in denied:
            shutil.rmtree(made, ignore_errors=True)
    for record in _store(REFLECTED).glob("*" + DONE):
        if _worktree_gone(read_launch_record(record).get("target")):
            record.unlink(missing_ok=True)
    made = home / uuid.uuid4().hex
    _write_private(made / CANDIDATE_NAME, target.read_bytes() if target.exists() else b'{"version": 1}\n')
    write_launch_record(made / SOURCE_NAME, {"target": str(target), "base": content_version(target)})
    return made / CANDIDATE_NAME


def reflect(candidate: Path, record: Callable[[], None], sha256: str, request_id: Optional[str] = None) -> bool:
    """承認の記録の錠の中で候補を反映先へ置く。置いたか反映済みなら真、反映先が変わっていたか途中で保存を見つけたら偽。

    先に `recover` で退避を片付け、反映先が反映元のバージョンと同じときだけ `record`（承認の行を書く）を呼んで置き換える。
    書けなければ片付けてから `precondition` で止める（承認の行は残る）。
    """
    from ndf.contracts import internal

    source = read_launch_record(candidate.parent / SOURCE_NAME)
    target = Path(str(source.get("target")))
    with records_lock(state_file(APPROVALS)):
        recover(target)
        now, wanted = content_version(target), content_version(candidate)
        rec = {"candidate": candidate.parent.name, "base": source.get("base"), "content": wanted, "sha256": sha256, "request_id": request_id}
        if now == wanted:
            _after_reflect(target, candidate, rec)
            return True
        if now != source.get("base"):
            return False
        record()
        write_launch_record(reflected_file(target, PENDING), rec)
        try:
            placed = _replace(target, candidate, rec["base"])
        except OSError as exc:
            recover(target)
            raise internal.InternalError("precondition", f"{target} へ反映できない（{type(exc).__name__}）。同じコマンドを打ち直す") from None
        if not placed:
            recover(target)
            return False
        _after_reflect(target, candidate, rec)
        return True


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
    """完了の記録を書いてから、候補と、反映元のバージョンが合わなくなった同じ反映先の候補を消す。"""
    _write_done(target, rec)
    now = content_version(target)
    for made in candidate.parent.parent.glob("*"):
        source = read_launch_record(made / SOURCE_NAME)
        if made == candidate.parent or (source.get("target") == str(target) and source.get("base") != now):
            shutil.rmtree(made, ignore_errors=True)


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
