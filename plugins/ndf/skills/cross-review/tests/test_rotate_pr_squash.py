"""rotate-pr.sh の squash モードに関する現状固定テスト（#727 / R5-003）。

squash モードの振る舞い（(rotated) 接尾辞の正規化、prepare.json / gh pr view からの
メタ情報フォールバック解決、ブランチ復元、PR body 生成）を固定する。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROTATE_SH = HERE.parent / "scripts" / "rotate-pr.sh"


def _run_bash(script: str, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env or os.environ.copy(),
        cwd=cwd,
    )


def test_squash_helpers_are_defined() -> None:
    """R5-003 で抽出されたヘルパー関数が rotate-pr.sh 内に定義されていることを確認する。"""
    body = ROTATE_SH.read_text(encoding="utf-8")
    assert "resolve_squash_pr_metadata()" in body
    assert "resolve_squash_head_branch()" in body
    assert "normalize_rotated_title()" in body
    assert "create_and_push_squash_branch()" in body
    assert "build_squash_body()" in body


# ---------------- 1. (rotated) 接尾辞の正規化 ----------------

@pytest.mark.parametrize(
    ("original_title", "expected_title"),
    [
        ("Fix foo", "Fix foo (rotated)"),
        ("Fix foo (rotated)", "Fix foo (rotated)"),
        ("Fix foo (rotated2)", "Fix foo (rotated)"),
        ("Fix foo (rotated)(rotated)", "Fix foo (rotated)"),
        ("Fix foo (rotated123)", "Fix foo (rotated)"),
        ("Fix foo  (rotated)", "Fix foo (rotated)"),
        ("feat: add (special) feature", "feat: add (special) feature (rotated)"),
    ],
)
def test_title_rotated_suffix_normalization(original_title: str, expected_title: str) -> None:
    """rotate-pr.sh の接尾辞正規化ロジックの現状固定テスト。"""
    snippet = f"""
    eval "$(sed -n '/^normalize_rotated_title() {{/,/^}}/p' {ROTATE_SH})"
    normalize_rotated_title {json.dumps(original_title)}
    """
    res = _run_bash(snippet)
    assert res.returncode == 0
    assert res.stdout == expected_title


# ---------------- 2. PR メタ情報（base / title）の解決 ----------------

def test_pr_metadata_reads_prepare_json(tmp_path: pathlib.Path) -> None:
    """prepare.json が存在する場合、base_branch と old_title を優先して読み込む。"""
    prep_file = tmp_path / "prepare.json"
    prep_file.write_text(
        json.dumps({
            "base_branch": "develop",
            "old_title": "Fix something important",
            "head_branch": "feat/my-branch",
        }),
        encoding="utf-8",
    )
    snippet = f"""
    eval "$(sed -n '/^resolve_squash_pr_metadata() {{/,/^}}/p' {ROTATE_SH})"
    resolve_squash_pr_metadata {json.dumps(str(prep_file))} "123"
    """
    res = _run_bash(snippet)
    assert res.returncode == 0
    meta = json.loads(res.stdout)
    assert meta["base"] == "develop"
    assert meta["title"] == "Fix something important"


# ---------------- 3. ブランチ名解決のフォールバック順 ----------------

def test_branch_resolution_from_prepare_json_when_detached(tmp_path: pathlib.Path) -> None:
    """detached HEAD で git branch --show-current が空の場合、prepare.json の head_branch を使う。"""
    prep_file = tmp_path / "prepare.json"
    prep_file.write_text(
        json.dumps({
            "head_branch": "feature/from-prepare",
        }),
        encoding="utf-8",
    )
    # git branch --show-current を空文字で模す一時ラッパー
    snippet = f"""
    git() {{
      if [ "$1" = "branch" ] && [ "$2" = "--show-current" ]; then
        return 0
      fi
      command git "$@"
    }}
    eval "$(sed -n '/^resolve_squash_head_branch() {{/,/^}}/p' {ROTATE_SH})"
    resolve_squash_head_branch {json.dumps(str(prep_file))} "123"
    """
    res = _run_bash(snippet)
    assert res.returncode == 0
    assert res.stdout == "feature/from-prepare"


# ---------------- 4. 新 PR 本文の生成 ----------------

def test_squash_body_content() -> None:
    """squash モードで生成される新 PR 本文の構造を固定する。"""
    snippet = f"""
    eval "$(sed -n '/^build_squash_body() {{/,/^}}/p' {ROTATE_SH})"
    build_squash_body "123" "8"
    """
    res = _run_bash(snippet)
    assert res.returncode == 0
    assert "旧 PR #123 をベースに、cross-review クロスレビューループの継続。" in res.stdout
    assert "round_in_pr=8" in res.stdout
    assert "<!-- I want to review in Japanese. -->" in res.stdout
