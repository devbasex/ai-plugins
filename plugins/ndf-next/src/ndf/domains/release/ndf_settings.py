"""リリースの設定（`.ndf/release.json`）を読み、設定の手順を流す。

保存先と名前は事実の表（`ndf-files` の `release` と `locations` の `ndf`）から引き、写さない。設定が無ければ
既定の値（手順なし・テストをリリース対象に数えない・CHANGELOG は根の `CHANGELOG.md`）で進める。

| キー | 意味 | 既定 |
| --- | --- | --- |
| `steps` | リリースの段階で流すコマンド（`name`・`stage`・`command`・`writes`・`timeout_seconds`）。`command` の `{version}` は出すバージョンに置き換える | 無し |
| `undistributed` | リリース対象に関わらないファイルの形（プラグインの根からの gitignore の形） | テスト（`changes_decide.DEFAULT_UNDISTRIBUTED`） |
| `bump_undistributed` | リリース対象に関わらない変化だけのプラグインもバージョンを上げるか（#2217） | 偽 |
| `version_files` | 版上げで、プラグインの名前を含む行のバージョンを書き換えるファイル | 無し |
| `changelog` | CHANGELOG のファイル（リポジトリの根から） | `CHANGELOG.md` |

トークン消費の記録のような、リポジトリ固有のリリースの作業は設定の手順に書く（NDF は中身を知らない）。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ndf import identity
from ndf.adapters import git as git_mod
from ndf.contracts import facts
from ndf.domains.release import changes_decide

# 読める設定のバージョン
READABLE_VERSION = 1
# 設定の手順の段階（本番の組み立て・開発版の組み立て・リリース後テスト）
STAGES = ("production", "dev", "verify")
# 手順の時間の既定（秒）
DEFAULT_TIMEOUT = 600
# 手順のコマンドで出すバージョンに置き換える語
VERSION_PLACEHOLDER = "{version}"
# プロジェクトの設定（迂回の設定 `production_merge.admin` を持つ。#3506）
PROJECT_FILE = Path(identity.FIXED_WORDS["ndf_settings_dir"]) / "project.json"


class NdfSettingsError(Exception):
    """設定が読めない・バージョンが違う・値の形が違う。"""


class StepFailed(Exception):
    """設定の手順が失敗した（終了コードが 0 でない・時間切れ・起動できない）。"""


@dataclass(frozen=True)
class Step:
    name: str
    stage: str
    command: tuple[str, ...]
    writes: tuple[str, ...] = ()
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class NdfSettings:
    steps: tuple[Step, ...] = ()
    undistributed: tuple[str, ...] = changes_decide.DEFAULT_UNDISTRIBUTED
    bump_undistributed: bool = False
    version_files: tuple[str, ...] = ()
    changelog: str = "CHANGELOG.md"
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    def for_stage(self, stage: str) -> list[Step]:
        return [s for s in self.steps if s.stage == stage]


def path_of(root: Path) -> Path:
    location = facts.value("ndf-files", "release", "location")
    return root / facts.value("locations", location, "path") / facts.value("ndf-files", "release", "path")


def _value(raw: dict, key: str, default: object) -> object:
    """キーがあれば値、無ければ `default`。"""
    return raw[key] if key in raw else default


def _strings(raw: dict, key: str, default: Sequence[str]) -> tuple[str, ...]:
    value = _value(raw, key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise NdfSettingsError(f"{key} は文字列の配列")
    return tuple(value)


def _step(raw: object) -> Step:
    if not isinstance(raw, dict):
        raise NdfSettingsError(f"steps の行がオブジェクトでない: {raw!r}")
    name, stage, command = raw.get("name"), raw.get("stage"), raw.get("command")
    if not isinstance(name, str) or stage not in STAGES:
        raise NdfSettingsError(f"steps の行の name か stage（{', '.join(STAGES)}）が違う: {raw!r}")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise NdfSettingsError(f"steps の {name} の command は空でない文字列の配列")
    timeout = _value(raw, "timeout_seconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise NdfSettingsError(f"steps の {name} の timeout_seconds は正の数")
    return Step(name, stage, tuple(command), _strings(raw, "writes", ()), float(timeout))


def _parse(raw: dict) -> NdfSettings:
    if raw.get("version") != READABLE_VERSION:
        raise NdfSettingsError(f"設定の version が {READABLE_VERSION} でない: {raw.get('version')!r}")
    steps = _value(raw, "steps", [])
    if not isinstance(steps, list):
        raise NdfSettingsError("steps は配列")
    flag = _value(raw, "bump_undistributed", False)
    changelog = _value(raw, "changelog", NdfSettings.changelog)
    if not isinstance(flag, bool) or not isinstance(changelog, str):
        raise NdfSettingsError("bump_undistributed は真偽、changelog は文字列")
    return NdfSettings(
        tuple(_step(s) for s in steps),
        _strings(raw, "undistributed", changes_decide.DEFAULT_UNDISTRIBUTED),
        flag,
        _strings(raw, "version_files", ()),
        changelog,
        raw,
    )


def load(root: Path) -> NdfSettings:
    """設定を読む。無ければ既定の設定。"""
    path = path_of(root)
    if not path.is_file():
        return NdfSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NdfSettingsError(f"{path} を読めない: {exc}") from None
    if not isinstance(raw, dict):
        raise NdfSettingsError(f"{path} が JSON のオブジェクトでない")
    return _parse(raw)


def run_step(root: Path, step: Step, version: str) -> None:
    """設定の手順を 1 つ流す。失敗は `StepFailed`（標準エラーの末尾を添える）。"""
    argv = [part.replace(VERSION_PLACEHOLDER, version) for part in step.command]
    try:
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=step.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StepFailed(f"手順 {step.name} を流せない: {exc}") from None
    if done.returncode != 0:
        raise StepFailed(f"手順 {step.name} が終了コード {done.returncode} で失敗: {done.stderr.strip()[-500:]}")


def _admin_ndf_settings(root: Path) -> Path:
    """迂回の設定を読むファイル（コントラクトの `production_merge` の保存先は `main`）。

    メインディレクトリが解ければそこのファイルだけを読み、無くても呼ばれた木の値で補わない。PR の作業場所が
    足した設定だけで迂回を選べないようにする（旧いプラグインの `decl.overlay_keys` と同じ扱い。#2363・#3506 の I1）。
    git の外ならメインディレクトリを持たないので呼ばれた木。
    """
    try:
        main = git_mod.Git(root).common_dir().parent
    except git_mod.GitError:  # git の外はメインディレクトリが無いので呼ばれた木の設定を読む
        return root / PROJECT_FILE
    return main / PROJECT_FILE


def production_admin(root: Path) -> bool:
    """迂回の設定（`.ndf/project.json` の `production_merge.admin`）が JSON の true か（#3506 の I1）。

    設定はメインディレクトリから読む（`_admin_ndf_settings`）。ファイルが無い・読めない・壊れている・値の型が違う・
    `unknown` は偽（迂回しない）。
    """
    try:
        raw = json.loads(_admin_ndf_settings(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない設定は設定なしとして迂回しない（#3506 の I1）
        return False
    got = raw.get("production_merge") if isinstance(raw, dict) else None
    return isinstance(got, dict) and got.get("admin") is True
