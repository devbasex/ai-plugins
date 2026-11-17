"""worktree の運用の設定（`.ndf/worktree.json` と個人設定 `.ndf/worktree.local.json`）を読む。

設定はメインディレクトリから読む（どの worktree から打っても同じ設定になる）。保存先と名前は事実の表
（`ndf-files` の `worktree`・`worktree-local` と `locations` の `ndf`）から引き、写さない。

- 共有設定が無ければ `None`（worktree の運用を使わないリポジトリ）。読めない・バージョンが違う・旧いブランチ戦略のキーが残れば
  `NdfSettingsError`（旧いキーは位置と移行のコマンドを理由に出す。`core.branch_strategy_legacy`）
- 個人設定は `LOCAL_KEYS` の項目だけを重ねる（運用を決める項目は共有設定だけが持つ。リモート名 `remote` もここに入れず、
  `adapters/git.py` の `remote_of` が共有設定から読む）。壊れていても共有の
  運用を止めず、反映しなかった項目と理由を `local_ignored` に載せる

スプリントの起点と PR のマージ先は、worktree の設定ではなくブランチ戦略の設定（`.ndf/project.json` の `branch_strategy`）
から読む（`strategy`。`Strategy.sprint_start_ref`・`Strategy.pr_target`）。スプリントの開始時にはセクションの全体を
状態の保存先へスナップショットを書き（`write_start_snapshot`）、スプリントの中の部品はスナップショットを読む（`read_snapshot`）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.domains.worktree import atomic

# 読める設定のバージョン
VERSION = 1
# 個人設定から重ねる項目と、その値の型
# セクションの名前（依存の用意・ローカルの動作検証・テスト環境）
DEPS = "deps"
LOCALENV = "localenv"
TESTENV = "testenv"
LOCAL_KEYS: dict[str, type] = {"follow_branch": bool, LOCALENV: dict, TESTENV: dict}
# 個人設定で反映しない項目のうち、報告もしないもの
LOCAL_SILENT = ("version", "$schema")
# guard の許可パスの既定（設定に guard.allow_paths の配列が無いとき）。知識と設定の保存先
# git の無視の設定のファイルの名前
GITIGNORE = ".gitignore"
DEFAULT_ALLOW_PATHS = (
    "issues/",
    "docs/",
    ".claude/",
    ".codex/",
    ".kiro/",
    ".agents/",
    ".serena/",
    identity.FIXED_WORDS["ndf_settings_dir"] + "/",
    GITIGNORE,
)
# スプリントの開始時のスナップショットを置く状態のファイルの名前（状態の保存先の下）
SNAPSHOT_FILE = "branch-strategy.json"


class NdfSettingsError(Exception):
    """共有設定が読めない（JSON でない・オブジェクトでない・バージョンが違う・値の形が違う）。"""


def _file(row: str, main_dir: Path) -> Path:
    location = facts.value("ndf-files", row, "location")
    return main_dir / facts.value("locations", location, "path") / facts.value("ndf-files", row, "path")


def shared_file(main_dir: Path) -> Path:
    return _file("worktree", main_dir)


def local_file(main_dir: Path) -> Path:
    return _file("worktree-local", main_dir)


@dataclass(frozen=True)
class NdfSettings:
    """重ねた後の設定。`raw` は共有設定に個人設定の許した項目を重ねた値。"""

    raw: dict[str, Any]
    local_ignored: tuple[str, ...] = ()

    def section(self, name: str) -> Optional[dict[str, Any]]:
        """セクション（`deps`・`localenv`・`testenv` など）の値。無い・オブジェクトでなければ `None`。"""
        value = self.raw.get(name)
        return value if isinstance(value, dict) else None

    @property
    def allow_paths(self) -> tuple[str, ...]:
        """guard が案内を出さないパス。配列があればそれ（空の配列は何も許さない）、無ければ既定。"""
        guard = self.raw.get("guard")
        items = guard.get("allow_paths") if isinstance(guard, dict) else None
        if not isinstance(items, list):
            return DEFAULT_ALLOW_PATHS
        return tuple(x for x in items if isinstance(x, str) and x)

    @property
    def follow_branch(self) -> bool:
        return self.raw.get("follow_branch") is True


def read_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NdfSettingsError(f"{path} を読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise NdfSettingsError(f"{path} が JSON のオブジェクトでない")
    return data


def _local_overrides(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    """個人設定から重ねる項目と、反映しなかった項目（名前の順）。読めなければ全体を反映しない。"""
    if not path.exists():
        return {}, ()
    try:
        data = read_object(path)
    except NdfSettingsError as exc:
        return {}, (f"{path.name}: {exc}",)
    keep: dict[str, Any] = {}
    ignored: list[str] = []
    for key in sorted(data):
        want = LOCAL_KEYS.get(key)
        if want is not None and isinstance(data[key], want):
            keep[key] = data[key]
        elif key not in LOCAL_SILENT:
            ignored.append(key)
    return keep, tuple(ignored)


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """オブジェクトは深く併合し、ほかの値（配列を含む）は置き換える。"""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(main_dir: Path) -> Optional[NdfSettings]:
    """メインディレクトリの設定。共有設定が無ければ `None`。旧いキーが残れば既定へ倒さずに `NdfSettingsError`。"""
    shared = shared_file(main_dir)
    if not shared.exists():
        return None
    data = read_object(shared)
    found = legacy.find_legacy({"worktree": data}, legacy.load_moves())
    if found:
        raise NdfSettingsError(legacy.legacy_reason(found, "11"))
    if data.get("version") != VERSION:
        raise NdfSettingsError(f"{shared} の version が {VERSION} でない: {data.get('version')!r}")
    over, ignored = _local_overrides(local_file(main_dir))
    return NdfSettings(_merge(data, over), ignored)


def strategy(main_dir: Path) -> bs.Strategy:
    """ブランチ戦略の設定（メインディレクトリの `.ndf/project.json` の `branch_strategy`）。

    セクションが無い・スキーマに落ちる・旧い 4 か所にキーが残るときは `NdfSettingsError`（既定ブランチへ倒さない）。
    """
    try:
        return bs.load(main_dir)
    except bs.StrategyError as exc:
        raise NdfSettingsError(exc.reason) from None


def sprint_start(main_dir: Path) -> str:
    """スプリントの起点（ブランチの名前）（`Strategy.sprint_start_ref`）。"""
    return strategy(main_dir).sprint_start_ref()


def sprint_pr_target(main_dir: Path) -> str:
    """ベースブランチ（スプリントの PR のマージ先。`Strategy.pr_target("sprint")`）。"""
    return strategy(main_dir).pr_target("sprint")


def snapshot_file(state_dir: Path) -> Path:
    return Path(state_dir) / SNAPSHOT_FILE


def _checked_snapshot(path: Path) -> dict[str, Any]:
    snap = read_object(path)
    section = snap.get("section")
    failed = bs.validate(section)
    if not isinstance(section, dict) or failed:
        where = "、".join(f"{f['rule']} `{f['pointer'] or '/'}`" for f in failed)
        detail = f"（{where}）" if where else ""
        raise NdfSettingsError(f"{path} はスプリントの開始時のスナップショットの形でない{detail}。スプリントを始め直す")
    return snap


def read_snapshot(path: Path) -> bs.Strategy:
    """スナップショット（`write_start_snapshot` が書いたファイル）のセクションを、スキーマで検査して型にする。"""
    return bs.Strategy(_checked_snapshot(path)["section"])


def write_start_snapshot(main_dir: Path, state_dir: Path) -> dict[str, Any]:
    """スプリントの開始時にセクションの全体を `<state_dir>/branch-strategy.json` へスナップショットとして書く（#3672 の決定 9）。

    スナップショットが既にあれば書き換えない（2 回目以降の呼び出しは開始時の値を保つ）。戻り値はスナップショットのパス・書いたか・ハッシュ
    （`digest`）と、スナップショットと今のセクションの違うキーの位置（`changed`。I13）。セクションが読めなければ `NdfSettingsError`
    （スプリントを始めない）、書けなければ `OSError`。
    """
    now = strategy(main_dir)
    path = snapshot_file(state_dir)
    written = not path.exists()
    if written:
        snap = bs.snapshot(now, f"{bs.PROJECT} の {bs.SECTION}")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps(snap, ensure_ascii=False, indent=2) + "\n")
    else:
        snap = _checked_snapshot(path)
    return {"path": str(path), "written": written, "digest": snap.get("digest"), "changed": bs.snapshot_diff(snap, now)}
