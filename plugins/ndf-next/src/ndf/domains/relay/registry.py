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
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf.contracts import facts
from ndf.domains.relay import hub_decide, signals

NAME_RE = signals.NAME_RE
LOCK_FILE = "relay.lock"
STATE_FILE = "wrapper.json"
LOG_FILE = "log.jsonl"


class RegistryError(ValueError):
    """ラッパーの名前の形が違う・状態のファイルが壊れている。"""


@dataclass(frozen=True)
class Starts:
    """今日起動し直した回数。JSON では `{"date": ISO 日付, "count": 回数}` で持つ。"""

    date: str
    count: int

    @classmethod
    def today(cls, raw: Any) -> "Starts":
        """`raw` が今日の日付の回数ならその回数、形が違うか日付が今日でなければ 0 回。"""
        today = date.today().isoformat()
        if isinstance(raw, dict) and raw.get("date") == today:
            return cls(today, int(raw.get("count", 0)))  # noqa: NDF502 ファイル: 回数が無ければ 0 回
        return cls(today, 0)

    def bumped(self) -> "Starts":
        return Starts(self.date, self.count + 1)

    def as_json(self) -> dict[str, Any]:
        return {"date": self.date, "count": self.count}


def wrappers_dir(state_dir: Path) -> Path:
    return state_dir / facts.value("places", "relay-wrappers", "path")


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
    signals.replace_json(directory / STATE_FILE, {**dict(data), "at": signals.now_iso()})


def append_log(directory: Path, row: Mapping[str, Any]) -> None:
    signals.append_jsonl(directory / LOG_FILE, row)


def _busy_dirs(root: Path) -> list[Path]:
    """`root` の直下のディレクトリのうち、錠が持たれているもの（名前順）。`root` が無ければ空。"""
    dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return [d for d in dirs if lock_busy(d / LOCK_FILE)]


def running(state_dir: Path) -> list[hub_decide.Running]:
    """この NDF のラッパーのうち動いているもの。"""
    return [hub_decide.Running(d.name, _version(d)) for d in _busy_dirs(wrappers_dir(state_dir))]


def _version(directory: Path) -> Optional[str]:
    """ラッパーの状態に書かれた版。状態が無いか版が文字でなければ None。"""
    state = read_state(directory)
    version = state.get("version") if state else None
    return version if isinstance(version, str) else None


def legacy_root(environ: Mapping[str, str]) -> Path:
    explicit = environ.get("NDF_STATE_HOME")
    if explicit:
        return Path(explicit) / "relay"
    xdg = environ.get("XDG_STATE_HOME")
    return (Path(xdg) / "ndf" if xdg else Path.home() / ".local" / "state" / "ndf") / "relay"


def legacy_running(environ: Mapping[str, str]) -> list[hub_decide.Running]:
    """旧い NDF（10.x）のラッパーのうち動いているもの。版は分からないので None。"""
    return [hub_decide.Running(d.name, None) for d in _busy_dirs(legacy_root(environ))]


def all_running(state_dir: Path, environ: Mapping[str, str]) -> list[hub_decide.Running]:
    return running(state_dir) + legacy_running(environ)
