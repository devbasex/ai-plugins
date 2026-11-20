"""自分のディレクトリ・プラグイン名・Skill の前置き・状態の保存先を求める（#1142 の決定 75・決定 79）。

プラグイン名は `plugin.json` の `name` だけから求め、コードは自分の名前を文文字列で持たない。
入れ替えで書き換えるのは `plugin.json` 2 本の `name` と `version` だけになる。

プラグインの根は、このファイルの位置（`ROOT`）の 1 通りで求める。コントラクトの表（`ndf.contracts`）も同じ `ROOT` を読む。
環境変数 `CLAUDE_PLUGIN_ROOT` は根を決めず、自分の根と同じかを確かめるだけに使う。違えば（ほかのプラグインの
hook の子として動いたなど）`PluginIdentity.foreign_root` に残し、旧い状態の保存先を退避しない（#3367）。
`bin/ndf` は子へ自分の根を渡す前に、呼び出し元の値を `NDF_CALLER_PLUGIN_ROOT` へ写す。それがあれば（空でも）
そちらを呼び出し元の値として確かめる。

状態の保存先の用意（`prepare_state_dir`）:

- 旧い NDF の状態の保存先の全一覧は `OLD_STATE_LOCATIONS` の表で、行ごとに退避するか・その理由を持つ。退避するのは
  新しい NDF が同じパスへ書き、状態を持つ 2 つ（状態のホームとチェックアウトの保存先）だけで、どちらも同じ規則で退避する。
  規則は「形のバージョンのファイル `format.json` が無く、ほかのファイルがある保存先は、名前を `retired_name` の
  `<元の名前>.v<旧い形のバージョン>-<UTC の日付>` へ変える」で、消さない。退避先が既にあれば何も変えずに止まる（理由
  `retire_target_exists`）。チェックアウトの保存先（`<git の共通ディレクトリ>/<名前>/state`）は `core/durable.py` の
  `prepare_store` が同じ規則で持つ
- 判定から `format.json` を置くまでを、保存先の隣のロックのファイル（`lock_path`）の排他の下で行う。同時に起動した
  処理は待ち、先の処理が置いた `format.json` を見て退避しない
- 名前を変える前に、旧い保存先の錠（`LEGACY_LOCKS`。旧いラッパーとリレーハブ）を待たずに排他で取りにいく
  （`hold_legacy_locks`）。1 つでも取れなければ何も変えずに止まる（理由 `legacy_running`）。すべて取れたら、名前の変更と
  新しい保存先の `format.json` の書き込みが終わるまで持ち続ける
- `format.json` は一時の名前で書いてから置き換える（`os.replace`）。`format.json` があることが「用意が済んだ」マーカーで、
  途中で落ちても中身の半端な `format.json` は残らない
- 退避する保存先が退避しない保存先（`Handling.KEEP` の行。アカウントの設定・旧い NDF のジャーナル・動いているプロセスの
  排他など）を中に持てば、外側ごと動かすとそれらまで動くため、何も変えずに `IdentityError` を投げて人へ戻す
- 戻り値の `StateReady` が、退避した保存先の元と先を持つ（結果 JSON の `items` へ載せる形は `StateReady.items`）。
  止まったときの `IdentityError` は、停止理由の表の語（`kind`）・停止理由の細目（`reason`）・関わったパス（`paths`）と、
  それまでに退避した元と先を持つ（同じ形は `IdentityError.items`）
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# プラグインの根（このファイルから 3 つ上）。根を求めるのはここだけ
ROOT = Path(__file__).resolve().parents[2]
# 入れ替え（S2）の後のプラグイン名（#3347 の決定 101）。検査の I1 は今の名前とこの名前の両方を検証する
FINAL_NAME = "ndf"
# worker（`ndf worker run`）が起動する子へ作業種別を渡す環境変数。hook の guard はこれで worker の中かを見分ける（#3112）
WORKER_KIND_ENV = "NDF_WORKER_KIND"
# プラグイン名から求めない固定の語の表（#3347 の決定 101）。I1 はこの表の定義の行だけを通し、コードはここから引く
FIXED_WORDS = {
    # 利用先のリポジトリの設定の保存先（`.ndf/project.json` など）
    "ndf_settings_dir": ".ndf",
    # エントリポイントのファイル名（`bin/ndf`）と、結果 JSON の next・routes のコマンドの先頭
    "entrypoint": "ndf",
    # 工程の定義の文書の名前空間（`metadata.ndf`・`document.namespace`）
    "workflow_namespace": "ndf",
}
# プラグインの根からの自分の定義（`plugin.json`）の保存先
MANIFEST_REL = Path(".claude-plugin") / "plugin.json"
# 状態の保存先の先頭に置く形のバージョンのファイル。無い保存先は旧い実装のものとみなす
FORMAT_FILE = "format.json"
FORMAT_VERSION = 1
# `format.json` の無い保存先（旧い NDF のもの）の形のバージョン。退避先の名前に入れる
LEGACY_FORM = 10


def _retired_prefix(form: int) -> str:
    """退避先の名前で、元の名前と日付の間に置く `.v<形のバージョン>-`。"""
    return f".v{form}-"


# 旧い保存先の退避先の名前の接尾辞（形は `retired_name`）
RETIRED_SUFFIX = _retired_prefix(LEGACY_FORM)
# 状態のホームを退避する前に取る、旧い NDF の錠（ラッパーとリレーハブ）。保存先からの glob の形
LEGACY_LOCKS = ("relay/*/relay.lock", "relay/hub/hub.lock")
# 退避した旧い保存先を標準エラーへ書くときの 1 行（エントリポイントと test-slot が使う。`items` の 1 件で埋める）
RETIRED_LINE = "旧い状態の保存先を退避した: {retired_from} -> {retired_to}"


class IdentityError(Exception):
    """`plugin.json` が読めず自分の名前を求められない、旧い保存先を退避してよいと確かめられない、または退避の途中で失敗した。

    `kind` は停止理由の表の語、`reason` は停止理由の細目（`legacy_running`・`retire_target_exists`。無ければ None）、
    `paths` はその理由に関わったパス（取れなかった錠・既にある退避先）。退避の途中で失敗したときは、それまでに
    退避した保存先を `retired` に持つ（復旧先を呼び出し元が引ける）。
    """

    def __init__(
        self,
        message: str,
        retired: tuple[Retired, ...] = (),
        kind: str = "config_broken",
        reason: Optional[str] = None,
        paths: tuple[Path, ...] = (),
    ):
        super().__init__(message)
        self.retired = retired
        self.kind = kind
        self.reason = reason
        self.paths = paths

    def items(self) -> list[dict[str, str]]:
        """結果 JSON の `items` へ載せる、失敗の前に退避した保存先の元と先と、停止理由に関わったパス。"""
        reasons = [{"reason": self.reason, "path": str(p)} for p in self.paths] if self.reason else []
        return _retired_items(self.retired) + reasons


@dataclass(frozen=True)
class PluginIdentity:
    root: Path
    name: str
    prefix: str
    state_dir: Path
    # `CLAUDE_PLUGIN_ROOT` が自分の根と違うときの、その値。同じか無ければ None
    foreign_root: Optional[Path] = None
    # `name` と同じ 1 回の読み取りで得た自分の定義の中身。バージョンなどを読み直さずに使う（#3909）
    manifest: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class Retired:
    """退避した旧い保存先の元と先。"""

    source: Path
    target: Path


@dataclass(frozen=True)
class StateReady:
    """用意した状態の保存先と、このときに退避した旧い保存先。"""

    path: Path
    retired: tuple[Retired, ...] = ()

    def items(self) -> list[dict[str, str]]:
        """結果 JSON の `items` へ載せる、退避した保存先の元と先。"""
        return _retired_items(self.retired)


def _retired_items(retired: tuple[Retired, ...]) -> list[dict[str, str]]:
    return [{"retired_from": str(r.source), "retired_to": str(r.target)} for r in retired]


def entrypoint(root: Path) -> Path:
    """プラグインの根 `root` の下のエントリポイント（`bin/ndf`）。"""
    return root / "bin" / FIXED_WORDS["entrypoint"]


def _env_dir(var: str, *default: str) -> Path:
    """環境変数 `var` が空でなければそのパス、空か無ければ HOME の下の `default`。"""
    value = os.environ.get(var, "")
    return Path(value) if value else Path.home().joinpath(*default)


def _state_home() -> Path:
    """状態の保存先の根（保存先は `<根>/<プラグイン名>`）。`NDF_STATE_HOME`・`XDG_STATE_HOME`・`~/.local/state` の順に引く。

    `NDF_STATE_HOME` も根として読み、プラグイン名を足す。サービスが旧い NDF と同じ値を渡しても、旧い保存先を退避しない。
    """
    for name in ("NDF_STATE_HOME", "XDG_STATE_HOME"):  # 先に引くほうが先
        value = os.environ.get(name, "")
        if value:
            return Path(value).expanduser()
    return Path.home() / ".local" / "state"


def legacy_state_root(environ: Mapping[str, str]) -> Path:
    """旧い NDF の状態の根（`NDF_STATE_HOME` → `XDG_STATE_HOME/ndf` → `~/.local/state/ndf`）。

    旧い NDF のプラグイン名は入れ替えの後の名前 `FINAL_NAME` と同じ（#1142 の決定 73）。
    """
    explicit = environ.get("NDF_STATE_HOME")
    if explicit:
        return Path(explicit)
    xdg = environ.get("XDG_STATE_HOME")
    return Path(xdg) / FINAL_NAME if xdg else Path.home() / ".local" / "state" / FINAL_NAME


def _config_home() -> Path:
    """Claude Code の設定の保存先（`CLAUDE_CONFIG_DIR`、無ければ `~/.claude`）。"""
    return _env_dir("CLAUDE_CONFIG_DIR", ".claude")


def _legacy_accounts_dir(ident: PluginIdentity) -> Path:
    """旧い NDF のアカウントの登録の保存先（旧い `claude_accounts.store_dir` と同じ規則）。

    `NDF_ACCOUNTS_DIR` → `<NDF_SHARED_CONFIG_DIR（空なら ~/.claude）か CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/accounts`。
    """
    forced = os.environ.get("NDF_ACCOUNTS_DIR")
    if forced:
        return Path(forced)
    if "NDF_SHARED_CONFIG_DIR" in os.environ:
        shared = os.environ["NDF_SHARED_CONFIG_DIR"]
        return (Path(shared) if shared else Path.home() / ".claude") / ident.name / "accounts"
    return _config_home() / ident.name / "accounts"


def _legacy_issue_lock_dir(ident: PluginIdentity) -> Path:
    """旧い NDF の Issue の本文を守る錠の保存先（旧い `gh_parts.issue_lock_target` と同じ規則）。`NDF_LOCK_DIR` → `~/.claude/<名前>/locks`。"""
    return Path(os.environ.get("NDF_LOCK_DIR") or Path.home() / ".claude" / ident.name / "locks")


def _foreign_root(root: Path) -> Optional[Path]:
    # `bin/ndf` が子へ自分の根を渡す前に写した呼び出し元の値（空でも）が、`CLAUDE_PLUGIN_ROOT` より先に効く
    env = os.environ
    names = [name for name in ("NDF_CALLER_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT") if name in env]
    if not names:
        return None
    env_root = env[names[0]]
    if not env_root or Path(env_root).resolve() == root.resolve():
        return None
    return Path(env_root)


def manifest_path(root: Path) -> Path:
    """プラグインの根 `root` の自分の定義（`MANIFEST_REL`）のパス。"""
    return root / MANIFEST_REL


def read_manifest(root: Path) -> Any:
    """プラグインの根 `root` の自分の定義（`MANIFEST_REL`）を JSON として読む。読めなければ OSError・ValueError をそのまま上げる。"""
    return json.loads(manifest_path(root).read_text(encoding="utf-8"))


def load() -> PluginIdentity:
    """自分の根（`ROOT`）の `plugin.json` を 1 回だけ読んで自分を求め、読んだ中身も持たせる（#3909）。"""
    root = ROOT
    manifest = manifest_path(root)
    try:
        data = read_manifest(root)
        name = data["name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityError(f"plugin.json を読めない: {manifest}: {exc}") from exc
    if not isinstance(name, str) or not name:
        raise IdentityError(f"plugin.json の name が文字列でない: {manifest}")
    return PluginIdentity(
        root=root,
        name=name,
        prefix=name + ":",
        state_dir=_state_home() / name,
        foreign_root=_foreign_root(root),
        manifest=data,
    )


def name_of(root: Path) -> Optional[str]:
    """任意のプラグインの根 `root` の `plugin.json` の名前。読めなければ None。"""
    return _manifest_value(root, "name")


def version_of(root: Path) -> Optional[str]:
    """任意のプラグインの根 `root` の `plugin.json` のバージョン。読めなければ None。"""
    return _manifest_value(root, "version")


def _manifest_value(root: Path, key: str) -> Optional[str]:
    try:
        data = read_manifest(root)
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めないプラグインの根は None で呼ぶ側へ返す
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, str) else None


