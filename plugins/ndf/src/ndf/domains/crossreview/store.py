"""状態ファイルと修正の結果ファイル（#2770）。

PR を巻き直す（閉じて新しい番号で出し直す）と、状態ファイルは元の番号のまま残る。番号の規則は 1 つにする:

- 状態ファイル・修正の結果ファイルの名前と、修正担当へ渡す指示の番号は、元の番号（`origin_pr`）にそろえる
- 状態ファイルは今の番号（`current_pr`）を持つ。今の番号を渡されたら、`current_pr` の当たる状態ファイルを引く
- 修正の結果は、元の番号と今の番号のどちらの名前・中の `pr` でも受ける（巻き直しの前に起動した修正担当が
  今の番号で書いても読み飛ばさない）

状態ファイルが壊れていれば `StateBroken` を投げる（既定の保存先や空の状態へ倒さない）。

保存先は非公開の PR の差分と状態を持つため、所有者だけが読み書きできるディレクトリにする。保存先の検査（`private_dir`）は
コマンドの入口で 1 回だけ行い、個々の読み書きは対象のエントリだけを確かめる（PR 3944 の r10-6）。保存先の
エントリのシンボリックリンクは拒む（リンク先の状態ファイルを実行者の状態として読み、`current_pr` を取得先や投稿先に
使わない。リンク先を書き換えない。PR 3944 の r9-1・r9-2）。書き込みは毎回、保存先に新しく作る所有者だけの一時ファイルへ
書いてから名前を置き換える（ハードリンクの先を切り詰めない・同時の書き込みが互いの一時ファイルを消さない。r10-3・r10-7）。
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple, Optional

STATE_NAME = "cross-review-pr{pr}-state.json"
STATE_GLOB = "cross-review-pr*-state.json"
FIX_RESULT_NAME = "fix-pr{pr}-result.json"
# 状態ファイルのキー
ORIGIN_KEY = "pr"
CURRENT_KEY = "current_pr"
REPO_KEY = "target_repo"
ROUNDS = "rounds"
REVIEWED = "reviewed_sections"
FINDINGS = "findings"


class StateBroken(Exception):
    """状態ファイルか結果ファイルが JSON の辞書として読めない。"""


class UnsafeStateDir(Exception):
    """状態の保存先が実行者のものでない・保存先のエントリにシンボリックリンクがある。"""


PRIVATE_MODE = 0o700
# 所有者のほかに書ける権限（グループ・その他の書き込み）
_SHARED_WRITE = 0o022
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _owned(st: os.stat_result, also_root: bool) -> bool:
    if not hasattr(os, "geteuid"):
        return True
    return st.st_uid == os.geteuid() or (also_root and st.st_uid == 0)


def _safe_parents(real: Path) -> None:
    """保存先の上位のディレクトリを、実行者か root のもので他のユーザーが書き換えられないものに限る（書ける上位からは保存先
    そのものを差し替えられる。スティッキービットの付いた /tmp のような共有の場所は、他のユーザーが実行者のエントリを
    差し替えられないので受ける。PR 3944 の r10-1）。"""
    for parent in real.parents:
        try:
            st = os.lstat(parent)
        except FileNotFoundError:  # noqa: NDF401 ファイル: まだ作っていない上位は差し替えられるものが無い
            continue
        _check_parent(real, parent, st)


def _check_parent(real: Path, parent: Path, st: os.stat_result) -> None:
    if not _owned(st, also_root=True) or (st.st_mode & _SHARED_WRITE and not st.st_mode & stat.S_ISVTX):
        raise UnsafeStateDir(
            f"状態の保存先 {real} の上位 {parent} を他のユーザーが書き換えられる（保存先を差し替えられる）。"
            "上位も実行者だけが書ける保存先を --state-dir に渡す"
        )


def _enter(fd: int, name: str) -> int:
    """`fd` の下の `name` を所有者だけの権限で作り（あれば作らない）、リンクをたどらずに開く。"""
    with contextlib.suppress(FileExistsError):  # noqa: NDF402 ファイル: 既にある。開いた後に所有者と権限を確かめる
        os.mkdir(name, PRIVATE_MODE, dir_fd=fd)
    return os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=fd)


def _make(real: Path) -> None:
    """検査を済ませた既存の上位の下へ、足りないディレクトリを上から 1 つずつ所有者だけの権限で作る（`mkdir(parents=True)`
    のように検査の前にまとめて作らない。PR 3944 の r11-4）。各階層はリンクをたどらずに開き、開いたディレクトリの所有者と
    権限を確かめてから、そのディレクトリを基準に次の階層を作る（事前の検査の後に中間がリンクへ差し替えられても、リンク先に
    作らない。r12-2）。中間がリンク・ディレクトリでなければ開けずに `OSError`（呼び手が `UnsafeStateDir` にする）。
    保存先そのものの種類と所有者は、呼び手のたどり先の再確認と `lstat` の検査が確かめる。"""
    here = Path(real.anchor)
    fd = os.open(here, os.O_RDONLY | _DIRECTORY)
    try:
        for name in real.parts[1:-1]:
            _check_parent(real, here, os.fstat(fd))
            nxt = _enter(fd, name)
            os.close(fd)
            fd, here = nxt, here / name
        _check_parent(real, here, os.fstat(fd))
        with contextlib.suppress(FileExistsError):  # noqa: NDF402 ファイル: 既にある。呼び手の検査が種類と所有者を確かめる
            os.mkdir(real.name, PRIVATE_MODE, dir_fd=fd)
    finally:
        os.close(fd)


def _entries(real: Path) -> list[os.DirEntry[str]]:
    with os.scandir(real) as found:
        return list(found)


def private_dir(state_dir: Path, write: bool = False, outside: Optional[Path] = None) -> Path:
    """保存先をコマンドの入口で 1 回だけ確かめ、たどった先の実体のパスを返す（以後はそのパスだけを使う）。
    `write` は書き込みを伴うコマンド: 無ければ所有者だけの保存先を作り、他のユーザーが読めるだけなら所有者だけへ絞る。
    読むだけのコマンドは権限を変えない（共有の保存先を参照しただけで他の利用者のアクセスを奪わない。PR 3944 の r10-5）。
    `outside` を渡すと、保存先がその worktree の外かを、作成・権限の変更・読み取りより前に、使うのと同じ実体のパスで確かめる
    （PR 3944 の r11-3）。
    次のどれかなら `UnsafeStateDir`: `outside` の中にある・上位を他のユーザーが書き換えられる（r10-1）・確かめている間に
    たどり先が変わった（r11-3）・ディレクトリでない（r10-4）・実行者のものでない・他のユーザーが書き込める（読むだけの
    コマンドも書き込みのコマンドも、空でも権限を変えずに拒む。r10-2・r11-1・r11-2・r12-1）・中にシンボリックリンクがある・
    検査の途中で OS のエラーが起きた（r11-5）。"""
    try:
        return _private_dir(state_dir, write, outside)
    except OSError as exc:
        raise UnsafeStateDir(f"状態の保存先 {state_dir} を確かめられない: {exc}。状態を置くディレクトリを --state-dir に渡す") from None


def _private_dir(state_dir: Path, write: bool, outside: Optional[Path]) -> Path:
    real = Path(os.path.realpath(state_dir))
    if outside is not None:
        tree = Path(os.path.realpath(outside))
        if real == tree or tree in real.parents:
            raise UnsafeStateDir(
                f"状態の保存先 {state_dir} がレビューする worktree {outside} の中にある。PR のチェックアウトが書き換えられる"
                "場所の状態を読み書きしないので、worktree の外の保存先を --state-dir に渡す"
            )
    # 作る前に既存の上位を確かめる（他のユーザーが書き換えられる上位の下に作らない。r11-4）
    _safe_parents(real)
    if write:
        _make(real)
        _safe_parents(real)
    # 上位を確かめた後のたどり先が、worktree の外と確かめたパスと同じか（間に差し替えられていない。r11-3）
    if Path(os.path.realpath(state_dir)) != real:
        raise UnsafeStateDir(
            f"状態の保存先 {state_dir} のたどり先が確かめている間に変わった。実行者だけが書ける保存先を --state-dir に渡す"
        )
    try:
        st = os.lstat(real)
    except FileNotFoundError:  # noqa: NDF401 ファイル: まだ作っていない保存先は読むものが無い
        return real
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafeStateDir(f"状態の保存先 {state_dir} がディレクトリでない。状態を置くディレクトリを --state-dir に渡す")
    if not _owned(st, also_root=False):
        raise UnsafeStateDir(f"状態の保存先 {state_dir} が実行者のものでない。実行者だけが読み書きできる保存先を --state-dir に渡す")
    shared = st.st_mode & _SHARED_WRITE
    untrusted = UnsafeStateDir(
        f"状態の保存先 {state_dir} は他のユーザーが書き込めるため、中の状態を信頼できない。実行者だけが読み書きできる"
        "新しい保存先を --state-dir に渡して再開する"
    )
    if shared:
        # 空でも書き込みのコマンドでも権限を変えずに拒む。絞ってから中を確かめる間に、別の実行が絞った保存先を専用と見て
        # 絞る前に置かれた状態を読む・中断した後の実行が同じ状態を信頼する（r11-1・r11-2・r12-1）
        raise untrusted
    entries = _entries(real)
    if write and st.st_mode & 0o077:
        # 他のユーザーは読めても書けなかった保存先だけを絞る（中の状態は実行者のもの）
        os.chmod(real, PRIVATE_MODE)
    links = sorted(e.name for e in entries if e.is_symlink())
    if links:
        raise UnsafeStateDir(
            f"状態の保存先 {state_dir} にシンボリックリンクがある（{', '.join(links)}）。リンクを読み書きしないので取り除く"
        )
    return real


def _no_link(path: Path) -> None:
    if path.is_symlink():
        raise UnsafeStateDir(f"{path} がシンボリックリンクである。リンク先を状態として読み書きしないので取り除く")


def is_entry(path: Path) -> bool:
    """保存先のファイルがあるか。シンボリックリンクなら `UnsafeStateDir`（リンク先を状態として扱わない）。"""
    _no_link(path)
    return path.is_file()


def _replace(path: Path, text: str) -> None:
    """保存先に新しく作る所有者だけの一時ファイル（書き込みごとに固有の名前）へ書いてから `path` へ置き換える。既存の
    ファイルを開いて切り詰めないので、ハードリンクの先（保存先の外の同じファイル）を書き換えない（PR 3944 の r10-3）。
    同時の書き込みは互いの一時ファイルに触れない（r10-7）。読む側に書きかけを見せない。"""
    _no_link(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_text(path: Path, text: str) -> None:
    """保存先のファイルへ書く。シンボリックリンクとハードリンクの先を書き換えない。"""
    _replace(path, text)


class FixResultMismatch(Exception):
    """修正の結果ファイルの `pr` が、元の番号とも今の番号とも合わない。"""


class StateRef(NamedTuple):
    """元の PR 番号・今の PR 番号・状態ファイルのパス（まだ無ければ元の番号の名前のパス）。"""

    origin_pr: int
    current_pr: int
    path: Path

    @property
    def numbers(self) -> frozenset[int]:
        return frozenset((self.origin_pr, self.current_pr))


def read_object(path: Path) -> dict[str, Any]:
    """JSON の辞書を読む。無い・壊れている・辞書でなければ `StateBroken`。"""
    _no_link(path)
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
        with open(fd, encoding="utf-8") as fh:
            found = json.loads(fh.read())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateBroken(f"{path} を読めない: {exc}") from None
    if not isinstance(found, dict):
        raise StateBroken(f"{path} が JSON の辞書でない")
    return found


def write_object(path: Path, value: dict[str, Any]) -> None:
    """JSON の辞書を書く（`_replace`。打ち直しても同じ中身）。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_MODE)
    _replace(path, json.dumps(value, ensure_ascii=False, sort_keys=True))


