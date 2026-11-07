"""NDF の状態の置き場（#2766）。リポジトリの外に書く状態を、持ち主ごとに 3 つの根へ分ける。

| 持ち主 | 根（既定） | 置くもの | 理由 |
| --- | --- | --- | --- |
| チェックアウト | `<git の共通ディレクトリ>/ndf/state`（`checkout_home`） | 耐久の記録 `dbos/`・プランの状態 `sv/` | 絶対パスから鍵を作る。永続で、チェックアウトごとに別になる（同じパスの別のチェックアウトと混ざらない） |
| 利用者 | `~/.claude/ndf/state`（`user_home`） | 追記だけの台帳（`ask.jsonl`・MVV の記録・`metrics/`・`usage/`・`checks/`・`stages/` など。`USER_KINDS`） | Claude Code のログインと同じ永続の木。複数のコンテナで共有して集計に使う |
| コンテナのプロセス | `~/.local/state/ndf`（`container_home`） | relay・待ちの通知・guard・context（pid とセッションを持つもの） | pid の名前空間はコンテナごとに別。プロセスはコンテナとともに消える |

`NDF_STATE_HOME` か `$XDG_STATE_HOME/ndf` を指定すると、3 つとも その 1 つの根になる（テストと明示の指定）。
各置き場の個別の上書き（`NDF_DBOS_DIR` など）は呼ぶ側が先に見る。`CLAUDE_CONFIG_DIR` は使わない（relay が
アカウントごとの子へ別の値を渡すため、置き場が割れる）。

旧の置き場（`~/.local/state/ndf`）は読まない。更新したら各コンテナで、主のリポジトリの作業ツリーから一度
`state-home.py migrate`（`migrate`）を打って旧から移す（2026-11-07 の決定。毎回旧を確かめない）:

- 利用者の台帳（`USER_KINDS`）を利用者の根へ足し合わせる。`.jsonl` は旧と新の行を時刻（`at`）の順に並べて書き直す
  （読み手は末尾を最新として読んでよい）
- チェックアウトの記録（`dbos/`・`sv/`）を、打った作業ツリーのチェックアウトの根へ移す（`_move_checkout`）。
  プランの状態は `plan.json` のパスから持ち主のチェックアウトが分かればそこへ移す（`_sv_owner`）。耐久の記録は持ち主を
  示すものを持たない（入力は空、ファイル名は識別の文字列のハッシュ）ため、打った場所へ移す。作業ツリーの外で打てば移さない。動いている記録（ロックが持たれている耐久の記録・ステップの子が生きているプラン）と
  移し先に同じ名前があるものは旧に残す
- 何度打ってもよい（移すものが無ければ何もしない）

シェルの同じ順は `skills/development-workflow/scripts/lib/workflow-common.sh` の `ndf_user_state_home` にある。
"""

from __future__ import annotations

import datetime
import fcntl  # 耐久の記録のロック（filelock と同じ flock）を標準ライブラリだけで確かめる
import json
import os
import shutil
from pathlib import Path
from typing import Mapping, Optional, Sequence

# 利用者の根に置く台帳（ファイルかディレクトリの名前）。ほかはコンテナの根に残る
USER_KINDS = (
    "ask.jsonl",
    "mvv-gate.jsonl",
    "project-mvv.jsonl",
    "project-mvv-signals.jsonl",
    "gate-rejections.jsonl",
    "project-mvv",
    "metrics",
    "usage",
    "checks",
    "stages",
    "estimate",
    "test-run",
)
_HELD = ".migrating-"  # migrate が名前を外した台帳の接尾辞（`<名前>.jsonl.migrating-<pid>`）
_TMP = ".tmp-"  # migrate が並べ直した台帳を書く一時ファイルの接尾辞（`.<名前>.jsonl.tmp-<pid>`）


