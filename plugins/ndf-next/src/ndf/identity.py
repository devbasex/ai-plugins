"""自分のディレクトリ・プラグイン名・Skill の前置き・状態の置き場を求める（#1142 の決定 75・決定 79）。

プラグイン名は `plugin.json` の `name` だけから求め、コードは自分の名前を文字で持たない。
入れ替えで書き換えるのは `plugin.json` 2 本の `name` と `version` だけになる。
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# 状態の置き場の先頭に置く形の版のファイル。無い置き場は旧い実装のものとみなす
FORMAT_FILE = "format.json"
FORMAT_VERSION = 1
# 旧い置き場の退避先の名前の接尾辞（`<プラグイン名>.v10-<日付>`）
RETIRED_SUFFIX = ".v10-"
# 自分の根を指す環境変数
PLUGIN_ROOT_ENV = "CLAUDE_PLUGIN_ROOT"
# 状態の置き場の根を指す環境変数（先に引くほうが先）
STATE_HOME_ENV = "NDF_STATE_HOME"
XDG_STATE_ENV = "XDG_STATE_HOME"


class IdentityError(Exception):
    """`plugin.json` が読めず、自分の名前を求められない。"""


@dataclass(frozen=True)
class PluginIdentity:
    root: Path
    name: str
    prefix: str
    state_dir: Path


def _state_home() -> Path:
    """状態の置き場の根（置き場は `<根>/<プラグイン名>`）。`NDF_STATE_HOME`・`XDG_STATE_HOME`・`~/.local/state` の順に引く。

    `NDF_STATE_HOME` も根として読み、プラグイン名を足す。サービスが旧い NDF と同じ値を渡しても、旧い置き場を退避しない。
    """
    for name in (STATE_HOME_ENV, XDG_STATE_ENV):
        value = os.environ.get(name, "")
        if value:
            return Path(value).expanduser()
    return Path.home() / ".local" / "state"


def load() -> PluginIdentity:
    """`CLAUDE_PLUGIN_ROOT`（無ければこのファイルから 3 つ上）の `plugin.json` から自分を求める。"""
    env_root = os.environ.get(PLUGIN_ROOT_ENV, "")
    root = Path(env_root) if env_root else Path(__file__).resolve().parents[2]
    manifest = root / ".claude-plugin" / "plugin.json"
    try:
        name = json.loads(manifest.read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IdentityError(f"plugin.json を読めない: {manifest}: {exc}") from exc
    if not isinstance(name, str) or not name:
        raise IdentityError(f"plugin.json の name が文字列でない: {manifest}")
    return PluginIdentity(root=root, name=name, prefix=name + ":", state_dir=_state_home() / name)


def _retire_target(state_dir: Path, now: datetime) -> Path:
    """退避先のパス。日付の名前が既にあれば時刻を足し、それもあれば番号を足す。消さない。"""
    base = state_dir.with_name(state_dir.name + RETIRED_SUFFIX + now.strftime("%Y%m%d"))
    if not base.exists():
        return base
    stamped = base.with_name(base.name + now.strftime("T%H%M%S"))
    candidate, n = stamped, 1
    while candidate.exists():
        candidate = stamped.with_name(f"{stamped.name}-{n}")
        n += 1
    return candidate


def lock_path(ident: PluginIdentity) -> Path:
    """状態の置き場を用意する処理どうしを排他するロックのファイル。退避で動かないよう置き場の隣に置く。"""
    return ident.state_dir.with_name(ident.state_dir.name + ".lock")


def prepare_state_dir(ident: PluginIdentity, now: datetime | None = None) -> Path:
    """状態の置き場を用意して返す。

    形の版のファイルが無く、ほかのファイルがある置き場は旧い実装のものとして、名前を変えて退避してから作り直す。
    退避は名前の変更だけなので戻せる（決定 79・判断 (b)）。
    判定から形の版のファイルの作成までをロックで排他し、同時に起動した処理が書いたデータを切り離さない。
    """
    ident.state_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path(ident), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _prepare_locked(ident.state_dir, now)


def _prepare_locked(state_dir: Path, now: datetime | None) -> Path:
    if state_dir.is_dir() and not (state_dir / FORMAT_FILE).exists() and any(state_dir.iterdir()):
        target = _retire_target(state_dir, now or datetime.now())
        state_dir.rename(target)
        log.warning("旧い状態の置き場を退避した: %s -> %s", state_dir, target)
    state_dir.mkdir(parents=True, exist_ok=True)
    fmt = state_dir / FORMAT_FILE
    if not fmt.exists():
        fmt.write_text(json.dumps({"format": FORMAT_VERSION}) + "\n", encoding="utf-8")
    return state_dir