class Handling(Enum):
    """旧い保存先の扱い。"""

    RETIRE_HERE = "retire_here"  # このモジュールで退避する
    RETIRE_ELSEWHERE = "retire_elsewhere"  # ほかのモジュールが同じ規則で退避する
    KEEP = "keep"  # 退避しない


@dataclass(frozen=True)
class OldPlace:
    """旧い NDF の状態の保存先の 1 行。`handling` が扱い、`reason` がその理由。

    `path` は保存先のパス。このモジュールで退避する行（`Handling.RETIRE_HERE`）と、退避しない行（`Handling.KEEP`）が
    持つ。退避しない行のパスは、退避する保存先の中に入っていないかを確かめるためだけに使い、触らない。
    ほかのモジュールが退避する行（`Handling.RETIRE_ELSEWHERE`）は None。
    """

    key: str
    where: str
    handling: Handling
    reason: str
    path: Optional[Callable[[PluginIdentity], Path]] = None


# 旧い NDF の状態の保存先の全一覧（#3341 の 3341-3）。SSOT はこの表だけで、文書へ写さない。
# 規則は「新しい NDF が同じパスへ書き、状態を持つ旧い保存先（状態のホームとチェックアウトの保存先）だけを退避する」。
# 退避は `prepare_state_dir` が上から順に行う
OLD_STATE_LOCATIONS: tuple[OldPlace, ...] = (
    OldPlace(
        key="state-home",
        where="<NDF_STATE_HOME か XDG_STATE_HOME か ~/.local/state>/<名前>",
        handling=Handling.RETIRE_HERE,
        reason="新しい状態の保存先と同じパスで、新しい NDF がそこへ書く。退避の後に作り直す",
        path=lambda ident: ident.state_dir,
    ),
    OldPlace(
        key="config-state",
        where="$HOME/.claude/<名前>/state",
        handling=Handling.KEEP,
        reason="旧い NDF の利用者のジャーナル。新しい NDF は読みも書きもせず、重ならない。共有の設定ディレクトリを使うほかのコンテナの"
        "旧い NDF が書き続けるため、名前を変えない",
        path=lambda ident: Path.home() / ".claude" / ident.name / "state",
    ),
    OldPlace(
        key="checkout-state",
        where="<git の共通ディレクトリ>/<名前>/state",
        handling=Handling.RETIRE_ELSEWHERE,
        reason="新しい NDF の耐久の記録が同じパスへ書く。退避は `core/durable.py` の `prepare_store` が同じ規則で行う（決定 85）",
    ),
    OldPlace(
        key="config-accounts",
        where="<NDF_ACCOUNTS_DIR か <NDF_SHARED_CONFIG_DIR か CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/accounts>",
        handling=Handling.KEEP,
        reason="利用者のアカウントの設定。新しい NDF は新しい状態の保存先の `relay/accounts` を使い重ならない。移行は S2 #3304",
        path=_legacy_accounts_dir,
    ),
    OldPlace(
        key="config-relay",
        where="<CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/relay.py",
        handling=Handling.KEEP,
        reason="中継の 1 つの複製で、新しい NDF の導入（`ndf relay install`）が上書きする",
        path=lambda ident: _config_home() / ident.name / "relay.py",
    ),
    OldPlace(
        key="config-locks",
        where="<CLAUDE_CONFIG_DIR か ~/.claude>/<名前>/locks",
        handling=Handling.KEEP,
        reason="動いているプロセスの排他。退避すると動いている旧い NDF の排他が外れる",
        path=lambda ident: _config_home() / ident.name / "locks",
    ),
    OldPlace(
        key="issue-locks",
        where="<NDF_LOCK_DIR か ~/.claude/<名前>/locks>",
        handling=Handling.KEEP,
        reason="動いている旧い NDF の Issue の本文を守る錠。退避すると使用中の錠が外れる",
        path=_legacy_issue_lock_dir,
    ),
    OldPlace(
        key="cache",
        where="~/.cache/<名前>",
        handling=Handling.KEEP,
        reason="新しい NDF が使わない、再生成できるキャッシュ",
        path=lambda ident: Path.home() / ".cache" / ident.name,
    ),
)


