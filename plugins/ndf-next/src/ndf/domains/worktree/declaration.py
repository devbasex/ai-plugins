"""worktree の運用の宣言（`.ndf/worktree.json` と個人設定 `.ndf/worktree.local.json`）を読む。

宣言はメインディレクトリから読む（どの worktree から打っても同じ宣言になる）。置き場と名前は事実の表
（`ndf-files` の `worktree`・`worktree-local` と `places` の `ndf`）から引き、写さない。

- 共有設定が無ければ `None`（worktree の運用を使わないリポジトリ）。読めない・版が違う・旧いブランチ戦略のキーが残れば
  `DeclarationError`（旧いキーは位置と移行のコマンドを理由に出す。`core.branch_strategy_legacy`）
- 個人設定は `LOCAL_KEYS` の項目だけを重ねる（運用を決める項目は共有設定だけが持つ。リモート名 `remote` もここに入れず、
  `adapters/git.py` の `remote_of` が共有設定から読む）。壊れていても共有の
  運用を止めず、反映しなかった項目と理由を `local_ignored` に載せる

スプリントの起点と PR の宛先は、worktree の宣言ではなくブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）
から読む（`strategy`。`Strategy.sprint_start_ref`・`Strategy.pr_target`）。スプリントの開始時にはセクションの全体を
状態の置き場へ控え（`write_start_snapshot`）、スプリントの中の部品は控えを読む（`read_snapshot`）。
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

# 読める宣言の版
VERSION = 1
# 個人設定から重ねる項目と、その値の型
# 節の名前（依存の用意・ローカルの動作検証・テスト環境）
DEPS = "deps"
LOCALENV = "localenv"
TESTENV = "testenv"
LOCAL_KEYS: dict[str, type] = {"follow_branch": bool, LOCALENV: dict, TESTENV: dict}
# 個人設定で反映しない項目のうち、報告もしないもの
LOCAL_SILENT = ("version", "$schema")
# guard の許可パスの既定（宣言に guard.allow_paths の配列が無いとき）。知識と設定の置き場
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
    identity.FIXED_WORDS["declaration_dir"] + "/",
    GITIGNORE,
)
# スプリントの開始時のスナップショットを置く状態のファイルの名前（状態の置き場の下）
SNAPSHOT_FILE = "branch-strategy.json"


class DeclarationError(Exception):
    """共有設定が読めない（JSON でない・オブジェクトでない・版が違う・値の形が違う）。"""


def _file(row: str, main_dir: Path) -> Path:
    place = facts.value("ndf-files", row, "place")
    return main_dir / facts.value("places", place, "path") / facts.value("ndf-files", row, "path")


def shared_file(main_dir: Path) -> Path:
    return _file("worktree", main_dir)


def local_file(main_dir: Path) -> Path:
    return _file("worktree-local", main_dir)


@dataclass(frozen=True)
class Declaration:
    """重ねた後の宣言。`raw` は共有設定に個人設定の許した項目を重ねた値。"""

    raw: dict[str, Any]
    local_ignored: tuple[str, ...] = ()

    def section(self, name: str) -> Optional[dict[str, Any]]:
        """節（`deps`・`localenv`・`testenv` など）の値。無い・オブジェクトでなければ `None`。"""
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
        raise DeclarationError(f"{path} を読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise DeclarationError(f"{path} が JSON のオブジェクトでない")
    return data


def _local_overrides(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    """個人設定から重ねる項目と、反映しなかった項目（名前の順）。読めなければ全体を反映しない。"""
    if not path.exists():
        return {}, ()
    try:
        data = read_object(path)
    except DeclarationError as exc:
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


def load(main_dir: Path) -> Optional[Declaration]:
    """メインディレクトリの宣言。共有設定が無ければ `None`。"""
    shared = shared_file(main_dir)
    if not shared.exists():
        return None
    data = read_object(shared)
    found = legacy.find_legacy({"worktree": data}, legacy.load_moves())
    if found:
        raise DeclarationError(legacy.legacy_reason(found, "11"))
    if data.get("version") != VERSION:
        raise DeclarationError(f"{shared} の version が {VERSION} でない: {data.get('version')!r}")
    over, ignored = _local_overrides(local_file(main_dir))
    return Declaration(_merge(data, over), ignored)


def strategy(main_dir: Path) -> bs.Strategy:
    """ブランチ戦略の宣言（メインディレクトリの `.ndf/project.json` の `branch_strategy`）。

    セクションが無い・スキーマに落ちる・旧い 4 か所にキーが残るときは `DeclarationError`（既定ブランチへ倒さない）。
    """
    try:
        return bs.load(main_dir)
    except bs.StrategyError as exc:
        raise DeclarationError(exc.reason) from None


def sprint_start(main_dir: Path) -> str:
    """スプリントの起点（ブランチの名前）（`Strategy.sprint_start_ref`）。"""
    return strategy(main_dir).sprint_start_ref()


def base_branch(main_dir: Path) -> str:
    """ベースブランチ（スプリントの PR の宛先。`Strategy.pr_target("sprint")`）。"""
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
        raise DeclarationError(f"{path} はスプリントの開始時のスナップショットの形でない{detail}。スプリントを始め直す")
    return snap


def read_snapshot(path: Path) -> bs.Strategy:
    """控え（`write_start_snapshot` が書いたファイル）のセクションを、スキーマで検査して型にする。"""
    return bs.Strategy(_checked_snapshot(path)["section"])


def write_start_snapshot(main_dir: Path, state_dir: Path) -> dict[str, Any]:
    """スプリントの開始時にセクションの全体を `<state_dir>/branch-strategy.json` へ控える（#3672 の決定 9）。

    控えが既にあれば書き換えない（2 回目以降の呼び出しは開始時の値を保つ）。戻り値は控えのパス・書いたか・ハッシュ
    （`digest`）と、控えと今のセクションの違うキーの位置（`changed`。I13）。セクションが読めなければ `DeclarationError`
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
