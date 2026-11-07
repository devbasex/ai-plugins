"""NDF の状態の置き場（#2766）。リポジトリの外に書く状態を、持ち主ごとに 3 つの根へ分ける。

| 持ち主 | 根（既定） | 置くもの | 理由 |
| --- | --- | --- | --- |
| チェックアウト | `<git の共通ディレクトリ>/ndf/state`（`checkout_home`） | 耐久の記録 `dbos/`・プランの状態 `sv/` | 絶対パスから鍵を作る。永続で、チェックアウトごとに別になる（同じパスの別のチェックアウトと混ざらない） |
| 利用者 | `~/.claude/ndf/state`（`user_home`） | 追記だけの台帳（`ask.jsonl`・MVV の記録・`metrics/`・`usage/`・`checks/`・`stages/` など。`USER_KINDS`） | Claude Code のログインと同じ永続の木。複数のコンテナで共有して集計に使う |
| コンテナのプロセス | `~/.local/state/ndf`（`container_home`） | relay・待ちの通知・guard・context（pid とセッションを持つもの） | pid の名前空間はコンテナごとに別。プロセスはコンテナとともに消える |

`NDF_STATE_HOME` か `$XDG_STATE_HOME/ndf` を指定すると、3 つとも その 1 つの根になる（テストと明示の指定）。
各置き場の個別の上書き（`NDF_DBOS_DIR` など）は呼ぶ側が先に見る。`CLAUDE_CONFIG_DIR` は使わない（relay が
アカウントごとの子へ別の値を渡すため、置き場が割れる）。

旧の置き場（`~/.local/state/ndf`）は読まない。更新したら各コンテナで、動いているプランを止めてから、主のリポジトリの
作業ツリーから一度 `state-home.py migrate`（`migrate`）を打って旧から移す（2026-11-07 の決定。毎回旧を確かめない）:

- 利用者の根のロック（`LOCK`）で直列化する。取れなければ何も移さず止まる。旧の `dbos/` のロックが 1 つでも持たれていれば
  （動いている耐久ワークフロー）、台帳も含めて何も移さず止まる
- 利用者の台帳（`USER_KINDS`）を利用者の根へ足し合わせる。`.jsonl` は新に無い行を足し、時刻（`at`）の順に並べて書き直す
  （読み手は末尾を最新として読んでよい）。旧の台帳は消さず `<名前>.migrated` へ改名して残し、次の migrate が
  新に中身がすべて入ったものを消す
- チェックアウトの記録（`dbos/`・`sv/`）を、打った作業ツリーのチェックアウトの根へ移す（`state_checkout.migrate_all`）。
  実行時の探し方（作業ディレクトリの `checkout_home`）と同じ規則である。作業ツリーの外で打てば移さない。移し先に同じ名前が
  あるものは旧に残す。移行の前の queue は `--resume` で続けられない（耐久の記録の鍵と中の ID が旧の置き場のパスから
  できている）。新しく流す
- 何度打ってもよい（移すものが無ければ何もしない）

シェルの同じ順は `skills/development-workflow/scripts/lib/workflow-common.sh` の `ndf_user_state_home` にある。
"""

from __future__ import annotations

import collections
import datetime
import fcntl  # 耐久の記録のロック（filelock と同じ flock）を標準ライブラリだけで確かめる
import json
import os
import shutil
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

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
_SIDE = ".migrating-"  # migrate が名前を外した移し先の台帳の接尾辞（`<名前>.jsonl.migrating-<n>`）
_TMP = ".tmp"  # migrate が並べ直した台帳を書く一時ファイルの接尾辞（`.<名前>.jsonl.tmp`）
MIGRATED = ".migrated"  # 足し合わせた旧の台帳を旧の置き場に残す名前（`<名前>.jsonl.migrated`）
LOCK = ".migrate.lock"  # migrate を直列化するロック（利用者の根の下。共有する置き場の別のコンテナとも排他になる）
NOTE = "移行の前に動いているプランを止める。移行の前の queue は --resume で続けられない（新しく流す）"


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