def old_state_locations(ident: PluginIdentity) -> list[Path]:
    """`OLD_STATE_LOCATIONS` のうち、このモジュールで退避する保存先のパス（表の順）。"""
    return _locations(ident, Handling.RETIRE_HERE)


def kept_locations(ident: PluginIdentity) -> list[Path]:
    """`OLD_STATE_LOCATIONS` のうち、退避しない保存先のパス（表の順）。"""
    return _locations(ident, Handling.KEEP)


def _locations(ident: PluginIdentity, handling: Handling) -> list[Path]:
    return [location.path(ident) for location in OLD_STATE_LOCATIONS if location.handling is handling and location.path is not None]


def retired_name(place: Path, form: Optional[int], now: datetime) -> Path:
    """退避先のパス `<元の名前>.v<形のバージョン>-<YYYYMMDD>`。`form` が None なら `LEGACY_FORM`。日付は UTC。退避先の有無を見ない。"""
    version = LEGACY_FORM if form is None else form
    return place.with_name(f"{place.name}{_retired_prefix(version)}{now.astimezone(UTC):%Y%m%d}")


def _try_exclusive(fd: int) -> bool:
    """`fd` の排他を待たずに取りにいく。ほかが持っていて取れなければ偽。"""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 排他が取れなければほかが持っている
        return False
    return True


