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


def test_reinstall_removes_deprecated_prompts_and_keeps_user_prompt(tmp_path: Path) -> None:
    # 配布を終えた clean.md と review.md は再インストールで除去し、利用者が置いた
    # 無関係な prompt は残す。削除範囲が広がる退行を検出するための現状固定である。
    project = tmp_path / "project"
    prompts_dir = project / ".kiro" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "clean.md").write_text("old clean\n", encoding="utf-8")
    (prompts_dir / "review.md").write_text("old review\n", encoding="utf-8")
    custom_text = "利用者が置いた prompt\n"
    (prompts_dir / "custom.md").write_text(custom_text, encoding="utf-8")

    proc = run("--project", str(project), "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "  removed (deprecated): clean" in proc.stdout.splitlines()
    assert "  removed (deprecated): review" in proc.stdout.splitlines()
    assert not (prompts_dir / "clean.md").exists()
    assert not (prompts_dir / "review.md").exists()
    assert (prompts_dir / "pr.md").is_file()
    assert (prompts_dir / "custom.md").read_text(encoding="utf-8") == custom_text

    # --with-codex を付けないため codex.md は配布されない
    distributed = {
        p.name
        for p in (INSTALLER.parent / "prompts").glob("*.md")
        if p.name != "codex.md"
    }
    assert distributed
    assert {p.name for p in prompts_dir.iterdir()} == distributed | {"custom.md"}


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


SKILLS_HEADER = "Skills シンボリックリンクを作成中..."
PROMPTS_HEADER = "ワークフロープロンプトを作成中..."


def distributed_skills() -> set[str]:
    manifest = ROOT / "plugins" / "ndf" / "manifests" / "kiro-skills.txt"
    names = {
        line.split("#", 1)[0].rstrip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
    }
    # ndf-policies は steering として配置し、Skill としてはリンクしない
    return names - {"", "ndf-policies"}


def distributed_prompts() -> set[str]:
    return {
        p.stem for p in (INSTALLER.parent / "prompts").glob("*.md") if p.name != "codex.md"
    }


def ndf_version() -> str:
    manifest = ROOT / "plugins" / "ndf" / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def split_listing(stdout: str, project: Path) -> tuple[list[str], dict[str, set[str]]]:
    """標準出力を、一覧の行（`linked:` / `prompt:`）とそれ以外の行へ分ける。

    一覧の並びは `sort` のロケールで変わるため、名前を集合で返す。一覧の行が
    どの見出しの下に出たかは、`<見出し>/<種類>` の鍵で残す。
    """
    rest: list[str] = []
    listed: dict[str, set[str]] = {}
    section = ""
    for line in stdout.replace(str(project), "<PROJECT>").splitlines():
        kind, sep, name = line.strip().partition(": ")
        if line.startswith("  ") and sep and kind in ("linked", "prompt"):
            listed.setdefault(f"{section}/{kind}", set()).add(name)
            continue
        if not line.startswith("  "):
            section = line
        rest.append(line)
    return rest, listed


def expected_head_lines() -> list[str]:
    return [
        "=== NDF Plugin Installer for Kiro CLI ===",
        "  スコープ: workspace (<PROJECT>/.kiro)",
        SKILLS_HEADER,
        "  SKIP: ndf-policies (steering として配置)",
        PROMPTS_HEADER,
        "Slack通知: 無効 (--with-slack で有効化)",
        "Codex CLI連携: 無効 (--with-codex で有効化)",
    ]


def test_dry_run_output_is_characterized(tmp_path: Path) -> None:
    # 導入の各段が並ぶ順序と dry-run の要約を、標準出力の全行で固定する（現状固定）。
    project = tmp_path / "project"
    project.mkdir()
    proc = run("--project", str(project), "--dry-run", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    rest, listed = split_listing(proc.stdout, project)
    skills = distributed_skills()
    assert rest == expected_head_lines() + [
        "",
        "DRY RUN: 書き込みは行いませんでした",
        f"  NDF バージョン: {ndf_version()}",
        "  エージェント設定: <PROJECT>/.kiro/agents/ndf.json",
        "  常時指示: <PROJECT>/.kiro/steering/ndf-policies.md",
        f"  Skills数: {len(skills)}",
    ]
    assert listed == {
        f"{SKILLS_HEADER}/linked": skills,
        f"{PROMPTS_HEADER}/prompt": distributed_prompts(),
    }
    assert list(project.iterdir()) == []


def test_install_output_and_generated_files_are_characterized(tmp_path: Path) -> None:
    # 通常導入の標準出力の全行と、導入先に生成されるファイルの一覧を固定する（現状固定）。
    project = tmp_path / "project"
    project.mkdir()
    proc = run("--project", str(project), home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    rest, listed = split_listing(proc.stdout, project)
    skills = distributed_skills()
    prompts = distributed_prompts()
    assert rest == expected_head_lines() + [
        "常時指示を生成: <PROJECT>/.kiro/steering/ndf-policies.md",
        "",
        "=== インストール完了 ===",
        f"  NDF バージョン: {ndf_version()}",
        "  エージェント設定: <PROJECT>/.kiro/agents/ndf.json",
        "  常時指示: <PROJECT>/.kiro/steering/ndf-policies.md",
        f"  Skills数: {len(skills)} (シンボリックリンク: <PROJECT>/.kiro/skills)",
        "",
        "前提: NDF は 1M コンテキストのモデルを対象に配布 Skill を組んでいます。",
        f"  起動時に読み込む文脈は {len(skills)} 個の SKILL.md と AGENTS.md / README.md / 常時指示です。",
        "  既定の auto はこの条件を満たします。モデルを固定する場合も 1M のものを選んでください。",
        "",
        "Kiro CLIを起動して動作確認してください:",
        "  kiro-cli chat --agent ndf",
        "",
        "既定エージェントとして起動したい場合は --set-default を付けて再実行してください。",
    ]
    assert listed == {
        f"{SKILLS_HEADER}/linked": skills,
        f"{PROMPTS_HEADER}/prompt": prompts,
    }

    # リンクした Skill の先へは降りずに、導入先の全エントリを並べる
    generated = set()
    for dirpath, dirnames, filenames in os.walk(project):
        for name in dirnames + filenames:
            generated.add((Path(dirpath) / name).relative_to(project).as_posix())
    assert generated == (
        {".kiro", ".kiro/agents", ".kiro/agents/ndf.json", ".kiro/prompts", ".kiro/skills"}
        | {".kiro/steering", ".kiro/steering/ndf-policies.md"}
        | {f".kiro/prompts/{name}.md" for name in prompts}
        | {f".kiro/skills/{name}" for name in skills}
    )
    skills_src = INSTALLER.parent.parent / "skills"
    for name in skills:
        link = project / ".kiro" / "skills" / name
        assert link.is_symlink()
        assert os.readlink(link) == str(skills_src / name)


def place_policy_symlink(target: Path, tmp_path: Path) -> None:
    dummy = tmp_path / "legacy_target"
    dummy.mkdir()
    target.symlink_to(dummy)


def place_policy_file(target: Path, tmp_path: Path) -> None:
    target.write_text("user managed ndf-policies file\n", encoding="utf-8")


def place_policy_directory(target: Path, tmp_path: Path) -> None:
    target.mkdir()
    (target / "custom.md").write_text("user managed content\n", encoding="utf-8")


# 事前に `.kiro/skills/ndf-policies` へ置くものと、実行後に残る内容（target からの
# 相対パスと本文）の対応。残る内容が空の case は、エントリごと削除される。
NDF_POLICIES_CASES = {
    "symlink": (place_policy_symlink, {}),
    "file": (place_policy_file, {".": "user managed ndf-policies file\n"}),
    "directory": (place_policy_directory, {"custom.md": "user managed content\n"}),
}


@pytest.mark.parametrize("case", list(NDF_POLICIES_CASES))
def test_ndf_policies_skill_migration(tmp_path: Path, case: str) -> None:
    # ndf-policies の旧シンボリックリンクは削除する一方、同名の通常ファイルまたは
    # 実体ディレクトリは保持する分岐と、どちらでも steering を生成する結果を固定する。
    project = tmp_path / f"project_{case}"
    skills_dir = project / ".kiro" / "skills"
    skills_dir.mkdir(parents=True)
    target = skills_dir / "ndf-policies"
    place, kept = NDF_POLICIES_CASES[case]
    removed = not kept

    place(target, tmp_path)

    proc = run("--project", str(project), "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert_no_bare_cd_error(proc)

    assert not target.is_symlink()
    assert target.exists() is not removed
    for relative, text in kept.items():
        assert (target / relative).read_text(encoding="utf-8") == text
    assert (
        "  REMOVED: ndf-policies (steering へ移行済みのため .kiro/skills のリンクを削除)"
        in proc.stdout
    ) is removed
    assert ("  REMOVED: ndf-policies" in proc.stdout) is removed
    assert (f"WARN: {target} はシンボリックリンクではありません。" in proc.stderr) is not removed

    steering_file = project / ".kiro" / "steering" / "ndf-policies.md"
    assert steering_file.is_file()
    steering_content = steering_file.read_text(encoding="utf-8")
    assert (
        "<!-- plugins/ndf/dev.kiro/install.sh が生成します。直接編集しないでください。 -->"
        in steering_content
    )
    assert "<!-- 編集元: plugins/ndf/skills/ndf-policies/SKILL.md -->" in steering_content
    assert "# NDFポリシー" in steering_content
    assert "## ブランチ運用の原則" in steering_content

    source_skill = ROOT / "plugins" / "ndf" / "skills" / "ndf-policies" / "SKILL.md"
    source_body = source_skill.read_text(encoding="utf-8").split("\n---\n", 1)[1].strip("\n")
    assert source_body in steering_content

