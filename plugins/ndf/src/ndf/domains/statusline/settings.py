"""statusline の設定（`settings.json` の `statusLine`）とバックアップ・表示の本体を、排他の中で切り替える（#3569）。

旧いプラグインの `statusline-switch.sh` の振る舞いを移した。保存先は旧いと同じにする（旧いバージョンでバックアップした利用者が新しいバージョンで
`restore` できるため）。

- `settings.json` は Claude Code と同じく `CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）の下。symlink なら参照先へ書き、
  バックアップ（`.ndf-statusline-backup.json`）と排他（`.ndf-statusline.lock`）も参照先の隣に置く（#1576）
- 表示の本体は `~/.claude/ndf-statusline.sh`。`statusLine.command` が `~` 起点で指すため `CLAUDE_CONFIG_DIR` に依らない
- 排他は旧い `flock` コマンドと同じ種類の錠（`fcntl.flock`）で取り、旧い書き手と互いに待つ
- JSON は Python で読み書きし、`jq` を使わない
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

from ndf import identity
from ndf.domains.relay import signals

NDF_COMMAND = "bash ~/.claude/ndf-statusline.sh"
# NDF の表示を指す command の目印
NDF_MARK = "ndf-statusline.sh"
# 再描画の間隔（秒）。メインが待機中でもサブエージェントの使用量を追うには時間で描き直す必要がある
REFRESH_INTERVAL = 5
BACKUP_NAME = ".ndf-statusline-backup.json"
LOCK_NAME = ".ndf-statusline.lock"
SETTINGS_NAME = "settings.json"
BODY_NAME = "ndf-statusline.sh"
# 表示の本体（同じパッケージに置く。旧い scripts/statusline.sh と同じバイト列）
BODY_SOURCE = Path(__file__).resolve().parent / "statusline.sh"
# NDF が置いたコピーの目印と、目印の前の旧いコピーの名前と中身の目印
MANAGED_MARK = "ndf-statusline: managed"
LEGACY_COPY = "statusline-command.sh"
LEGACY_MARKS = ("[ctx:", "container_name")
# command の末尾の *.sh の語（パスに空白を含まない形）
SCRIPT_RE = re.compile(r"(?:^|\s)(\S+\.sh)(?:\s|$)")


class StatuslineError(Exception):
    """設定を読めない（`kind` が `invalid`）・書けない（`unknown`）・排他を待ちきれない（`store_in_use`）。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class Paths(NamedTuple):
    settings: Path
    backup: Path
    lock: Path
    body: Path


class Switched(NamedTuple):
    """行った操作（`action`）と、今の `statusLine`・バックアップの値。"""

    action: str
    message: str
    current: Optional[dict[str, Any]]
    backup: Optional[dict[str, Any]]


def paths(environ: Mapping[str, str], home: Optional[Path] = None) -> Paths:
    """`settings.json`（symlink なら参照先）・バックアップ・排他・表示の本体の保存先。壊れた symlink は `StatuslineError`。"""
    link = identity.claude_config_root(environ) / SETTINGS_NAME
    settings = link.resolve(strict=False) if link.is_symlink() else link
    if link.is_symlink() and not settings.parent.is_dir():
        raise StatuslineError("invalid", f"{link} の参照先の保存先が無い: {settings}")
    base = home if home is not None else Path.home()
    return Paths(settings, settings.parent / BACKUP_NAME, settings.parent / LOCK_NAME, base / ".claude" / BODY_NAME)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StatuslineError("invalid", f"{path} を読めない: {exc}") from exc
    if not isinstance(raw, dict):
        raise StatuslineError("invalid", f"{path} が object でない")
    return raw


def _read_settings(p: Paths) -> dict[str, Any]:
    """`settings.json` の中身。無ければ空の object。"""
    if not p.settings.exists():
        return {}
    return _read_json(p.settings)