def lock_held(path: Path) -> bool:
    """`path` の排他を誰かが持っていれば真。ファイルが無ければ偽で、ファイルを作らない。取れた排他はすぐ放す。"""
    try:
        fd = _open_existing(path)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 錠が無ければ動いていない
        return False
    try:
        return not _try_exclusive(fd)
    finally:
        os.close(fd)


@contextmanager
def hold_legacy_locks(place: Path, patterns: tuple[str, ...]) -> Iterator[list[Path]]:
    """`place` の下の `patterns` に当たる錠を待たずに排他で取りにいき、取れなかった錠のパスを渡す（名前順）。

    すべて取れたら空の一覧を渡し、`with` を抜けるまで錠を持ち続ける。1 つでも取れなければ、取れた錠をすぐ放してから
    渡す。錠のファイルを作らず、切り詰めない。開けなければ `IdentityError`（`config_broken`）。
    """
    paths = sorted({path for pattern in patterns for path in place.glob(pattern) if path.is_file()})
    held: list[int] = []
    busy: list[Path] = []
    try:
        for path in paths:
            fd = _open_lock(path)
            if fd is None:
                continue
            held.append(fd)
            if not _try_exclusive(fd):
                busy.append(path)
        if busy:
            _close_all(held)
        yield busy
    finally:
        _close_all(held)