def _ref_of(path: Path, data: dict[str, Any], fallback: int) -> StateRef:
    origin = data.get(ORIGIN_KEY, fallback)  # noqa: NDF502 ファイル: 古い状態ファイルは番号のキーを持たない
    current = data.get(CURRENT_KEY, origin)  # noqa: NDF502 ファイル: 巻き直していなければ今の番号は元の番号
    if type(origin) is not int or type(current) is not int:
        raise StateBroken(f"{path} の {ORIGIN_KEY}・{CURRENT_KEY} が整数でない")
    return StateRef(origin, current, path)


def state_pr(state_dir: Path, pr: int) -> StateRef:
    """渡された番号の状態ファイルを引く。巻き直しの後の番号なら、`current_pr` の当たる元の番号の状態ファイル。
    保存先はコマンドの入口で確かめてあること（`private_dir`）。ここでは引くエントリだけを確かめる。"""
    own = state_dir / STATE_NAME.format(pr=pr)
    if is_entry(own):
        return _ref_of(own, read_object(own), pr)
    for path in sorted(state_dir.glob(STATE_GLOB)):
        ref = _ref_of(path, read_object(path), pr)
        if ref.current_pr == pr:
            return ref
    return StateRef(pr, pr, own)


def read_state(ref: StateRef) -> dict[str, Any]:
    """状態ファイルを読む。まだ無ければ元の番号だけを持つ空の状態。"""
    return read_object(ref.path) if is_entry(ref.path) else {ORIGIN_KEY: ref.origin_pr}