def _write(path: Path, data: Mapping[str, Any]) -> None:
    """置き換える。今のファイルの権限を保ち、無ければ 0600（`env` などの秘密を持ちうるため広げない）。"""
    try:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
        signals.replace_text(path, json.dumps(dict(data), ensure_ascii=False, indent=2) + "\n", mode=mode)
    except OSError as exc:
        raise StatuslineError("unknown", f"{path} を書けない: {exc}") from exc


def _read_backup(p: Paths) -> Optional[dict[str, Any]]:
    return _read_json(p.backup) if p.backup.exists() else None


def _status_line(data: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    value = data["statusLine"] if "statusLine" in data else None
    return value if isinstance(value, dict) and value else None


def _command(line: Optional[Mapping[str, Any]]) -> str:
    value = line["command"] if line is not None and "command" in line else None
    return value if isinstance(value, str) else ""


def is_ndf(line: Optional[Mapping[str, Any]]) -> bool:
    return NDF_MARK in _command(line)


def _ndf_line() -> dict[str, Any]:
    return {"type": "command", "command": NDF_COMMAND, "refreshInterval": REFRESH_INTERVAL}


def _location_body(p: Paths) -> bool:
    """表示の本体を今のバージョンで置く。中身が同じなら置かない。置いたら真。"""
    text = BODY_SOURCE.read_text(encoding="utf-8")
    if p.body.is_file() and p.body.read_text(encoding="utf-8") == text:
        return False
    try:
        signals.replace_text(p.body, text, mode=0o755)
    except OSError as exc:
        raise StatuslineError("unknown", f"{p.body} を書けない: {exc}") from exc
    return True


def _script_path(line: Optional[Mapping[str, Any]], home: Path) -> Optional[Path]:
    """command の末尾の *.sh の語を実行するスクリプトのパスとみなし、先頭の `~` を展開する。"""
    found = SCRIPT_RE.findall(_command(line))
    if not found:
        return None
    word = found[-1]
    if word == "~":
        return home
    return home / word[2:] if word.startswith("~/") else Path(word)


def _managed_copy(path: Optional[Path]) -> bool:
    """NDF が置いたコピー（目印つき、または目印の前の旧いコピーの名前と中身）か。利用者の表示を書き換えないため厳しく見る。"""
    if path is None or not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # noqa: NDF401 ファイル: 読めないコピーは利用者のものとみなし、書き換えない
        return False
    if MANAGED_MARK in text:
        return True
    return path.name == LEGACY_COPY and all(mark in text for mark in LEGACY_MARKS)


def _with_ndf(data: dict[str, Any]) -> dict[str, Any]:
    return {**data, "statusLine": _ndf_line()}


def _keep_refresh(p: Paths, data: dict[str, Any], line: dict[str, Any]) -> None:
    """NDF の表示を使う設定へ、再描画の間隔が無ければ足す。利用者が決めた値は残す。"""
    if "refreshInterval" not in line:
        _write(p.settings, {**data, "statusLine": {**line, "refreshInterval": REFRESH_INTERVAL}})


def run_locked(p: Paths, run: Callable[[Paths], Switched], wait: float) -> Switched:
    """排他を `wait` 秒まで取りにいき、取れたら `run` を打つ。取れなければ `StatuslineError`（store_in_use）。"""
    p.lock.parent.mkdir(parents=True, exist_ok=True)
    with signals.held(p.lock, wait) as got:
        if not got:
            raise StatuslineError("store_in_use", f"排他 {p.lock} を待ちきれない（別のセッションか旧い statusline-switch.sh が持つ）")
        return run(p)


def ensure(p: Paths, home: Optional[Path] = None) -> Switched:
    """SessionStart 向け。`statusLine` が無ければ NDF の表示を設定する。NDF の表示なら本体だけを今のバージョンに置き直す。"""
    placed = _location_body(p)
    data = _read_settings(p)
    line = _status_line(data)
    if line is None:
        _write(p.settings, _with_ndf(data))
        return Switched("set", f"statusLine を NDF の表示に設定した（{p.settings}）", _ndf_line(), _read_backup(p))
    if is_ndf(line):
        _keep_refresh(p, data, line)
        action = "updated" if placed else "kept"
        return Switched(action, "NDF の表示を使っている。本体を今のバージョンに揃えた", line, _read_backup(p))
    if _managed_copy(_script_path(line, home if home is not None else Path.home())):
        return _migrate(p, data, line)
    return Switched("kept", "statusLine は利用者の設定のため触らない", line, _read_backup(p))


def _migrate(p: Paths, data: dict[str, Any], line: dict[str, Any]) -> Switched:
    """NDF が過去に置いたコピーを指す設定を、バックアップしてから正規の保存先の参照へ移す。"""
    if not p.backup.exists():
        _write(p.backup, line)
    _write(p.settings, _with_ndf(data))
    return Switched(
        "updated", f"NDF の旧いコピーを指す設定を正規の保存先へ移した（旧い設定は {p.backup} にバックアップした）", _ndf_line(), line
    )


def status(p: Paths) -> Switched:
    """今の `statusLine` とバックアップの有無を読む。何も書かない。"""
    line = _status_line(_read_settings(p))
    if line is None:
        message = "statusLine: 未設定（既定の表示）"
    elif is_ndf(line):
        message = f"statusLine: NDF の表示（{_command(line)}）"
    else:
        message = f"statusLine: 利用者の設定（{_command(line)}）"
    return Switched("status", message, line, _read_backup(p))


def set_ndf(p: Paths) -> Switched:
    """今の値をバックアップして NDF の表示へ切り替える。既に NDF の表示なら再描画の間隔だけを補う。"""
    data = _read_settings(p)
    _location_body(p)
    line = _status_line(data)
    if is_ndf(line) and line is not None:
        _keep_refresh(p, data, line)
        return Switched("kept", "既に NDF の表示が設定されている", line, _read_backup(p))
    # 上書きする前のバックアップ（settings.json を書けなければこの中身へ戻す。無かったなら消す）
    before = _backup_text(p) if line is not None else None
    if line is not None:
        _write(p.backup, line)
    try:
        _write(p.settings, _with_ndf(data))
    except StatuslineError:
        if line is not None:
            _undo_backup(p, before)
        raise
    return Switched("set", "NDF の表示に切り替えた", _ndf_line(), line)


def _backup_text(p: Paths) -> Optional[str]:
    """今のバックアップのバイト列のまま（壊れた JSON でも戻せるよう、読み解かない）。無ければ None。"""
    if not p.backup.exists():
        return None
    try:
        return p.backup.read_text(encoding="utf-8")
    except OSError as exc:
        raise StatuslineError("invalid", f"{p.backup} を読めない: {exc}") from exc


def _undo_backup(p: Paths, before: Optional[str]) -> None:
    """バックアップを操作の前の状態へ戻す。前にバックアップがあればその中身を書き戻し、無ければ消す。"""
    if before is None:
        p.backup.unlink(missing_ok=True)
        return
    try:
        signals.replace_text(p.backup, before, mode=stat.S_IMODE(p.backup.stat().st_mode))
    except OSError as exc:
        raise StatuslineError("unknown", f"{p.backup} を戻せない: {exc}") from exc


def restore(p: Paths) -> Switched:
    """バックアップから元の値へ戻し、バックアップを消す。バックアップが無く NDF の表示なら `statusLine` を消す。"""
    data = _read_settings(p)
    backup = _read_backup(p)
    if backup is not None:
        _write(p.settings, {**data, "statusLine": backup})
        p.backup.unlink(missing_ok=True)
        return Switched("restored", "バックアップから statusLine を戻した", backup, None)
    line = _status_line(data)
    if is_ndf(line):
        _write(p.settings, {k: v for k, v in data.items() if k != "statusLine"})
        return Switched("removed", "バックアップが無いため statusLine を消した（既定の表示に戻る）", None, None)
    return Switched("kept", "戻すものが無い（NDF の表示を使っておらず、バックアップも無い）", line, None)
