"""Kiro のインストーラが `--project` の誤った値を案内つきで止めることを固定する（#415）。

変更前は `cd` の裸のエラーと終了コード 1 で終わり、何を直せばよいかが読み取れなかった。
ここで見るのは、**誤りの種類ごとに文言が分かれること**・**存在しないパスにだけ
`mkdir -p` の案内が付くこと**・**案内のパスが bash の語 1 つとして読み戻せること**・
**正しい値では振る舞いが変わらないこと**の 4 つである。

終了コードはどちらの誤りも 2 であるため、文言まで見ないと 2 つの誤りを区別できない。
正しい値の確認は `--dry-run` で行い、利用者の環境へ書き込まない。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "plugins" / "ndf" / "dev.kiro" / "install.sh"


def run(*args: str, home: Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    # `--scope global` は HOME の下を導入先にする。誤って書き込んでも利用者の HOME に
    # 届かないよう、一時ディレクトリを HOME として渡す。
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        ["bash", str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
    )


def hint_words(stderr: str) -> list[str]:
    """`HINT: mkdir -p ` に続く部分を bash で読み戻し、語の並びを返す。

    `shlex.split` は `$'...'` の形を解さないため使わない（タブを含むパスで `$` が残る）。
    """
    prefix = "HINT: mkdir -p "
    lines = [line for line in stderr.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, stderr
    quoted = lines[0][len(prefix):]
    out = subprocess.run(
        ["bash", "-c", 'eval "set -- $1"; printf "%s\\0" "$@"', "_", quoted],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split("\0")[:-1]


def assert_no_bare_cd_error(proc: subprocess.CompletedProcess) -> None:
    assert "cd:" not in proc.stdout + proc.stderr


def test_project_without_path_stops_with_error(tmp_path: Path) -> None:
    proc = run("--project", home=tmp_path)

    assert proc.returncode == 2
    assert proc.stderr.splitlines() == ["ERROR: --project requires a path"]
    assert_no_bare_cd_error(proc)


@pytest.mark.parametrize("name", ["missing", "my project", "tab\tx"])
def test_missing_path_stops_with_error_and_hint(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    proc = run("--project", str(target), "--yes", home=tmp_path)

    assert proc.returncode == 2
    lines = proc.stderr.splitlines()
    assert lines[0] == f"ERROR: --project points at a path that does not exist: {target}"
    assert lines[1].startswith("HINT: mkdir -p ")
    assert len(lines) == 2
    # 案内のパスは、空白やタブを含んでも語 1 つとして渡したパスへ戻る
    assert hint_words(proc.stderr) == [str(target)]
    assert not target.exists()
    assert_no_bare_cd_error(proc)


def test_empty_path_stops_with_error_and_hint(tmp_path: Path) -> None:
    proc = run("--project", "", "--yes", home=tmp_path)

    assert proc.returncode == 2
    lines = proc.stderr.splitlines()
    assert lines[0] == "ERROR: --project points at a path that does not exist: "
    assert lines[1].startswith("HINT: mkdir -p ")
    assert len(lines) == 2
    assert hint_words(proc.stderr) == [""]
    assert list(tmp_path.iterdir()) == []
    assert_no_bare_cd_error(proc)


def test_file_path_stops_without_hint(tmp_path: Path) -> None:
    target = tmp_path / "afile"
    target.write_text("", encoding="utf-8")
    proc = run("--project", str(target), "--yes", home=tmp_path)

    assert proc.returncode == 2
    assert proc.stderr.splitlines() == [
        f"ERROR: --project points at a path that is not a directory: {target}"
    ]
    assert "HINT:" not in proc.stderr
    assert_no_bare_cd_error(proc)


def test_global_scope_with_missing_path_stops_the_same_way(tmp_path: Path) -> None:
    target = tmp_path / "missing"
    proc = run("--scope", "global", "--project", str(target), "--yes", home=tmp_path)

    assert proc.returncode == 2
    lines = proc.stderr.splitlines()
    assert lines[0] == f"ERROR: --project points at a path that does not exist: {target}"
    assert hint_words(proc.stderr) == [str(target)]
    assert len(lines) == 2
    assert "WARN:" not in proc.stderr
    assert_no_bare_cd_error(proc)


def test_global_scope_with_existing_path_warns_and_succeeds(tmp_path: Path) -> None:
    # --scope global と存在するディレクトリの併用では、PROJECT_GIVEN が真になって
    # WARN を出しつつ正常終了する。誤りではないため終了コードは 0 で、案内 (HINT) も
    # 出ない。存在しないパス併用時のエラー停止（上のテスト）とは別の経路である。
    project = tmp_path / "project"
    project.mkdir()
    proc = run(
        "--scope", "global", "--project", str(project), "--dry-run", "--yes",
        home=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "WARN: --scope global では --project は使用されません" in proc.stderr
    assert "ERROR:" not in proc.stderr
    assert "HINT:" not in proc.stderr
    assert_no_bare_cd_error(proc)


def test_existing_directory_is_unchanged(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    proc = run("--project", str(project), "--dry-run", "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "ERROR:" not in proc.stderr
    assert_no_bare_cd_error(proc)
    # --dry-run は導入先へ書き込まない
    assert list(project.iterdir()) == []


def test_reinstall_preserves_user_managed_agent_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = run("--project", str(project), "--yes", home=tmp_path)
    assert first.returncode == 0, first.stderr

    agent_file = project / ".kiro" / "agents" / "ndf.json"
    config = json.loads(agent_file.read_text(encoding="utf-8"))
    config["customKey"] = {"enabled": True}
    config.setdefault("mcpServers", {})["myserver"] = {
        "command": "myserver",
        "args": ["serve"],
    }
    config.setdefault("hooks", {})["myhook"] = [
        {"command": "echo custom", "timeout_ms": 1000}
    ]
    agent_file.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    second = run("--project", str(project), "--yes", home=tmp_path)
    assert second.returncode == 0, second.stderr

    reinstalled = json.loads(agent_file.read_text(encoding="utf-8"))
    assert reinstalled["customKey"] == {"enabled": True}
    assert reinstalled["mcpServers"]["myserver"] == {
        "command": "myserver",
        "args": ["serve"],
    }
    assert reinstalled["hooks"]["myhook"] == [
        {"command": "echo custom", "timeout_ms": 1000}
    ]
    assert reinstalled["hooks"]["agentSpawn"]
    assert reinstalled["hooks"]["userPromptSubmit"]


def test_reinstall_removes_optional_features_when_flags_omitted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = run(
        "--project",
        str(project),
        "--with-slack",
        "--with-codex",
        "--yes",
        home=tmp_path,
    )
    assert first.returncode == 0, first.stderr

    agent_file = project / ".kiro" / "agents" / "ndf.json"
    config = json.loads(agent_file.read_text(encoding="utf-8"))
    assert "stop" in config["hooks"]
    assert "codex" in config["mcpServers"]

    config["customKey"] = {"enabled": True}
    config["mcpServers"]["myserver"] = {
        "command": "myserver",
        "args": ["serve"],
    }
    config["hooks"]["myhook"] = [
        {"command": "echo custom", "timeout_ms": 1000}
    ]
    agent_file.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    second = run("--project", str(project), "--yes", home=tmp_path)
    assert second.returncode == 0, second.stderr

    reinstalled = json.loads(agent_file.read_text(encoding="utf-8"))
    assert "stop" not in reinstalled["hooks"]
    assert "codex" not in reinstalled.get("mcpServers", {})
    assert reinstalled["mcpServers"]["myserver"] == {
        "command": "myserver",
        "args": ["serve"],
    }
    assert reinstalled["hooks"]["myhook"] == [
        {"command": "echo custom", "timeout_ms": 1000}
    ]
    assert reinstalled["customKey"] == {"enabled": True}
    assert reinstalled["hooks"]["agentSpawn"]
    assert reinstalled["hooks"]["userPromptSubmit"]


@pytest.mark.parametrize(
    ("existing", "warn_marker"),
    [
        # 不正 JSON: 読み込みが例外になり、読めない旨で引き継ぎを断念する。
        ("{ not json", "を読めないため引き継ぎません"),
        # JSON オブジェクト以外（配列）: dict でないため引き継ぎを断念する。
        ("[]", "が JSON オブジェクトではないため引き継ぎません"),
        # hooks がオブジェクト以外（配列）: その節だけ引き継ぎを断念する。
        ('{"hooks": []}', "の hooks が JSON オブジェクトではないため引き継ぎません"),
        # mcpServers がオブジェクト以外（文字列）: その節だけ引き継ぎを断念する。
        ('{"mcpServers": "x"}', "の mcpServers が JSON オブジェクトではないため引き継ぎません"),
    ],
)
def test_reinstall_with_malformed_agent_config_warns_and_continues(
    tmp_path: Path, existing: str, warn_marker: str
) -> None:
    # 既存 ndf.json が不正 JSON・JSON オブジェクト以外・hooks / mcpServers が
    # オブジェクト以外のとき、引き継ぎを断念して警告を出しつつ導入を続ける経路を固定する。
    # 終了コードは 0 で、出力された ndf.json は JSON オブジェクトとして読め、
    # テンプレート由来の agentSpawn と userPromptSubmit を必ず持つ。
    project = tmp_path / "project"
    (project / ".kiro" / "agents").mkdir(parents=True)
    agent_file = project / ".kiro" / "agents" / "ndf.json"
    agent_file.write_text(existing, encoding="utf-8")

    proc = run("--project", str(project), "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    # 引き継ぎ断念の警告は Python 側の print で標準出力へ出る。
    assert f"WARN: 既存の {agent_file} {warn_marker}" in proc.stdout
    assert_no_bare_cd_error(proc)

    reinstalled = json.loads(agent_file.read_text(encoding="utf-8"))
    assert isinstance(reinstalled, dict)
    assert reinstalled["hooks"]["agentSpawn"]
    assert reinstalled["hooks"]["userPromptSubmit"]


@pytest.mark.parametrize(
    ("cwd_part", "arg", "resolved_part"),
    [(".", "project/sub", "project/sub"), ("project", ".", "project")],
)
def test_relative_path_is_resolved_against_cwd(
    tmp_path: Path, cwd_part: str, arg: str, resolved_part: str
) -> None:
    # 相対パスは実行時のカレントディレクトリを起点に `cd` と `pwd` で絶対パスへ解決され、
    # スコープの表示にはその絶対パスが出る。
    base = tmp_path.resolve()
    (base / "project" / "sub").mkdir(parents=True)
    proc = run("--project", arg, "--dry-run", "--yes", home=base, cwd=base / cwd_part)

    assert proc.returncode == 0, proc.stderr
    assert f"スコープ: workspace ({base / resolved_part}/.kiro)" in proc.stdout
    assert "ERROR:" not in proc.stderr
    assert_no_bare_cd_error(proc)
