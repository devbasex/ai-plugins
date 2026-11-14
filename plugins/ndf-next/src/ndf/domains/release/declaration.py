"""リリースの宣言（`.ndf/release.json`）を読み、宣言の手順を流す。

置き場と名前は事実の表（`ndf-files` の `release` と `places` の `ndf`）から引き、写さない。宣言が無ければ
既定の値（手順なし・テストをリリース対象に数えない・CHANGELOG は根の `CHANGELOG.md`）で進める。

| 鍵 | 意味 | 既定 |
| --- | --- | --- |
| `steps` | リリースの段階で流すコマンド（`name`・`stage`・`command`・`writes`・`timeout_seconds`）。`command` の `{version}` は出す版に置き換える | 無し |
| `undistributed` | リリース対象に関わらないファイルの形（プラグインの根からの gitignore の形） | テスト（`changes_decide.DEFAULT_UNDISTRIBUTED`） |
| `bump_undistributed` | リリース対象に関わらない変化だけのプラグインも版を上げるか（#2217） | 偽 |
| `version_files` | 版上げで、プラグインの名前を含む行の版を書き換えるファイル | 無し |
| `changelog` | CHANGELOG のファイル（リポジトリの根から） | `CHANGELOG.md` |

トークン消費の記録のような、リポジトリ固有のリリースの作業は宣言の手順に書く（NDF は中身を知らない）。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ndf.contracts import facts
from ndf.domains.release import changes_decide

# 読める宣言の版
READABLE_VERSION = 1
# 宣言の手順の段階（本番の組み立て・開発版の組み立て・リリース後テスト）
STAGES = ("production", "dev", "verify")
# 手順の時間の既定（秒）
DEFAULT_TIMEOUT = 600
# 手順のコマンドで出す版に置き換える語
VERSION_PLACEHOLDER = "{version}"


class DeclarationError(Exception):
    """宣言が読めない・版が違う・値の形が違う。"""


class StepFailed(Exception):
    """宣言の手順が失敗した（終了コードが 0 でない・時間切れ・起動できない）。"""


@dataclass(frozen=True)
class Step:
    name: str
    stage: str
    command: tuple[str, ...]
    writes: tuple[str, ...] = ()
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Declaration:
    steps: tuple[Step, ...] = ()
    undistributed: tuple[str, ...] = changes_decide.DEFAULT_UNDISTRIBUTED
    bump_undistributed: bool = False
    version_files: tuple[str, ...] = ()
    changelog: str = "CHANGELOG.md"
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    def for_stage(self, stage: str) -> list[Step]:
        return [s for s in self.steps if s.stage == stage]


def path_of(root: Path) -> Path:
    place = facts.value("ndf-files", "release", "place")
    return root / facts.value("places", place, "path") / facts.value("ndf-files", "release", "path")


def _strings(raw: dict, key: str, default: Sequence[str]) -> tuple[str, ...]:
    if key not in raw:
        return tuple(default)
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DeclarationError(f"{key} は文字列の配列")
    return tuple(value)


def _step(raw: object) -> Step:
    if not isinstance(raw, dict):
        raise DeclarationError(f"steps の行がオブジェクトでない: {raw!r}")
    name, stage, command = raw.get("name"), raw.get("stage"), raw.get("command")
    if not isinstance(name, str) or stage not in STAGES:
        raise DeclarationError(f"steps の行の name か stage（{', '.join(STAGES)}）が違う: {raw!r}")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise DeclarationError(f"steps の {name} の command は空でない文字列の配列")
    timeout = raw["timeout_seconds"] if "timeout_seconds" in raw else DEFAULT_TIMEOUT
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise DeclarationError(f"steps の {name} の timeout_seconds は正の数")
    return Step(name, stage, tuple(command), _strings(raw, "writes", ()), float(timeout))


def _parse(raw: dict) -> Declaration:
    if raw.get("version") != READABLE_VERSION:
        raise DeclarationError(f"宣言の version が {READABLE_VERSION} でない: {raw.get('version')!r}")
    steps = raw["steps"] if "steps" in raw else []
    if not isinstance(steps, list):
        raise DeclarationError("steps は配列")
    flag = raw["bump_undistributed"] if "bump_undistributed" in raw else False
    changelog = raw["changelog"] if "changelog" in raw else Declaration.changelog
    if not isinstance(flag, bool) or not isinstance(changelog, str):
        raise DeclarationError("bump_undistributed は真偽、changelog は文字列")
    return Declaration(
        tuple(_step(s) for s in steps),
        _strings(raw, "undistributed", changes_decide.DEFAULT_UNDISTRIBUTED),
        flag,
        _strings(raw, "version_files", ()),
        changelog,
        raw,
    )


def load(root: Path) -> Declaration:
    """宣言を読む。無ければ既定の宣言。"""
    path = path_of(root)
    if not path.is_file():
        return Declaration()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeclarationError(f"{path} を読めない: {exc}") from None
    if not isinstance(raw, dict):
        raise DeclarationError(f"{path} が JSON のオブジェクトでない")
    return _parse(raw)


def run_step(root: Path, step: Step, version: str) -> None:
    """宣言の手順を 1 つ流す。失敗は `StepFailed`（標準エラーの末尾を添える）。"""
    argv = [part.replace(VERSION_PLACEHOLDER, version) for part in step.command]
    try:
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=step.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StepFailed(f"手順 {step.name} を流せない: {exc}") from None
    if done.returncode != 0:
        raise StepFailed(f"手順 {step.name} が終了コード {done.returncode} で失敗: {done.stderr.strip()[-500:]}")