def _state_env(env: Optional[Mapping[str, str]]) -> Mapping[str, Optional[str]]:
    if env is not None:
        return env
    return {  # 読む変数を名前で書く（契約の検査が読みを拾う）
        "NDF_STATE_HOME": os.environ.get("NDF_STATE_HOME"),
        "XDG_STATE_HOME": os.environ.get("XDG_STATE_HOME"),
        "HOME": os.environ.get("HOME"),
    }


def explicit_home(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """明示した根（`NDF_STATE_HOME` → `$XDG_STATE_HOME/ndf`）。無ければ None。空の値は無いものとする。"""
    env = _state_env(env)
    if env.get("NDF_STATE_HOME"):
        return Path(env["NDF_STATE_HOME"])
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "ndf"
    return None


def _home_dir(env: Mapping[str, Optional[str]]) -> Path:
    return Path(env.get("HOME") or Path.home())


def legacy_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """旧の根（`~/.local/state/ndf`）。コンテナの根の既定と同じ場所である。"""
    return _home_dir(_state_env(env)) / ".local" / "state" / "ndf"


def container_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """コンテナのプロセスの状態の根。"""
    return explicit_home(env) or legacy_home(env)


def _user_default(env: Optional[Mapping[str, str]] = None) -> Path:
    return _home_dir(_state_env(env)) / ".claude" / "ndf" / "state"


def user_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """利用者の台帳の根。旧の置き場は読まない（`migrate` で移す）。"""
    return explicit_home(env) or _user_default(env)


def find_git_dir(start: Path) -> tuple[Path, Path] | None:
    """(リポジトリの根, git の共通ディレクトリ)。`.git` を上へ探して読む（`git` を起動しない）。git でなければ None。"""
    start = Path(start).resolve()
    for d in (start, *start.parents):
        dot = d / ".git"
        if dot.is_dir():
            return d, _common_of(dot)
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text[len("gitdir:") :].strip())
            gitdir = gitdir if gitdir.is_absolute() else (d / gitdir)
            return d, _common_of(gitdir.resolve())
    return None


def _common_of(gitdir: Path) -> Path:
    f = gitdir / "commondir"
    if f.is_file():
        try:
            rel = f.read_text(encoding="utf-8").strip()
        except OSError:
            return gitdir
        p = Path(rel)
        return (p if p.is_absolute() else gitdir / p).resolve()
    return gitdir


