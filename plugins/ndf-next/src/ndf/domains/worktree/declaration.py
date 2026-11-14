"""worktree の運用の宣言（`.ndf/worktree.json` と個人設定 `.ndf/worktree.local.json`）を読む。

宣言はメインディレクトリから読む（どの worktree から打っても同じ宣言になる）。置き場と名前は事実の表
（`ndf-files` の `worktree`・`worktree-local` と `places` の `ndf`）から引き、写さない。

- 共有設定が無ければ `None`（worktree の運用を使わないリポジトリ）。読めない・版が違えば `DeclarationError`
- 個人設定は `LOCAL_KEYS` の項目だけを重ねる（運用を決める項目は共有設定だけが持つ）。壊れていても共有の
  運用を止めず、反映しなかった項目と理由を `local_ignored` に載せる
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import facts

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
DEFAULT_ALLOW_PATHS = ("issues/", "docs/", ".claude/", ".codex/", ".kiro/", ".agents/", ".serena/", ".ndf/", GITIGNORE)
# branch_flow.start が取れる値（宣言の base_branch・production_branch のどちらを起点にするか）
START_CHANNELS = ("base", "production")


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

    def _text(self, key: str) -> Optional[str]:
        value = self.raw.get(key)
        return value if isinstance(value, str) and value else None

    def section(self, name: str) -> Optional[dict[str, Any]]:
        """節（`deps`・`localenv`・`testenv` など）の値。無い・オブジェクトでなければ `None`。"""
        value = self.raw.get(name)
        return value if isinstance(value, dict) else None

    @property
    def branch_flow(self) -> Optional[dict[str, Any]]:
        """取り込み方の節（`branch_flow`）。無い・オブジェクトでなければ `None`。"""
        return self.section("branch_flow")

    @property
    def base_branch(self) -> Optional[str]:
        return self._text("base_branch")

    @property
    def production_branch(self) -> Optional[str]:
        return self._text("production_branch")

    @property
    def start_channel(self) -> str:
        """スプリントの起点のチャネル（`branch_flow.start`。無ければ `base`）。"""
        flow = self.branch_flow
        start = flow.get("start") if flow is not None else None
        if start is None:
            return START_CHANNELS[0]
        if start not in START_CHANNELS:
            raise DeclarationError(f"branch_flow.start は {' か '.join(START_CHANNELS)} で書く: {start!r}")
        return str(start)

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
    if data.get("version") != VERSION:
        raise DeclarationError(f"{shared} の version が {VERSION} でない: {data.get('version')!r}")
    over, ignored = _local_overrides(local_file(main_dir))
    return Declaration(_merge(data, over), ignored)