def _open_existing(path: Path) -> int:
    """在る錠のファイルを、作らず切り詰めずに書ける形で開く。書けないファイルだけ読むだけで開く。

    Linux の NFS は `flock` を範囲の錠で真似るため、読むだけで開いたファイルに排他を取れない（EBADF）。"""
    try:
        return os.open(path, os.O_RDWR)
    except PermissionError:  # 書けない錠は読むだけで開く（ローカルの flock はこれで排他を取れる）
        return os.open(path, os.O_RDONLY)


def _open_lock(path: Path) -> Optional[int]:
    """在る錠のファイルを開く（`_open_existing`）。確かめる間に消えていれば None。開けなければ `IdentityError`（`config_broken`）。"""
    try:
        return _open_existing(path)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 確かめる間に消えた錠は持つ者が居ない
        return None
    except OSError as exc:
        raise IdentityError(f"旧い錠のファイルを開けない: {path}: {exc}", paths=(path,)) from exc


def _close_all(fds: list[int]) -> None:
    """開いた錠のファイルを閉じて排他を放し、一覧を空にする。"""
    while fds:
        os.close(fds.pop())


def lock_path(ident: PluginIdentity) -> Path:
    """状態の保存先を用意する処理どうしを排他するロックのファイル。退避で動かないよう保存先の隣に置く。"""
    return ident.state_dir.with_name(ident.state_dir.name + ".lock")