def checkout_home(start: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> Path:
    """チェックアウトの記録の根。`start`（省けば作業ディレクトリ）の git の共通ディレクトリの下。git の外はコンテナの根。"""
    explicit = explicit_home(env)
    if explicit is not None:
        return explicit
    try:
        got = find_git_dir(Path(start) if start is not None else Path.cwd())
    except OSError:
        got = None
    return got[1] / "ndf" / "state" if got else container_home(env)


def _held_of(path: Path) -> Optional[tuple[str, int]]:
    """名前を外した台帳（`<名前>.jsonl.migrating-<pid>` か `<名前>.jsonl.migrating-<pid>-<n>`）なら (元の名前, pid)。違えば None。"""
    name, sep, tag = path.name.rpartition(_HELD)
    pid = tag.split("-", 1)[0]
    if sep and name.endswith(".jsonl") and pid.isdigit() and (tag == pid or tag[len(pid) + 1 :].isdigit()):
        return name, int(pid)
    return None


def _held_running(pid: int) -> bool:
    """名前を外した migrate のプロセスが生きているか（`os.kill(pid, 0)`。psutil を要する `procs` を読まない）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # 権限が無い（別の利用者のプロセスが生きている）
    return True


def _row_time(line: bytes) -> Optional[datetime.datetime]:
    """台帳の 1 行の時刻（`at`。実物の台帳はどれもこのキーを持つ）。無い・読めなければ None。時差の無い値は UTC とみなす。"""
    try:
        at = json.loads(line).get("at")
        d = datetime.datetime.fromisoformat(at.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _by_time(chunks: Sequence[Sequence[bytes]]) -> list[bytes]:
    """まとまり（移し先 → 旧の台帳。これまでの追記の並び）の行を、時刻の順に並べる（安定。同じ時刻は元の並び）。
    時刻の無い行は、同じまとまりの直前の行（先頭なら直後の行）の時刻を借り、元の隣り合いを保つ。
    時刻の無い行だけのまとまりは、それまでの最も新しい時刻を借りて末尾に残る（追記と同じ並び）。"""
    keyed: list[tuple[datetime.datetime, bytes]] = []
    last = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    for lines in chunks:
        times = [_row_time(line) for line in lines]
        head = next((t for t in times if t is not None), last)
        cur = head
        for t, line in zip(times, lines):
            cur = t if t is not None else cur
            keyed.append((cur, line))
        last = max([last, *(t for t in times if t is not None)])
    keyed.sort(key=lambda kv: kv[0])
    return [line for _, line in keyed]


def _lines_of(data: bytes) -> list[bytes]:
    return [line + b"\n" for line in data.splitlines() if line.strip()]


def _side(dst: Path) -> Path:
    """`dst` の名前を外した先。止まった前の移行の残り（同じ pid の名前）を上書きしない名前を選ぶ。"""
    n = 0
    while True:
        side = dst.with_name(f"{dst.name}{_HELD}{os.getpid()}" + (f"-{n}" if n else ""))
        if not side.exists():
            return side
        n += 1


def _mode_of(paths: Sequence[Path]) -> int:
    """台帳 `paths` の権限の厳しい方（ビットの積）。問いと答えを持つ `ask.jsonl` の 0600 を広げない。"""
    mode = 0o777
    for path in paths:
        mode &= path.stat().st_mode & 0o777
    return mode


def _drain(read: dict[Path, int], dst: Path) -> None:
    """名前を外した台帳 `read`（パス → 読んだ大きさ）に、読んだ後で足された行を `dst` の末尾へ足す。
    名前を外す前に開いた書き手の fd は外した台帳へ書き続けるため、消す前に拾う。大きさが変わらなくなるまで繰り返す。"""
    while True:
        grown = False
        for path, size in read.items():
            data = path.read_bytes()
            if len(data) > size:
                with dst.open("ab") as f:
                    f.write(b"".join(_lines_of(data[size:])))
                read[path] = len(data)
                grown = True
        if not grown:
            return


def _rewrite_by_time(dst: Path, sources: Sequence[tuple[Path, bool]]) -> None:
    """旧の台帳 `sources`（(パス, 止まった移行の残りか)）を `dst` と合わせ、時刻の順に並べて `dst` を書き直し、`sources` を消す。

    `dst` も名前を外してから読み（`<dst>.migrating-<pid>`）、並べたものを一時ファイルへ書いて `os.link` で元の名前へ置く。
    一時ファイルは作る時点から、移行元・移行先の台帳の厳しい方の権限にする（umask によらない）。
    間に別のプロセスが `dst` を作り直して追記していれば、その名前も外して末尾へ足してから置き直す（追記を落とさない）。
    置いた後、名前を外した台帳を既に開いていた書き手の追記を `_drain` で末尾へ足してから消す。最後の確かめから
    消すまでの間（ファイルを 1 つ読んで消すだけの間）の追記は拾えないが、その間はごく短い。
    止まった移行の残りは、`dst` に既にある行を足さない（前の移行が足した後、消す前に止まった）。
    途中で止まっても、名前を外した `dst` は次の migrate が `_resume_held` で元の名前へ戻す。"""
    read: dict[Path, int] = {}
    current: list[bytes] = []
    if dst.exists():
        side = _side(dst)
        os.rename(dst, side)
        data = side.read_bytes()
        read[side] = len(data)
        current = _lines_of(data)
    have: dict[bytes, int] = {}
    for line in current:
        have[line] = have.get(line, 0) + 1
    chunks: list[list[bytes]] = []
    for src, resumed in sources:
        data = src.read_bytes()
        read[src] = len(data)
        rows = []
        for line in _lines_of(data):
            if resumed and have.get(line, 0) > 0:
                have[line] -= 1
                continue
            rows.append(line)
        chunks.append(rows)
    mode = _mode_of(list(read))
    tmp = dst.with_name(f".{dst.name}{_TMP}{os.getpid()}")  # `.jsonl` で終わらない（読み手が拾わない）
    tmp.unlink(missing_ok=True)  # 止まった前の書き直しの残りは移し先と同じ実体のことがある。書き込む前に切り離す
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.fchmod(fd, mode)  # umask で削られた分を戻す（広げるのは移行元・移行先の権限まで）
    with os.fdopen(fd, "wb") as f:
        f.write(b"".join(_by_time([current, *chunks])))
    while True:
        try:
            os.link(tmp, dst)
            break
        except FileExistsError:
            side = _side(dst)
            os.rename(dst, side)  # 書き直しの間に作り直された台帳。行は並べたものより新しい
            data = side.read_bytes()
            read[side] = len(data)
            os.chmod(tmp, _mode_of([tmp, side]))
            with tmp.open("ab") as f:
                f.write(b"".join(_lines_of(data)))
    tmp.unlink()
    _drain(read, dst)
    for path in read:
        path.unlink()


def _merge_ledger(src: Path, dst: Path, moved: list, kept: list) -> None:
    """`src` を `dst` へ足し合わせる。`.jsonl` は両方の行を時刻の順に並べて書き直し（`_rewrite_by_time`）、無いものは移し、
    同じ名前の他のファイルは旧に残す。止まった migrate が名前を外したまま残した台帳（`_held_of`）も足す。"""
    if src.is_dir() and not src.is_symlink():
        for child in sorted(src.iterdir(), key=lambda c: (_held_of(c) is None, c.name)):
            held = _held_of(child)
            if held is None:
                _merge_ledger(child, dst / child.name, moved, kept)
            else:
                _resume_held(child, dst / held[0], held[1], moved, kept)
        try:
            src.rmdir()
        except OSError:
            pass  # 残したものがある
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.suffix == ".jsonl":
        # 名前を先に外してから読む。旧の版の書き手が後から追記すると旧の名前で作り直し、次の migrate で移る
        # （読んだ後に追記された行を消さない）。移し先が別のファイルシステムでもコピーと削除の間に行を落とさない
        held = _side(src)
        os.rename(src, held)
        _rewrite_by_time(dst, [(held, False)])
        moved.append(str(dst))
    elif not dst.exists():
        shutil.move(str(src), str(dst))
        moved.append(str(dst))
    else:
        kept.append(str(src))


def _resume_held(held: Path, dst: Path, pid: int, moved: list, kept: list) -> None:
    """止まった migrate の残り `held`（旧の台帳か、名前を外した移し先）を `dst` へ足す。
    名前を外したプロセスが生きていれば（動いている migrate）触らない。"""
    if pid != os.getpid() and _held_running(pid):
        kept.append(str(held))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    _rewrite_by_time(dst, [(held, True)])
    moved.append(str(dst))


def _resume_sides(new: Path, kind: str, moved: list, kept: list) -> None:
    """書き直しの途中で止まった移し先（名前を外した `dst`）を元の名前へ戻し、止まったプロセスの一時ファイルを消す
    （一時ファイルの行は、名前を外した台帳か移し先に必ずある）。"""
    found = [*new.glob(f"{kind}{_HELD}*"), *new.glob(f".{kind}{_TMP}*")] if new.is_dir() else []
    if (new / kind).is_dir():
        found += [*(new / kind).rglob(f"*{_HELD}*"), *(new / kind).rglob(f".*{_TMP}*")]
    for child in sorted(found):
        held = _held_of(child)
        if held is not None:
            _resume_held(child, child.with_name(held[0]), held[1], moved, kept)
            continue
        pid = child.name.rpartition(_TMP)[2]
        if pid.isdigit() and int(pid) != os.getpid() and not _held_running(int(pid)):
            child.unlink()


def _flock_free(lock: Path) -> Optional[int]:
    """耐久の記録のロック `lock` を待たずに取る（filelock と同じ `flock`）。取れれば開いた fd、持たれていれば None。
    ロックのファイルが無ければ -1（誰も持てない）。"""
    try:
        fd = os.open(lock, os.O_RDWR)
    except FileNotFoundError:
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


_DBOS_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".lock")  # 1 本の耐久の記録の組（`durable.purge_old` と同じ）


def _move_dbos(old: Path, new: Path, moved: list, kept: list) -> None:
    """旧の `dbos/` の記録を組（`<鍵>.sqlite` と `-wal`・`-shm`・`.lock`）ごと `new` へ移す。ロックを持ったまま移す。"""
    keys = sorted({f.name[: -len(sfx)] for f in old.iterdir() for sfx in _DBOS_SUFFIXES if f.name.endswith(sfx)})
    for key in keys:
        group = [old / f"{key}{sfx}" for sfx in _DBOS_SUFFIXES if (old / f"{key}{sfx}").exists()]
        name = str(old / f"{key}.sqlite") if (old / f"{key}.sqlite").exists() else str(group[0])
        if any((new / f.name).exists() for f in group):
            kept.append({"path": name, "reason": "移し先に同じ名前の記録がある"})
            continue
        fd = _flock_free(old / f"{key}.lock")
        if fd is None:
            kept.append({"path": name, "reason": "ロックが持たれている（動いている耐久ワークフロー）"})
            continue
        try:
            new.mkdir(parents=True, exist_ok=True)
            for f in sorted(group, key=lambda f: f.suffix == ".lock"):  # ロックのファイルは最後に移す
                shutil.move(str(f), str(new / f.name))
        finally:
            if fd >= 0:
                os.close(fd)
        moved.append(str(new / Path(name).name))


def _step_running(plan_state: Path) -> bool:
    """プランの状態 `plan_state` のステップの子（`step.pid` の `children`）が生きているか。"""
    try:
        data = json.loads((plan_state / "step.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    children = data.get("children") if isinstance(data, dict) else None
    return any(isinstance(c, dict) and isinstance(c.get("pid"), int) and _held_running(c["pid"]) for c in children or [])


_PLAN_PATHS = ("リポジトリ", "作業場所")  # プランの状態の持ち主を確かめるパス（`plan.json` の写し。この順に見る）


def _sv_owner(plan_state: Path) -> Optional[Path]:
    """プランの状態 `plan_state` の持ち主のチェックアウトの根（`<git の共通ディレクトリ>/ndf/state`）。
    `plan.json` のパスのうち、今ある作業ツリーの最初のもので決める。決まらなければ None。"""
    try:
        plan = json.loads((plan_state / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in _PLAN_PATHS:
        value = plan.get(key) if isinstance(plan, dict) else None
        if not isinstance(value, str) or not Path(value).is_absolute():
            continue
        try:
            got = find_git_dir(Path(value)) if Path(value).is_dir() else None
        except OSError:
            got = None
        if got is not None:
            return got[1] / "ndf" / "state"
    return None


def _move_sv(old: Path, new: Path, moved: list, kept: list) -> None:
    """旧の `sv/` のプランの状態をディレクトリごと、持ち主のチェックアウトの根（`_sv_owner`）の `sv/` へ移す。
    持ち主が決まらない（パスを持たない・作業ツリーが無くなった・git の外）ものは `new`（打った作業ツリーのチェックアウト）へ移す。"""
    for child in sorted(old.iterdir()):
        owner = _sv_owner(child)
        to = owner / "sv" if owner is not None else new
        if (to / child.name).exists() or (to / child.name).is_symlink():
            kept.append({"path": str(child), "reason": f"移し先 {to} に同じ名前のプランの状態がある"})
        elif _step_running(child):
            kept.append({"path": str(child), "reason": "ステップの子が生きている（動いているプラン）"})
        else:
            to.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(to / child.name))
            moved.append(str(to / child.name))


def _move_checkout(old: Path, cwd: Path) -> dict:
    """旧の根の `dbos/`・`sv/` を `cwd` の作業ツリーのチェックアウトの根へ移す。作業ツリーの外なら移さず理由を返す。"""
    out: dict = {"checkout": {"to": None, "reason": None}, "dbos": {"moved": [], "kept": []}, "sv": {"moved": [], "kept": []}}
    try:
        got = find_git_dir(cwd)
    except OSError:
        got = None
    if got is None:
        out["checkout"]["reason"] = f"{cwd} は git の作業ツリーでない。耐久の記録とプランの状態は移さない（主のリポジトリで打ち直す）"
        return out
    to = got[1] / "ndf" / "state"
    out["checkout"]["to"] = str(to)
    for kind, move in (("dbos", _move_dbos), ("sv", _move_sv)):
        if (old / kind).is_dir() and not (old / kind).is_symlink():
            move(old / kind, to / kind, out[kind]["moved"], out[kind]["kept"])
            try:
                (old / kind).rmdir()
            except OSError:
                pass  # 残したものがある
    return out


def migrate(env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> dict:
    """旧の根から移す。利用者の台帳（`USER_KINDS`）は利用者の根へ足し合わせ、チェックアウトの記録（`dbos/`・`sv/`）は
    `cwd`（省けば作業ディレクトリ）の作業ツリーのチェックアウトの根へ移す。

    結果は `{"status", "summary", "from", "to", "moved", "kept", "checkout", "dbos", "sv"}`。`moved`・`kept` は台帳のパス、
    `checkout` は `{"to", "reason"}`（作業ツリーの外なら `to` が None で `reason` に理由）、`dbos`・`sv` は
    `{"moved": [パス], "kept": [{"path", "reason"}]}`。
    status: `migrated`（移した）・`none`（移すものが無い・明示の根を使う）・`kept`（旧に残したものがある: 移し先に同じ名前がある・
    別の migrate が動いている・動いている記録）。止まった migrate の残りも次に打てば足す。何度打ってもよい。"""
    env = _state_env(env)
    old, new = legacy_home(env), _user_default(env)
    empty = {"moved": [], "kept": []}
    base = {"from": str(old), "to": str(new), "moved": [], "kept": [], "checkout": {"to": None, "reason": None}, "dbos": empty, "sv": empty}
    if explicit_home(env) is not None:
        return {**base, "status": "none", "summary": "NDF_STATE_HOME か XDG_STATE_HOME が指定されていて、既定の置き場を使わない"}
    moved: list = []
    kept: list = []
    for kind in USER_KINDS:
        if old.is_dir():
            for child in sorted(old.glob(f"{kind}{_HELD}*")):  # 根の直下の台帳（`ask.jsonl` など）の止まった移行の残り
                held = _held_of(child)
                if held is not None:
                    _resume_held(child, new / held[0], held[1], moved, kept)
        if (old / kind).exists():
            _merge_ledger(old / kind, new / kind, moved, kept)
        _resume_sides(new, kind, moved, kept)
    out = {**base, "moved": moved, "kept": kept, **_move_checkout(old, Path(cwd) if cwd is not None else Path.cwd())}
    n_moved = len(moved) + len(out["dbos"]["moved"]) + len(out["sv"]["moved"])
    n_kept = len(kept) + len(out["dbos"]["kept"]) + len(out["sv"]["kept"])
    tail = f"。{out['checkout']['reason']}" if out["checkout"]["reason"] else ""
    if n_kept:
        return {
            **out,
            "status": "kept",
            "summary": f"{n_moved} 件を移し、{n_kept} 件は旧に残した（移し先に同じ名前がある・動いている）{tail}",
        }
    if n_moved:
        return {
            **out,
            "status": "migrated",
            "summary": f"{n_moved} 件を移した（台帳 {len(moved)}・耐久の記録 {len(out['dbos']['moved'])}・プランの状態 {len(out['sv']['moved'])}）{tail}",
        }
    return {**out, "status": "none", "summary": f"{old} に移すものが無い{tail}"}
