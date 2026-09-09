"""公開されている配布起点の Bash 例を、本物の Git で現状固定する。"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

FORM = Path(__file__).resolve().parents[1] / "references/form-package-plugin.md"
PLUGIN_JSON = Path("plugins/ndf/.claude-plugin/plugin.json")
PAYLOAD = Path("plugins/ndf/payload.txt")

# 検査用リポジトリの開発ブランチ名。例の側は HEAD しか見ないため、この値は試験の中で閉じる。
DEFAULT_BRANCH = "development"
# 例が `--list 'ndf--v*'` で絞る接頭辞。ここを変えると例の選び方から外れる。
TAG_PREFIX = "ndf--v"


@pytest.fixture
def repository(tmp_path: Path):
    # 設定・署名・フックと、呼び出し元の GIT_DIR 等を持ち込まない。
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=tmp_path, env=env,
            text=True, capture_output=True, check=True,
        )

    git("init", "--template=", f"--initial-branch={DEFAULT_BRANCH}")
    git("config", "core.hooksPath", os.devnull)
    git("config", "commit.gpgSign", "false")
    git("config", "tag.gpgSign", "false")
    (tmp_path / PLUGIN_JSON).parent.mkdir(parents=True)
    return tmp_path, env, git


def commit_package(repository, version: str, payload: str) -> None:
    root, _, git = repository
    (root / PLUGIN_JSON).write_text(
        json.dumps({"version": version}) + "\n", encoding="utf-8",
    )
    (root / PAYLOAD).write_text(payload + "\n", encoding="utf-8")
    git("add", "plugins/ndf")
    git("commit", "-m", "テスト用の配布内容を記録")


def run_base_example(repository) -> subprocess.CompletedProcess[str]:
    root, env, _ = repository
    body = FORM.read_text(encoding="utf-8")
    example = re.search(r"^```bash\n(.*?)^```", body, re.MULTILINE | re.DOTALL)
    assert example, "文書の最初の Bash ブロックが見つからない"
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-euo", "pipefail", "-c", example.group(1)],
        cwd=root, env=env, text=True, capture_output=True,
    )


def test_latest_product_tag_is_used_even_outside_head_ancestry(repository) -> None:
    """現状固定: 版順・製品名で選び、祖先でない公開版と同じ配布内容なら差分なし。"""
    root, _, git = repository
    commit_package(repository, "1.9.0", "古い配布内容")
    git("tag", f"{TAG_PREFIX}1.9.0")
    git("tag", "other--v99.0.0")

    git("checkout", "-b", "published")
    commit_package(repository, "1.10.0", "最新の配布内容")
    git("tag", f"{TAG_PREFIX}1.10.0")

    git("checkout", DEFAULT_BRANCH)
    # 配布物以外の差も置き、比較対象をプラグインへ絞る挙動を通す。
    (root / "development.txt").write_text("開発側だけの記録\n", encoding="utf-8")
    git("add", "development.txt")
    commit_package(repository, "1.10.0", "最新の配布内容")
    assert git("rev-list", f"HEAD..{TAG_PREFIX}1.10.0").stdout.strip()

    result = run_base_example(repository)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_without_tags_the_version_commit_is_used(repository) -> None:
    """現状固定: 対象タグがなければ現行版を書いたコミットからの差を数える。"""
    commit_package(repository, "1.9.0", "以前の内容")
    commit_package(repository, "1.10.0", "版を書いた時点の内容")
    commit_package(repository, "1.10.0", "版を書いた後の変更")

    result = run_base_example(repository)

    assert result.returncode == 0, result.stderr
    assert re.search(r"\b1 file changed\b", result.stdout), result.stdout
