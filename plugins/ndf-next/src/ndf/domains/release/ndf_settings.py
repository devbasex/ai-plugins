"""リリースの設定（`.ndf/release.json`）を読み、設定の手順を流す。

保存先と名前は事実の表（`ndf-files` の `release` と `locations` の `ndf`）から引き、写さない。設定が無ければ
既定の値（手順なし・テストをリリース対象に数えない・CHANGELOG は根の `CHANGELOG.md`）で進める。

| キー | 意味 | 既定 |
| --- | --- | --- |
| `steps` | リリースの段階で流すコマンド（`name`・`stage`・`command`・`writes`・`timeout_seconds`）。`command` の `{version}` は出すバージョンに置き換える | 無し |
| `undistributed` | リリース対象に関わらないファイルの形（バージョンの単位の根からの gitignore の形）。テストのファイルの表から作る既定（`changes_decide.default_undistributed`）へ足す | 無し（既定だけ） |
| `bump_undistributed` | リリース対象に関わらない変化だけのプラグインもバージョンを上げるか（#2217） | 偽 |
| `changelog` | CHANGELOG のファイル（リポジトリの根から） | `CHANGELOG.md` |

トークン消費の記録のような、リポジトリ固有のリリースの作業は設定の手順に書く（NDF は中身を知らない）。
版数の保存先と版上げで書き換える追加のファイルは、ブランチ戦略の設定（`.ndf/project.json` の `branch_strategy` の
`release.version_store`）が持つ（`core.branch_strategy`）。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ndf import contracts
from ndf.adapters import github as github_mod
from ndf.contracts import facts

# 環境変数で渡す git の設定の名前の頭（`GIT_CONFIG_COUNT`・`GIT_CONFIG_KEY_<n>`・`GIT_CONFIG_VALUE_<n>`・`GIT_CONFIG_PARAMETERS`）
_GIT_CONFIG_ENV = "GIT_CONFIG_"
# 設定のスキーマ（設定のバージョン・段階・既定の値の SSOT）
_SCHEMA = contracts.load_json("schemas/ndf-settings/release.schema.json")["properties"]
_STEP_SCHEMA = _SCHEMA["steps"]["items"]["properties"]
# 読める設定のバージョン
(READABLE_VERSION,) = _SCHEMA["version"]["enum"]
# 設定の手順の段階（本番の組み立て・開発版の組み立て・リリース後テスト）
STAGES = tuple(_STEP_SCHEMA["stage"]["enum"])
# 手順の時間の既定（秒）
DEFAULT_TIMEOUT = _STEP_SCHEMA["timeout_seconds"]["default"]
# CHANGELOG のファイルの既定（リポジトリの根から）
DEFAULT_CHANGELOG = _SCHEMA["changelog"]["default"]
# 手順のコマンドで出すバージョンに置き換える語
VERSION_PLACEHOLDER = "{version}"


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
    # 既定（テストのファイルの表）へ足す形。既定はここに写さない（#3615 の決定 5）
    undistributed: tuple[str, ...] = ()
    bump_undistributed: bool = False
    changelog: str = DEFAULT_CHANGELOG
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
        _strings(raw, "undistributed", ()),
        flag,
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


def step_env(environ: Mapping[str, str]) -> dict[str, str]:
    """設定の手順へ渡す環境変数。GitHub の認証の値（`github.TOKEN_ENV`）と、環境変数で渡す git の設定（`GIT_CONFIG_*`。
    workflow が送り先の認証のヘッダーを入れる）を消す。手順は承認の前のブランチの設定のコマンドなので、リリースの書き込みの
    トークンを渡さない。"""
    return {k: v for k, v in environ.items() if k not in github_mod.TOKEN_ENV and not k.startswith(_GIT_CONFIG_ENV)}


def run_step(root: Path, step: Step, version: str) -> None:
    """設定の手順を 1 つ流す（認証の値を消した環境で）。失敗は `StepFailed`（標準エラーの末尾を添える）。"""
    argv = [part.replace(VERSION_PLACEHOLDER, version) for part in step.command]
    try:
        done = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, timeout=step.timeout, check=False, env=step_env(os.environ)
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StepFailed(f"手順 {step.name} を流せない: {exc}") from None
    if done.returncode != 0:
        raise StepFailed(f"手順 {step.name} が終了コード {done.returncode} で失敗: {done.stderr.strip()[-500:]}")