def prepare_state_dir(ident: PluginIdentity, now: Optional[datetime] = None) -> StateReady:
    """状態の保存先を用意し、用意した保存先と退避した旧い保存先を返す（手順はモジュールの説明）。

    退避が要るのに `CLAUDE_PLUGIN_ROOT` が自分の根と違う・旧い錠が 1 つでも取れない・退避先が既にある、のどれかなら
    何も変えずに `IdentityError` を投げる。`now` はタイムゾーンを持つ時刻（無ければ今の UTC）。
    """
    ident.state_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path(ident), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_locked(ident, now or datetime.now(UTC))


def format_tmp(state_dir: Path) -> Path:
    """`format.json` を書く途中の一時ファイル（`.format.json.<pid>.tmp`）。書き込みが中断されると残りうる。"""
    return state_dir / f".{FORMAT_FILE}.{os.getpid()}.tmp"


def holds_data(location: Path) -> bool:
    """保存先に `format.json` を書く途中の一時ファイル（`format_tmp`）のほかのものがあれば真。

    中断された書き込みの一時ファイルだけが残った保存先を、旧い利用者のデータとして退避し直さない。
    """
    return any(not _is_format_tmp(p.name) for p in location.iterdir())


def _is_format_tmp(name: str) -> bool:
    return name.startswith(f".{FORMAT_FILE}.") and name.endswith(".tmp")


def _is_old(location: Path) -> bool:
    return location.is_dir() and not (location / FORMAT_FILE).exists() and holds_data(location)


def _outermost(locations: list[Path]) -> list[Path]:
    """ほかの保存先の中にある保存先と、前に同じパスがある保存先を除く（外側を動かせば中も動く）。順は保つ。"""
    resolved = [p.resolve() for p in locations]
    return [p for i, (p, r) in enumerate(zip(locations, resolved)) if r not in resolved[:i] and not any(o in r.parents for o in resolved)]


def _prepare_locked(ident: PluginIdentity, now: datetime) -> StateReady:
    old = _outermost([p for p in old_state_locations(ident) if _is_old(p)])
    _refuse_unsafe_retire(ident, old)
    # 旧い錠の確かめから名前の変更と format.json の書き込みまで、旧い錠を持ち続ける（I3b）
    with ExitStack() as stack:
        busy = [path for place in old for path in stack.enter_context(hold_legacy_locks(place, LEGACY_LOCKS))]
        if busy:
            log.warning("旧いラッパーが動いているため、旧い状態の保存先を退避しない: %s", ", ".join(map(str, busy)))
            raise IdentityError(
                "旧い NDF のラッパーが動いているため、旧い状態の保存先を退避しない（止めてから打ち直す）: " + ", ".join(map(str, busy)),
                kind="store_in_use",
                reason="legacy_running",
                paths=tuple(busy),
            )
        retired = _retire_all(old, now)
        try:
            ident.state_dir.mkdir(parents=True, exist_ok=True)
            _write_format(ident.state_dir)
        except OSError as exc:
            raise IdentityError(f"旧い状態の保存先を退避した後で、新しい状態の保存先を用意できなかった: {exc}", retired) from exc
    return StateReady(ident.state_dir, retired)


