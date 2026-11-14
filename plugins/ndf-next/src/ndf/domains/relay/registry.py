"""ラッパーの置き場と、動いているラッパーの見分け（#2563・#3341 の 3341-2）。

ラッパーは `<状態の置き場>/relay/wrappers/<名前>/` に次を持つ。

| ファイル | 中身 |
| --- | --- |
| `relay.lock` | 動いている間ラッパーが排他で持つ。排他が取れなければ動いている（pid の生死では見ない） |
| `wrapper.json` | 名前・版・状態（`running`・`stalled`・`waiting`・`stopped`）・理由・区間・アカウント・今日起動し直した回数 |
| `log.jsonl` | 区間の切り替えと、切り替えない理由が変わったときの 1 行 |
| `next.json`・`question.json` | hook が書くシグナルファイル（`signals`） |

旧い NDF（10.x）のラッパーは `<旧い状態の根>/relay/<作業ディレクトリ>/relay.lock` を持つ。旧い状態の根は
`NDF_STATE_HOME` → `$XDG_STATE_HOME/ndf` → `~/.local/state/ndf` の順で決まる（旧い NDF と同じ規則）。
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf.domains.relay import hub_decide, signals

NAME_RE = signals.NAME_RE
LOCK_FILE = "relay.lock"
STATE_FILE = "wrapper.json"
LOG_FILE = "log.jsonl"


class RegistryError(ValueError):
    """ラッパーの名前の形が違う・状態のファイルが壊れている。"""


def wrappers_dir(state_dir: Path) -> Path:
    return state_dir / "relay" / "wrappers"


def wrapper_dir(state_dir: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise RegistryError(f"ラッパーの名前の形が違う: {name}")
    return wrappers_dir(state_dir) / name


def lock_busy(path: Path) -> bool:
    """`path` の排他を誰かが持っていれば真。ファイルが無ければ偽。"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 錠が無ければ動いていない
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # noqa: NDF401 ファイル: 排他が取れなければ動いている
        return True
    finally:
        os.close(fd)
    return False


def read_state(directory: Path) -> Optional[dict[str, Any]]:
    """`wrapper.json`。無ければ None、壊れていれば `RegistryError`。"""
    path = directory / STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:  # noqa: NDF401 ファイル: 起動の途中は状態がまだ無い
        return None
    except ValueError as exc:
        raise RegistryError(f"ラッパーの状態が壊れている（{path}）: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError(f"ラッパーの状態の形が違う（{path}）")
    return data


def write_state(directory: Path, data: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / STATE_FILE
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({**dict(data), "at": signals.now_iso()}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def append_log(directory: Path, row: Mapping[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": signals.now_iso(), **dict(row)}, ensure_ascii=False) + "\n")


def running(state_dir: Path) -> list[hub_decide.Running]:
    """この NDF のラッパーのうち動いているもの。"""
    root = wrappers_dir(state_dir)
    dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    out = []
    for d in dirs:
        if lock_busy(d / LOCK_FILE):
            state = read_state(d)
            version = state.get("version") if state else None
            out.append(hub_decide.Running(d.name, version if isinstance(version, str) else None))
    return out


def legacy_root(environ: Mapping[str, str]) -> Path:
    explicit = environ.get("NDF_STATE_HOME")
    if explicit:
        return Path(explicit) / "relay"
    xdg = environ.get("XDG_STATE_HOME")
    return (Path(xdg) / "ndf" if xdg else Path.home() / ".local" / "state" / "ndf") / "relay"


def legacy_running(environ: Mapping[str, str]) -> list[hub_decide.Running]:
    """旧い NDF（10.x）のラッパーのうち動いているもの。版は分からないので None。"""
    root = legacy_root(environ)
    dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return [hub_decide.Running(d.name, None) for d in dirs if lock_busy(d / LOCK_FILE)]


def all_running(state_dir: Path, environ: Mapping[str, str]) -> list[hub_decide.Running]:
    return running(state_dir) + legacy_running(environ)
