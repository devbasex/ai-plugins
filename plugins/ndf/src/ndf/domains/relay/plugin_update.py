"""中継のプラグインの更新: 導入元の選び方と更新の結果の読み取り（純粋な関数）と、錠の中で 2 つのコマンドを打つ手順（#3812）。

- 導入元は、有効な `ndf@` の導入のうち `installPath` がラッパーの動いている根と一致するちょうど 1 件である（I4）
- 更新はマーケットプレイス → NDF の順に `claude plugin` を打ち、結果を `read_update` の表で `updated`・`up_to_date`・`failed` に分ける
- 次の子が読む起動先（`Target`）は、更新の成否とは別に導入の一覧を読み直して決める（I8）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from ndf import identity
from ndf.adapters import claude
from ndf.adapters.claude import Ran
from ndf.domains.relay import signals
from ndf.domains.relay.hub_decide import _major

RESULTS = ("updated", "up_to_date", "skipped", "failed")
# 導入元が決まらない理由
LIST_UNREADABLE = "導入済みのプラグインの一覧を読めない"
NO_MATCH = "動いている導入先と一致する有効な NDF の導入が無い"
SHARED_PATH = "同じ導入先を複数のスコープが指す"
# 新しいバージョンへ上げたときの `updateOutcome`（2026-11-19 に Claude Code 2.1.328 で実測）
UPDATED_OUTCOME = "updated"
# 理由の 1 行の長さの上限（文字）
REASON_CHARS = 200
_PATH = re.compile(r"\S*/\S*")


class Origin(NamedTuple):
    """更新する導入（ID・マーケットプレイス・スコープ）。"""

    plugin_id: str
    marketplace: str
    scope: str


class Target(NamedTuple):
    """次の子が読む起動先。分からなければ両方 None。"""

    version: Optional[str]
    root: Optional[Path]


UNKNOWN = Target(None, None)


class Updated(NamedTuple):
    """更新の結果。`result` は `RESULTS` のどれか。`target` は一覧で確かめた起動先（I8）。"""

    result: str
    before: Optional[str]
    after: Optional[str]
    reason: str
    target: Target = UNKNOWN


def find_origin(listed: Optional[Sequence[Mapping[str, Any]]], root: Path) -> Origin | str:
    """導入元をちょうど 1 件選ぶ。選べなければ理由の文。"""
    if listed is None:
        return LIST_UNREADABLE
    mine = root.resolve()
    hits = [e for e in listed if _is_ndf(e) and Path(e["installPath"]).resolve() == mine]
    if not hits:
        return NO_MATCH
    if len(hits) > 1:
        return SHARED_PATH
    plugin_id = hits[0]["id"]
    return Origin(plugin_id, plugin_id.split("@", 1)[1], hits[0]["scope"])


def _is_ndf(entry: Mapping[str, Any]) -> bool:
    plugin_id = entry.get("id")
    return (
        entry.get("enabled") is True
        and isinstance(plugin_id, str)
        and plugin_id.startswith(f"{identity.FINAL_NAME}@")
        and isinstance(entry.get("scope"), str)
        and isinstance(entry.get("installPath"), str)
    )


def find_target(listed: Optional[Sequence[Mapping[str, Any]]], origin: Origin) -> Target:
    """一覧のうち導入元と同じ ID・スコープの 1 件の `version`・`installPath`。決まらなければ `UNKNOWN`。"""
    if listed is None:
        return UNKNOWN
    hits = [e for e in listed if e.get("id") == origin.plugin_id and e.get("scope") == origin.scope]
    if len(hits) != 1:
        return UNKNOWN
    hit = hits[0]
    version, path = hit.get("version"), hit.get("installPath")
    if not isinstance(version, str) or not isinstance(path, str):
        return UNKNOWN
    return Target(version, Path(path))


def _last_json(stdout: str) -> Optional[dict[str, Any]]:
    """標準出力のうち JSON のオブジェクトとして読める最後の行。"""
    for line in reversed(stdout.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:  # noqa: S112 外部コマンド: 人向けの行は読み飛ばし、JSON の行を探す
            continue
        if isinstance(data, dict):
            return data
    return None


def _brief(payload: Optional[Mapping[str, Any]], ran: Ran) -> str:
    """失敗の理由の 1 行（`failureCode` と `message` の 1 行目。パスは伏せる）。"""
    if ran.code is None:
        return ran.error
    if payload is None:
        return f"終了コード {ran.code}・JSON の行が無い"
    parts = [v.strip().splitlines()[0] for v in (payload.get("failureCode"), payload.get("message")) if isinstance(v, str) and v.strip()]
    text = "・".join(parts) if parts else f"outcome={payload.get('outcome')}"
    return _PATH.sub("<パス>", text)[:REASON_CHARS]


def _checked(label: str, ran: Ran) -> Updated | dict[str, Any]:
    """コマンド 1 つの結果。成功なら JSON の行、失敗なら `failed` の結果（表の 1・2 行）。"""
    payload = _last_json(ran.stdout) if ran.code is not None else None
    if ran.code == 0 and payload is not None and payload.get("outcome") == "ok":
        return payload
    return Updated("failed", None, None, f"{label}: {_brief(payload, ran)}")


def read_update(market: Ran, plugin: Optional[Ran]) -> Updated:
    """2 つのコマンドの結果から更新の結果を決める（詳細設計の「処理の手順」の表を上から当てる）。"""
    checked = _checked("マーケットプレイスを更新できない", market)
    if isinstance(checked, Updated):
        return checked
    if plugin is None:
        return Updated("failed", None, None, "NDF の更新を打っていない")
    payload = _checked("NDF を更新できない", plugin)
    if isinstance(payload, Updated):
        return payload
    outcome = payload.get("updateOutcome")
    before, after = _str_or_none(payload.get("oldVersion")), _str_or_none(payload.get("newVersion"))
    unreadable = Updated("failed", before, after, f"読めない結果: updateOutcome={outcome}")
    if before is None or after is None:
        return unreadable
    if outcome == "up_to_date" and before == after:
        return Updated("up_to_date", before, after, "新しいバージョンは無い")
    if outcome == UPDATED_OUTCOME and before != after:
        return Updated("updated", before, after, f"{before} から {after} へ上げた")
    return unreadable


def _str_or_none(value: Any) -> Optional[str]:
    """文字列ならそのまま、それ以外は None。"""
    return value if isinstance(value, str) else None


def major_changed(own: str, target: Target) -> bool:
    """起動先のメジャーバージョンがラッパーと違うか。起動先が不明なら偽（I8）。"""
    if target.version is None:
        return False
    return _major(target.version) != _major(own)


@dataclass
class Updater:
    """錠（`lock`）の中で 2 つのコマンドを打ち、起動先を一覧で確かめる手（`Settings.update`。`confirm` は `Settings.confirm`）。

    一覧（`listed`）と 2 つのコマンド（`run`）は、どれも同じ `cwd`・`env` で打つ。導入元は一度決めたら保持する。
    """

    root: Path
    cwd: Path
    env: Mapping[str, str]
    timeout: float
    lock: Path
    run: Callable[..., Ran] = claude.run_plugin
    listed: Callable[..., Optional[list[dict[str, Any]]]] = claude.list_plugins
    origin: Optional[Origin] = field(default=None, init=False)

    def __call__(self) -> Updated:
        try:
            self.lock.parent.mkdir(parents=True, exist_ok=True)
            with signals.held(self.lock, self.timeout) as got:
                if not got:
                    return Updated("failed", None, None, f"別のラッパーの更新を {self.timeout:g} 秒待っても終わらない", self.confirm())
                return self._update()
        except OSError as exc:  # 錠を作れなくても今のバージョンで続ける（I2）
            return Updated("failed", None, None, f"錠を取れない: {type(exc).__name__}", self.confirm())

    def _update(self) -> Updated:
        listed = self._list() if self.origin is None else None
        origin = self._origin_in(listed)
        if isinstance(origin, str):
            return Updated("failed" if listed is None else "skipped", None, None, origin)
        market = self.run(["marketplace", "update", origin.marketplace, "--json"], self.timeout, self.env, self.cwd)
        plugin = None
        if market.code == 0:
            plugin = self.run(["update", origin.plugin_id, "-y", "--json", "--scope", origin.scope], self.timeout, self.env, self.cwd)
        return read_update(market, plugin)._replace(target=self.confirm())

    def confirm(self) -> Target:
        """錠を取らず更新もせずに、一覧を 1 回読み直して起動先を返す。導入元が未定なら同じ一覧から先に決める。"""
        listed = self._list()
        origin = self._origin_in(listed)
        if isinstance(origin, str):
            return UNKNOWN
        return find_target(listed, origin)

    def _origin_in(self, listed: Optional[Sequence[Mapping[str, Any]]]) -> Origin | str:
        """導入元を返す。未定なら `listed` から決めて保持し、決められなければ理由の文字列を返す。"""
        if self.origin is None:
            found = find_origin(listed, self.root)
            if isinstance(found, str):
                return found
            self.origin = found
        return self.origin

    def _list(self) -> Optional[list[dict[str, Any]]]:
        return self.listed(cwd=self.cwd, env=self.env)
