"""ラッパーの保存先と、動いているラッパーの見分け（#2563・#3341 の 3341-2）。

ラッパーは `<状態の保存先>/relay/wrappers/<名前>/` に次を持つ。

| ファイル | 中身 |
| --- | --- |
| `relay.lock` | 動いている間ラッパーが排他で持つ。排他が取れなければ動いている（pid の生死では見ない） |
| `wrapper.json` | 名前・バージョン・状態（`running`・`stalled`・`waiting`・`stopped`）・理由・セグメント・アカウント |
| `log.jsonl` | セグメントの切り替えと、切り替えない理由が変わったときの 1 行 |
| `next.json`・`question.json` | hook が書くシグナルファイル（`signals`） |
| `again.json` | 再起動ループの検出で止めたときに書く、1 回だけの再開の値（セッション ID・引数・アカウント・作業ディレクトリ・役割・計画）。引数に秘密を含みうるので 0600 で書き、案内のコマンドには載せない（`--again` が読む） |

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
AGAIN_FILE = "again.json"
AGAIN_MODE = 0o600
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


def append_log(directory: Path, row: Mapping[str, Any]) -> None:
    signals.append_jsonl(directory / LOG_FILE, row)


def write_again(directory: Path, data: Mapping[str, Any]) -> None:
    """1 回だけの再開の値を 0600 で書く（引数に認証のヘッダーや非公開の本文を含みうる）。"""
    signals.replace_text(directory / AGAIN_FILE, json.dumps(dict(data), ensure_ascii=False), mode=AGAIN_MODE)


def read_again(directory: Path) -> dict[str, Any]:
    """`write_again` が書いた再開の値。無い・読めない・形が違えば `RegistryError`。"""
    path = directory / AGAIN_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RegistryError(f"再開の値が無い（{path}）。再起動ループの案内が出たラッパーの名前で打つ") from exc
    except ValueError as exc:
        raise RegistryError(f"再開の値を読めない（{path}）: {exc}") from exc
    ok = (
        isinstance(data, dict)
        and isinstance(data.get("session_id"), str)
        and isinstance(data.get("args"), list)
        and all(isinstance(a, str) for a in data["args"])
        and isinstance(data.get("cwd"), str)
        and all(data.get(k) is None or isinstance(data.get(k), str) for k in ("account", "role", "plan"))
    )
    if not ok:
        raise RegistryError(f"再開の値の形が違う（{path}）")
    return data


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