def _read_lines(path: Path) -> list[bytes]:
    return _lines_of(path.read_bytes()) if path.exists() else []


def _missing(have: Sequence[bytes], lines: Sequence[bytes]) -> list[bytes]:
    """`lines` のうち `have` に無い行（同じ行は数で比べる。2 度足さない）。"""
    left = collections.Counter(have)
    out = []
    for line in lines:
        if left[line] > 0:
            left[line] -= 1
        else:
            out.append(line)
    return out


def _side(dst: Path) -> Path:
    """`dst` の名前を外した先（`<名前>.migrating-<n>`。既にある名前を上書きしない）。"""
    n = 0
    while dst.with_name(f"{dst.name}{_SIDE}{n}").exists():
        n += 1
    return dst.with_name(f"{dst.name}{_SIDE}{n}")


def _side_of(path: Path) -> Optional[str]:
    """名前を外した移し先の台帳（`<名前>.jsonl.migrating-<n>`）なら元の名前。違えば None。"""
    name, sep, n = path.name.rpartition(_SIDE)
    return name if sep and name.endswith(".jsonl") and n.isdigit() else None


def _mode_of(paths: Sequence[Path]) -> int:
    """台帳 `paths` の権限の厳しい方（ビットの積）。問いと答えを持つ `ask.jsonl` の 0600 を広げない。"""
    mode = 0o777
    for path in paths:
        mode &= path.stat().st_mode & 0o777
    return mode


def _merge_into(dst: Path, sources: Sequence[Path]) -> int:
    """`sources` の行のうち `dst` に無いもの（`_missing`）を `dst` へ足し、時刻の順に並べて書き直す。足した行の数を返す
    （無ければ書き直さない）。`sources` は消さない。

    `dst` は名前を外してから読み（`_side`）、並べたものを一時ファイルへ書いて `os.link` で元の名前へ置く。一時ファイルは
    作る時点から、移行元・移行先の台帳の厳しい方の権限にする（umask によらない）。間に別のプロセスが `dst` を作り直して
    追記していれば、その名前も外して末尾へ足してから置き直す。置いた後、名前を外した台帳に読んだ後で足された行を
    末尾へ足してから消す。途中で止まっても、次の migrate が `_resume_sides` で元の名前の台帳へ戻す。"""
    if not _missing(_read_lines(dst), [line for src in sources for line in _read_lines(src)]):
        return 0
    read: dict[Path, int] = {}

    def take() -> list[bytes]:
        side = _side(dst)
        os.rename(dst, side)
        data = side.read_bytes()
        read[side] = len(data)
        return _lines_of(data)

    current = take() if dst.exists() else []
    have = list(current)
    chunks = []
    for src in sources:
        rows = _missing(have, _read_lines(src))
        have += rows
        chunks.append(rows)
    tmp = dst.with_name(f".{dst.name}{_TMP}")  # `.jsonl` で終わらない（読み手が拾わない）
    tmp.unlink(missing_ok=True)
    mode = _mode_of([*read, *sources])
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.fchmod(fd, mode)  # umask で削られた分を戻す（広げるのは移行元・移行先の権限まで）
    with os.fdopen(fd, "wb") as f:
        f.write(b"".join(_by_time([current, *chunks])))
    while True:
        try:
            os.link(tmp, dst)
            break
        except FileExistsError:
            late = take()  # 書き直しの間に作り直された台帳。行は並べたものより新しい
            os.chmod(tmp, _mode_of([tmp, *read]))
            with tmp.open("ab") as f:
                f.write(b"".join(late))
    tmp.unlink()
    for side, size in read.items():
        data = side.read_bytes()
        if len(data) > size:  # 名前を外す前に開いた書き手が、外した後に足した行
            with dst.open("ab") as f:
                f.write(b"".join(_lines_of(data[size:])))
        side.unlink()
    return sum(len(rows) for rows in chunks)


