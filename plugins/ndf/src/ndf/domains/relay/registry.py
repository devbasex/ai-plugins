"""ラッパーの保存先と、動いているラッパーの見分け（#2563・#3341 の 3341-2）。

ラッパーは `<状態の保存先>/relay/wrappers/<名前>/` に次を持つ。

| ファイル | 中身 |
| --- | --- |
| `relay.lock` | 動いている間ラッパーが排他で持つ。排他が取れなければ動いている（pid の生死では見ない） |
| `wrapper.json` | 名前・バージョン・状態（`running`・`stalled`・`waiting`・`stopped`）・理由・セグメント・アカウント |
| `log.jsonl` | セグメントの切り替えと、切り替えない理由が変わったときの 1 行 |
| `next.json`・`question.json` | hook が書くシグナルファイル（`signals`） |
| `activity.json` | Stop の hook が書く、最後の応答の時刻・セッション・背景の作業の一覧（`id`・`type`。観測できなければ `null`。`signals`） |
| `shells-ended.json` | `ndf relay ls --activity` が書く、出力のファイルを読めない背景のシェルの終わりを最初に確かめた時刻（`observe`） |

旧い NDF（10.x）のラッパーは `<旧い状態の根>/relay/<作業ディレクトリ>/relay.lock` を持つ。旧い状態の根は
`NDF_STATE_HOME` → `$XDG_STATE_HOME/ndf` → `~/.local/state/ndf` の順で決まる（旧い NDF と同じ規則）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.domains.relay import hub_decide, signals

NAME_RE = signals.NAME_RE
LOCK_FILE = "relay.lock"
STATE_FILE = "wrapper.json"
LOG_FILE = "log.jsonl"
# リレーハブの保存先（`hub.hub_dir`）の下の登録簿
REGISTRY_FILE = "registry.json"


class RegistryError(ValueError):
    """ラッパーの名前の形が違う・状態のファイルが壊れている。"""


def wrappers_dir(state_dir: Path) -> Path:
    return state_dir / facts.value("locations", "relay-wrappers", "path")


def wrapper_dir(state_dir: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise RegistryError(f"ラッパーの名前の形が違う: {name}")
    return wrappers_dir(state_dir) / name


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


def append_log(directory: Path, row: Mapping[str, Any]) -> bool:
    """`log.jsonl` に 1 行足す。書けなければ偽（診断の記録なので、容量不足などで書けなくても呼び手の切り替えを止めない）。"""
    try:
        signals.append_jsonl(directory / LOG_FILE, row)
    except OSError:  # noqa: NDF401 ファイル: 診断の記録が書けなくても、替え先の起動や判断を続ける
        return False
    return True


def read_registry(hub: Path) -> dict[str, dict[str, Any]]:
    """リレーハブの保存先 `hub` の登録簿の行。無ければ空、読めない・形が違えば `RegistryError`。"""
    path = hub / REGISTRY_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RegistryError(f"登録簿を読めない（{path}）: {exc}") from exc
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise RegistryError(f"登録簿の形が違う（{path}）")
    return entries


def save_registry(hub: Path, entries: Mapping[str, Any]) -> None:
    signals.replace_json(hub / REGISTRY_FILE, {"version": 1, "entries": dict(entries)})


def _busy_dirs(root: Path) -> list[Path]:
    """`root` の直下のディレクトリのうち、錠が持たれているもの（名前順）。`root` が無ければ空。"""
    dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return [d for d in dirs if identity.lock_held(d / LOCK_FILE)]


def busy_names(state_dir: Path) -> list[str]:
    """この NDF のラッパーのうち錠を持つものの名前（状態のファイルを読まない。一覧が行ごとに状態を 1 回だけ読む）。"""
    return [d.name for d in _busy_dirs(wrappers_dir(state_dir))]


def running(state_dir: Path) -> list[hub_decide.Running]:
    """この NDF のラッパーのうち動いているもの。"""
    return [hub_decide.Running(d.name, _version(d)) for d in _busy_dirs(wrappers_dir(state_dir))]


def _version(directory: Path) -> Optional[str]:
    """ラッパーの状態に書かれたバージョン。状態が無いかバージョンが文字でなければ None。"""
    state = read_state(directory)
    version = state.get("version") if state else None
    return version if isinstance(version, str) else None


def legacy_root(environ: Mapping[str, str]) -> Path:
    return identity.legacy_state_root(environ) / "relay"


def legacy_running(environ: Mapping[str, str]) -> list[hub_decide.Running]:
    """旧い NDF（10.x）のラッパーのうち動いているもの。バージョンは分からないので None。"""
    return [hub_decide.Running(d.name, None) for d in _busy_dirs(legacy_root(environ))]


def all_running(state_dir: Path, environ: Mapping[str, str]) -> list[hub_decide.Running]:
    return running(state_dir) + legacy_running(environ)
