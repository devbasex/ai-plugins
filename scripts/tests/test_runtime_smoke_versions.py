"""スモークが入れる CLI の版の固定と、最新の版のスモークの workflow の形（#1982 の I2・I3・I5）。

- I2: CLI の版の一覧（`tests/runtime-smoke/cli-versions.env`）のキーの集合が、4 つの Containerfile が
  宣言する `ARG` の名前の和集合と一致し、各 `ARG` は接頭辞のランタイムの Containerfile にだけ現れ、
  値は空でなく、`ARG` は既定値を持たない
- I3: 配布の経路の取得先は版数を含む取得先だけ（`latest`・公式のインストーラを使わない）
- I5: 最新の版のスモークは PR と push で起動せず、権限は `contents: read`・`issues: write` だけ
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SMOKE = REPO / "tests" / "runtime-smoke"
VERSIONS = SMOKE / "cli-versions.env"
LATEST_WORKFLOW = REPO / ".github" / "workflows" / "runtime-plugin-smoke-latest.yml"
PREFIXES = {"claude": "CLAUDE_CODE_", "codex": "CODEX_", "kiro": "KIRO_CLI_", "agy": "AGY_"}
ARG_RE = re.compile(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)(=.*)?\s*$", re.MULTILINE)


def read_versions(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        assert sep, f"KEY=VALUE でない行: {line!r}"
        assert key not in pairs, f"キーが重なる: {key}"
        pairs[key] = value
    return pairs


def containerfile_args(runtime: str) -> list[tuple[str, str | None]]:
    text = (SMOKE / f"Containerfile.{runtime}").read_text(encoding="utf-8")
    return [(m.group(1), m.group(2)) for m in ARG_RE.finditer(text)]


def install_lines(runtime: str) -> str:
    """Containerfile のうち、ARG とコメントを除いた行（取得の手順）"""
    text = (SMOKE / f"Containerfile.{runtime}").read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "ARG ")))


def test_versions_match_containerfile_args():
    versions = read_versions(VERSIONS)
    declared: set[str] = set()
    for runtime, prefix in PREFIXES.items():
        args = containerfile_args(runtime)
        assert args, f"Containerfile.{runtime} に ARG が無い"
        for name, default in args:
            assert default is None, f"Containerfile.{runtime} の ARG {name} が既定値を持つ"
            assert name.startswith(prefix), f"Containerfile.{runtime} の ARG {name} が {prefix} で始まらない"
            declared.add(name)
    assert declared == set(versions), "一覧のキーと ARG の名前が一致しない"
    empty = [k for k, v in versions.items() if not v.strip()]
    assert not empty, f"値が空: {empty}"


def test_versions_are_concrete():
    versions = read_versions(VERSIONS)
    for key in ("CLAUDE_CODE_VERSION", "CODEX_VERSION", "KIRO_CLI_VERSION", "AGY_VERSION"):
        assert re.fullmatch(r"[0-9]+(\.[0-9]+)+", versions[key]), f"{key} が版数でない: {versions[key]}"
    for key in ("AGY_URL_X86_64", "AGY_URL_AARCH64"):
        assert f"/{versions['AGY_VERSION']}-" in versions[key], f"{key} が AGY_VERSION の版を含まない"


@pytest.mark.parametrize("runtime", sorted(PREFIXES))
def test_install_uses_versioned_sources_only(runtime):
    body = install_lines(runtime)
    lowered = body.lower()
    assert "latest" not in lowered, f"Containerfile.{runtime} が latest を使う"
    assert "install.sh" not in body and "cli.kiro.dev/install" not in body, f"Containerfile.{runtime} が公式のインストーラを使う"
    # 取得の行は一覧の値（${...}）を含む取得先だけを使う
    for m in re.finditer(r"npm install -g (\S+)", body):
        assert "@${" in m.group(1), f"Containerfile.{runtime} が版の無い npm の取得先を使う: {m.group(1)}"
    for m in re.finditer(r"curl [^;]*", body):
        assert "${" in m.group(0) or '"$url"' in m.group(0), f"Containerfile.{runtime} が版の無い取得先を使う"
    # 入った CLI の版を一覧の版数と照らす（I1）
    version_key = f"{PREFIXES[runtime]}VERSION"
    assert re.search(rf'\[ "\$actual" = "\${version_key}" \]', body), f"Containerfile.{runtime} が版を照らさない"


def test_latest_workflow_triggers_and_permissions():
    data = yaml.safe_load(LATEST_WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML は `on` を True と読む
    triggers = data.get("on", data.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert data["permissions"] == {"contents": "read", "issues": "write"}
    for job in data["jobs"].values():
        assert job.get("permissions") in (None, {"contents": "read", "issues": "write"})
    assert "secrets." not in LATEST_WORKFLOW.read_text(encoding="utf-8")