def list_of(data: Mapping[str, Any], key: str) -> list[Any]:
    """キーの値が並びならその並びそのもの、ほかは空の並び。"""
    value = data.get(key)
    return value if isinstance(value, list) else []


def last_round(data: Mapping[str, Any]) -> dict[str, Any]:
    """状態の最後のラウンドの記録（rounds が並びで末尾が辞書ならその辞書そのもの、ほかは空の辞書）。"""
    rounds = list_of(data, ROUNDS)
    return rounds[-1] if rounds and isinstance(rounds[-1], dict) else {}


def record_current(ref: StateRef, current_pr: int) -> StateRef:
    """巻き直しの後の今の番号を状態ファイルへ書く。"""
    data = read_state(ref)
    data[CURRENT_KEY] = current_pr
    write_object(ref.path, data)
    return StateRef(ref.origin_pr, current_pr, ref.path)


def state_repo(ref: StateRef) -> Optional[str]:
    """状態ファイルが持つ対象のリポジトリ。状態ファイルが無ければ None。"""
    if not is_entry(ref.path):
        return None
    value = read_object(ref.path).get(REPO_KEY)
    return value if isinstance(value, str) and value else None


def fix_result_path(state_dir: Path, ref: StateRef) -> Path:
    """修正担当へ渡す結果ファイルのパス（元の番号の名前）。"""
    return state_dir / FIX_RESULT_NAME.format(pr=ref.origin_pr)


def pending_fix_result(state_dir: Path, ref: StateRef) -> Optional[Path]:
    """残っている修正の結果ファイル。元の番号の名前を先に、無ければ今の番号の名前を探す。"""
    for pr in dict.fromkeys((ref.origin_pr, ref.current_pr)):
        path = state_dir / FIX_RESULT_NAME.format(pr=pr)
        if is_entry(path):
            return path
    return None


def read_fix_result(path: Path, ref: StateRef) -> dict[str, Any]:
    """修正の結果を読む。中の `pr` は元の番号と今の番号のどちらでも受け、ほかの番号なら `FixResultMismatch`。"""
    found = read_object(path)
    file_pr = found.get(ORIGIN_KEY)
    if file_pr not in ref.numbers:
        raise FixResultMismatch(f"{path} の pr {file_pr!r} は元の番号 {ref.origin_pr} とも今の番号 {ref.current_pr} とも合わない")
    return found