def _merge_jsonl(src: Path, dst: Path, moved: list) -> None:
    """旧の台帳 `src` を `dst` へ足し合わせ、旧は `<名前>.migrated` へ改名して残す（既にあれば中身を後ろに足す）。
    旧の版の書き手が開いた fd へ後から足した行は `.migrated` に残り、次の migrate が足す。前の migrate が残した
    `.migrated` は、`dst` に中身がすべて入っていれば消す（この回に旧の台帳を足したものは、次の回まで残す）。"""
    mig = src.with_name(src.name + MIGRATED)
    earlier, appended = mig.exists(), src.exists()
    if appended and earlier:
        with mig.open("ab") as f:
            f.write(b"".join(_read_lines(src)))
        src.unlink()
    elif appended:
        os.rename(src, mig)
    if _merge_into(dst, [mig]) or appended:
        moved.append(str(dst))
    if earlier and not appended and not _missing(_read_lines(dst), _read_lines(mig)):
        mig.unlink()


def _merge_ledger(src: Path, dst: Path, moved: list, kept: list) -> None:
    """`src` を `dst` へ足し合わせる。`.jsonl` は `_merge_jsonl`、ほかは無ければ移し、同じ名前があれば旧に残す。"""
    if src.is_dir() and not src.is_symlink():
        names = {c.name[: -len(MIGRATED)] if c.name.endswith(".jsonl" + MIGRATED) else c.name for c in src.iterdir()}
        for name in sorted(names):
            _merge_ledger(src / name, dst / name, moved, kept)
        try:
            src.rmdir()
        except OSError:
            pass  # 残したもの（`.migrated` を含む）がある
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.name.endswith(".jsonl"):
        _merge_jsonl(src, dst, moved)
    elif not src.exists():
        return
    elif not dst.exists():
        shutil.move(str(src), str(dst))
        moved.append(str(dst))
    else:
        kept.append(str(src))


def _resume_sides(new: Path, kind: str, moved: list) -> None:
    """止まった migrate の残り（名前を外した移し先・一時ファイル）を片付ける。migrate のロック（`LOCK`）を持つ間だけ
    呼ぶので、残りはどれも止まった migrate のもの。名前を外した移し先は元の名前の台帳へ足して消し、一時ファイルは消す
    （一時ファイルの行は、名前を外した台帳か移し先に必ずある）。"""
    found = [*new.glob(f"{kind}{_SIDE}*"), *new.glob(f".{kind}{_TMP}")] if new.is_dir() else []
    if (new / kind).is_dir():
        found += [*(new / kind).rglob(f"*{_SIDE}*"), *(new / kind).rglob(f".*{_TMP}")]
    for child in sorted(found):
        name = _side_of(child)
        if name is not None:
            _merge_into(child.with_name(name), [child])
            child.unlink()
            moved.append(str(child.with_name(name)))
        elif child.name.endswith(_TMP):
            child.unlink()


def _flock_free(lock: Path, create: bool = False) -> Optional[int]:
    """ロック `lock` を待たずに取る（耐久の記録の filelock と同じ `flock`）。取れれば開いた fd、持たれていれば None。
    ロックのファイルが無ければ -1（誰も持てない）。`create` なら無いときに作って取る。"""
    try:
        fd = os.open(lock, os.O_RDWR | (os.O_CREAT if create else 0), 0o644)
    except FileNotFoundError:
        return -1
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _held_locks(dbos: Path) -> list[str]:
    """旧の `dbos/` の耐久の記録のロックのうち、持たれているもの（動いている耐久ワークフロー）。"""
    held = []
    for lock in sorted(dbos.glob("*.lock")) if dbos.is_dir() else []:
        fd = _flock_free(lock)
        if fd is None:
            held.append(str(lock))
        elif fd >= 0:
            os.close(fd)
    return held


