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
import shutil
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


@pytest.mark.parametrize(
    ("missing_path", "extra_args"),
    [
        (Path("skills"), ()),
        (Path("manifests/kiro-skills.txt"), ()),
        (Path("dev.kiro/prompts/codex.md"), ("--with-codex",)),
    ],
)
def test_missing_prerequisite_stops_with_path_error(
    tmp_path: Path, missing_path: Path, extra_args: tuple[str, ...]
) -> None:
    # 現状固定: 必須パスの種類やチェック位置によらず、同じ形式のエラー 1 行と
    # 終了コード 1 で停止する。
    plugin_dir = tmp_path / "ndf"
    shutil.copytree(INSTALLER.parents[1], plugin_dir)
    missing = plugin_dir / missing_path
    if missing.is_dir():
        shutil.rmtree(missing)
    else:
        missing.unlink()

    project = tmp_path / "project"
    project.mkdir()
    env = {**os.environ, "HOME": str(tmp_path)}
    proc = subprocess.run(
        [
            "bash",
            str(plugin_dir / "dev.kiro" / "install.sh"),
            "--project",
            str(project),
            "--dry-run",
            "--yes",
            *extra_args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert proc.returncode == 1
    assert proc.stderr.splitlines() == [f"ERROR: {missing} が見つかりません"]


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


@pytest.mark.parametrize("case", ["symlink", "file", "directory"])
def test_ndf_policies_skill_migration(tmp_path: Path, case: str) -> None:
    # ndf-policies の旧シンボリックリンクは削除する一方、同名の通常ファイルまたは
    # 実体ディレクトリは保持する分岐と、どちらでも steering を生成する結果を固定する。
    project = tmp_path / f"project_{case}"
    skills_dir = project / ".kiro" / "skills"
    skills_dir.mkdir(parents=True)
    target = skills_dir / "ndf-policies"

    if case == "symlink":
        dummy = tmp_path / "legacy_target"
        dummy.mkdir()
        target.symlink_to(dummy)
    elif case == "file":
        target.write_text("user managed ndf-policies file\n", encoding="utf-8")
    elif case == "directory":
        target.mkdir()
        (target / "custom.md").write_text("user managed content\n", encoding="utf-8")

    proc = run("--project", str(project), "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert_no_bare_cd_error(proc)

    if case == "symlink":
        assert not target.exists()
        assert not target.is_symlink()
        assert (
            "  REMOVED: ndf-policies (steering へ移行済みのため .kiro/skills のリンクを削除)"
            in proc.stdout
        )
        assert f"WARN: {target} はシンボリックリンクではありません。" not in proc.stderr
    elif case == "file":
        assert target.is_file()
        assert not target.is_symlink()
        assert target.read_text(encoding="utf-8") == "user managed ndf-policies file\n"
        assert f"WARN: {target} はシンボリックリンクではありません。" in proc.stderr
        assert "  REMOVED: ndf-policies" not in proc.stdout
    elif case == "directory":
        assert target.is_dir()
        assert not target.is_symlink()
        assert (target / "custom.md").read_text(encoding="utf-8") == "user managed content\n"
        assert f"WARN: {target} はシンボリックリンクではありません。" in proc.stderr
        assert "  REMOVED: ndf-policies" not in proc.stdout

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


def _kiro_manifest_skills() -> set[str]:
    """manifests/kiro-skills.txt が配布対象として列挙する Skill 名の集合。

    install.sh の Step 1 と同じ整形（コメント除去・末尾空白除去・空行除去）で読む。
    """
    manifest = INSTALLER.parents[1] / "manifests" / "kiro-skills.txt"
    names: set[str] = set()
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if line:
            names.add(line)
    return names


def test_manifest_skills_are_linked_with_count_and_output(tmp_path: Path) -> None:
    # 現状固定: Step 1 の Skill 配布パイプラインは manifest 掲載 Skill のうち
    # ndf-policies を除いた分だけ .kiro/skills/ へ symlink を張り、その本数を
    # 「Skills数」として出力し、各 Skill に "  linked: <名前>" を出す。リンク先は
    # プラグインの skills/<名前> を指す。構造改善で本数・リンク先・出力が動かないことを守る。
    project = tmp_path / "project"
    project.mkdir()
    proc = run("--project", str(project), "--yes", home=tmp_path)
    assert proc.returncode == 0, proc.stderr

    skills_dir = project / ".kiro" / "skills"
    plugin_skills = INSTALLER.parents[1] / "skills"
    manifest_skills = _kiro_manifest_skills()
    expected_linked = manifest_skills - {"ndf-policies"}

    # 実際に張られた symlink とそのリンク先を確認する。
    linked = {p.name for p in skills_dir.iterdir() if p.is_symlink()}
    assert linked == expected_linked
    for name in expected_linked:
        assert (skills_dir / name).resolve() == (plugin_skills / name).resolve()

    # ndf-policies は steering へ回すためリンクしない。
    assert not (skills_dir / "ndf-policies").is_symlink()

    # 出力の「linked」行と「Skills数」が本数と一致する。
    linked_lines = {
        line[len("  linked: "):]
        for line in proc.stdout.splitlines()
        if line.startswith("  linked: ")
    }
    assert linked_lines == expected_linked
    assert f"  Skills数: {len(expected_linked)} (シンボリックリンク: {skills_dir})" in proc.stdout


def test_reinstall_removes_only_managed_skill_links(tmp_path: Path) -> None:
    # 現状固定: 掃除が消すのは現在の checkout（プラグインの skills/）配下を指す
    # 既存リンクだけである。別の場所を指す利用者のリンクは残す。再インストールでも
    # 掃除→再リンクで最終状態が manifest 掲載分と一致する。
    project = tmp_path / "project"
    project.mkdir()
    first = run("--project", str(project), "--yes", home=tmp_path)
    assert first.returncode == 0, first.stderr

    skills_dir = project / ".kiro" / "skills"
    plugin_skills = INSTALLER.parents[1] / "skills"

    # 利用者が別の場所を指して張ったリンク（掃除対象外）。
    foreign_target = tmp_path / "foreign_skill"
    foreign_target.mkdir()
    (skills_dir / "user-external").symlink_to(foreign_target)

    # 現在の checkout を指すが manifest に無い名前のリンク（掃除対象）。
    stale = skills_dir / "stale-managed"
    stale.symlink_to(plugin_skills / "pr")

    second = run("--project", str(project), "--yes", home=tmp_path)
    assert second.returncode == 0, second.stderr

    manifest_skills = _kiro_manifest_skills()
    expected_managed = manifest_skills - {"ndf-policies"}

    # 現在の checkout 配下を指す managed リンクは manifest 掲載分だけが残る。
    assert not (skills_dir / "stale-managed").exists()
    # 別の場所を指す利用者のリンクは残る。
    assert (skills_dir / "user-external").is_symlink()
    assert (skills_dir / "user-external").resolve() == foreign_target.resolve()

    managed_now = {
        p.name
        for p in skills_dir.iterdir()
        if p.is_symlink()
        and str(p.resolve()).startswith(str(plugin_skills.resolve()) + os.sep)
    }
    assert managed_now == expected_managed