def _refuse_unsafe_retire(ident: PluginIdentity, old: list[Path]) -> None:
    """退避してよいと確かめられなければ、何も変えずに `IdentityError` を投げる（ほかの根・退避しない保存先を含む）。"""
    if old and ident.foreign_root is not None:
        raise IdentityError(
            f"CLAUDE_PLUGIN_ROOT（{ident.foreign_root}）が自分の根（{ident.root}）と違うため、旧い状態の保存先を退避しない: "
            + ", ".join(str(p) for p in old)
        )
    refuse_holding_kept(ident, old)


def refuse_holding_kept(ident: PluginIdentity, old: list[Path]) -> None:
    """退避する保存先 `old` のどれかが退避しない保存先を中に持てば、何も変えずに `IdentityError` を投げる。

    `paths` は外側と中の保存先の組を並べたもの。状態のホームの退避と、チェックアウトの保存先の退避（`core/durable.py`）が呼ぶ。
    """
    holding = _holding_kept(ident, old)
    if holding:
        raise IdentityError(
            "退避する旧い状態の保存先が、退避しない保存先を中に持つため退避しない（外側ごと動かすとそれらも動く）: "
            + ", ".join(f"{outer} が {kept} を含む" for outer, kept in holding),
            paths=tuple(p for pair in holding for p in pair),
        )


def _holding_kept(ident: PluginIdentity, old: list[Path]) -> list[tuple[Path, Path]]:
    """退避する保存先と、その中にある（あれば）退避しない保存先の組。"""
    # 保存先そのものがリンクでも、置かれている場所で比べる（リンクの先へたどらない）
    kept = [(k, k.parent.resolve() / k.name) for k in kept_locations(ident) if os.path.lexists(k)]
    return [(outer, k) for outer in old for k, resolved in kept if outer.resolve() in resolved.parents]


def taken_targets(moves: list[Retired]) -> list[Path]:
    """退避先が既にある（リンクも含む）退避の、その退避先。並びは `moves` の順。"""
    return [move.target for move in moves if os.path.lexists(move.target)]


def _retire_all(old: list[Path], now: datetime) -> tuple[Retired, ...]:
    """旧い保存先を順に退避先の名前へ変え、退避した元と先を返す（旧い錠は呼び出し元が持っている）。

    名前を変える前にすべての退避先が無いことを確かめ、1 つでもあれば何も変えずに `IdentityError`
    （`retire_target_exists`）を投げる。途中で名前を変えられなければ、それまでに退避した元と先をログへ書き、
    `retired` に持つ `IdentityError` を投げる。
    """
    plan = [Retired(location, retired_name(location, None, now)) for location in old]
    taken = taken_targets(plan)
    if taken:
        log.warning("退避先が既にあるため、旧い状態の保存先を退避しない: %s", ", ".join(map(str, taken)))
        raise IdentityError(
            "退避先が既にあるため、旧い状態の保存先を退避しない（2 つの保存先を確かめてから打ち直す）: " + ", ".join(map(str, taken)),
            kind="precondition",
            reason="retire_target_exists",
            paths=tuple(taken),
        )
    retired: list[Retired] = []
    try:
        for move in plan:
            move.source.rename(move.target)
            retired.append(move)
    except OSError as exc:
        _log_retired(retired)
        raise IdentityError(
            f"旧い状態の保存先を退避する途中で名前を変えられなかった（退避した {len(retired)} 件）: {exc}", tuple(retired)
        ) from exc
    _log_retired(retired)
    return tuple(retired)


def _log_retired(retired: list[Retired]) -> None:
    if retired:
        log.warning("旧い状態の保存先を退避した: %s", ", ".join(f"{r.source} -> {r.target}" for r in retired))


def _write_format(state_dir: Path) -> None:
    """`format.json` が無ければ一時の名前で書いてから置き換える（途中で落ちても半端な中身を残さない）。"""
    fmt = state_dir / FORMAT_FILE
    if not fmt.exists():
        tmp = format_tmp(state_dir)
        tmp.write_text(json.dumps({"format": FORMAT_VERSION}) + "\n", encoding="utf-8")
        os.replace(tmp, fmt)