def migrate(
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[Path] = None,
    move_checkout: Optional[Callable[[Path, Path], dict]] = None,
) -> dict:
    """旧の根から移す。利用者の台帳（`USER_KINDS`）は利用者の根へ足し合わせ、チェックアウトの記録（`dbos/`・`sv/`）は
    `cwd`（省けば作業ディレクトリ）の作業ツリーのチェックアウトの根へ `move_checkout` で移す（`state_checkout.migrate_all` が
    `state_checkout.move_checkout` を渡す。省けば台帳だけを移す）。

    利用者の根のロック（`LOCK`）を待たずに取り、取れなければ（別の migrate が動いている）何も移さず `busy` で止まる。
    旧の `dbos/` のロックが 1 つでも持たれていれば（動いている耐久ワークフロー）、台帳も含めて何も移さず `busy` で止まる。

    結果は `{"status", "summary", "note", "from", "to", "held", "moved", "kept", "checkout", "dbos", "sv"}`。`held` は止まった
    理由のロックのパス、`moved`・`kept` は台帳のパス、`checkout` は `{"to", "reason"}`（作業ツリーの外なら `to` が None で
    `reason` に理由）、`dbos`・`sv` は `{"moved": [パス], "kept": [{"path", "reason"}]}`。
    status: `migrated`（移した）・`none`（移すものが無い・明示の根を使う）・`kept`（移し先に同じ名前があって旧に残したものがある）・
    `busy`（上の 2 つの前提で止まった）。何度打ってもよい。"""
    env = _state_env(env)
    old, new = legacy_home(env), _user_default(env)
    base = {
        "from": str(old),
        "to": str(new),
        "note": NOTE,
        "held": [],
        "moved": [],
        "kept": [],
        "checkout": {"to": None, "reason": None},
        "dbos": {"moved": [], "kept": []},
        "sv": {"moved": [], "kept": []},
    }
    if explicit_home(env) is not None:
        return {**base, "status": "none", "summary": "NDF_STATE_HOME か XDG_STATE_HOME が指定されていて、既定の置き場を使わない"}
    new.mkdir(parents=True, exist_ok=True)
    lock = _flock_free(new / LOCK, create=True)
    if lock is None:
        return {
            **base,
            "status": "busy",
            "held": [str(new / LOCK)],
            "summary": "別の migrate が動いている。何も移さない（終わってから打つ）",
        }
    try:
        held = _held_locks(old / "dbos")
        if held:
            return {
                **base,
                "status": "busy",
                "held": held,
                "summary": f"動いている耐久ワークフローのロックが {len(held)} 件ある。何も移さない（{NOTE}）",
            }
        return _migrate_locked(old, new, base, cwd, move_checkout)
    finally:
        os.close(lock)


def _migrate_locked(old: Path, new: Path, base: dict, cwd: Optional[Path], move_checkout: Optional[Callable[[Path, Path], dict]]) -> dict:
    moved: list = []
    kept: list = []
    for kind in USER_KINDS:
        _resume_sides(new, kind, moved)
        if (old / kind).exists() or (old / f"{kind}{MIGRATED}").exists():
            _merge_ledger(old / kind, new / kind, moved, kept)
    out = {**base, "moved": list(dict.fromkeys(moved)), "kept": kept}
    if move_checkout is not None:  # relay が写すこのファイルは標準ライブラリだけを読むため、移す処理は呼ぶ側が渡す
        out.update(move_checkout(old, Path(cwd) if cwd is not None else Path.cwd()))
    n_moved = len(out["moved"]) + len(out["dbos"]["moved"]) + len(out["sv"]["moved"])
    n_kept = len(kept) + len(out["dbos"]["kept"]) + len(out["sv"]["kept"])
    tail = f"。{out['checkout']['reason']}" if out["checkout"]["reason"] else ""
    if n_kept:
        return {
            **out,
            "status": "kept",
            "summary": f"{n_moved} 件を移し、{n_kept} 件は旧に残した（移し先に同じ名前がある）{tail}",
        }
    if n_moved:
        return {
            **out,
            "status": "migrated",
            "summary": f"{n_moved} 件を移した（台帳 {len(out['moved'])}・耐久の記録 {len(out['dbos']['moved'])}・プランの状態 {len(out['sv']['moved'])}）{tail}",
        }
    return {**out, "status": "none", "summary": f"{old} に移すものが無い{tail}"}
