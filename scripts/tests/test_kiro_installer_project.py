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


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """導入先の空プロジェクトディレクトリ (tmp_path/"project") を作って返す。

    複数のテストが繰り返していた `project = tmp_path / "project"; project.mkdir()`
    の前置きを 1 箇所へまとめる。run(...) の呼び出しはテストごとに引数が異なるため
    各テストに残し、この fixture はディレクトリの用意だけに限る。
    """
    project = tmp_path / "project"
    project.mkdir()
    return project


def run(
    *args: str,
    home: Path,
    cwd: Path | None = None,
    path_prepend: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    # `--scope global` は HOME の下を導入先にする。誤って書き込んでも利用者の HOME に
    # 届かないよう、一時ディレクトリを HOME として渡す。
    env = {**os.environ, "HOME": str(home)}
    # 偽の kiro-cli を差し込むテストのため、PATH の先頭へディレクトリを足せるようにする。
    if path_prepend is not None:
        env["PATH"] = os.pathsep.join([str(path_prepend), env.get("PATH", "")])
    if extra_env is not None:
        env.update(extra_env)
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
    # 現状固定: 必須パスの種類や検査位置によらず、同じ形式のエラー 1 行と
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


def test_global_scope_with_existing_path_warns_and_succeeds(
    tmp_path: Path, project_dir: Path
) -> None:
    # --scope global と存在するディレクトリの併用では、PROJECT_GIVEN が真になって
    # WARN を出しつつ正常終了する。誤りではないため終了コードは 0 で、案内 (HINT) も
    # 出ない。存在しないパス併用時のエラー停止（上のテスト）とは別の経路である。
    proc = run(
        "--scope", "global", "--project", str(project_dir), "--dry-run", "--yes",
        home=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "WARN: --scope global では --project は使用されません" in proc.stderr
    assert "ERROR:" not in proc.stderr
    assert "HINT:" not in proc.stderr
    assert_no_bare_cd_error(proc)


def test_existing_directory_is_unchanged(tmp_path: Path, project_dir: Path) -> None:
    project = project_dir
    proc = run("--project", str(project), "--dry-run", "--yes", home=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "ERROR:" not in proc.stderr
    assert_no_bare_cd_error(proc)
    # --dry-run は導入先へ書き込まない
    assert list(project.iterdir()) == []


def test_reinstall_preserves_user_managed_agent_config(
    tmp_path: Path, project_dir: Path
) -> None:
    project = project_dir
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


def test_reinstall_removes_optional_features_when_flags_omitted(
    tmp_path: Path, project_dir: Path
) -> None:
    project = project_dir
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


def test_manifest_skills_are_linked_with_count_and_output(
    tmp_path: Path, project_dir: Path
) -> None:
    # 現状固定: Step 1 の Skill 配布パイプラインは manifest 掲載 Skill のうち
    # ndf-policies を除いた分だけ .kiro/skills/ へ symlink を張り、その本数を
    # 「Skills数」として出力し、各 Skill に "  linked: <名前>" を出す。リンク先は
    # プラグインの skills/<名前> を指す。構造改善で本数・リンク先・出力が動かないことを守る。
    project = project_dir
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


def test_reinstall_removes_only_managed_skill_links(
    tmp_path: Path, project_dir: Path
) -> None:
    # 現状固定: 掃除が消すのは現在の checkout（プラグインの skills/）配下を指す
    # 既存リンクだけである。別の場所を指す利用者のリンクは残す。再インストールでも
    # 掃除→再リンクで最終状態が manifest 掲載分と一致する。
    project = project_dir
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


# --- --set-default ワークフローの現状固定 -----------------------------------
#
# --set-default は kiro-cli の存在確認・scope からの実行ディレクトリ決定・ANSI 除去を
# 伴う現在値取得・対話確認・変更と再検証を直列に行う。実機の kiro-cli はログインを要し
# 副作用を持つため、PATH の先頭へ偽の kiro-cli を置いて経路を固定する。ここで守るのは
# 標準出力・標準エラー・終了コード・kiro-cli を呼んだディレクトリが構造改善で不変で
# あることである。

# 偽の kiro-cli。呼ばれた副コマンドと実行ディレクトリ (pwd) を $KIRO_FAKE_LOG へ追記する。
#   agent list       : $KIRO_FAKE_STATE の名前を "* <名前>" 形式（ANSI 付き）で stderr へ
#   agent set-default : SET_DEFAULT_MODE=success なら $KIRO_FAKE_STATE を書き換える。
#                       それ以外は書き換えず（未検出を模す）、どちらも終了コード 0。
_FAKE_KIRO_CLI = r"""#!/usr/bin/env bash
set -u
printf '%s\t%s\n' "$*" "$PWD" >> "$KIRO_FAKE_LOG"
if [ "$1" = "agent" ] && [ "$2" = "list" ]; then
  name="$(cat "$KIRO_FAKE_STATE" 2>/dev/null || true)"
  # ANSI 付きで stderr へ出す（install.sh 側が除去できることを固定する）
  printf '\033[32m* %s\033[0m\n' "$name" >&2
  printf '  other-agent\n' >&2
  exit 0
fi
if [ "$1" = "agent" ] && [ "$2" = "set-default" ]; then
  if [ "${SET_DEFAULT_MODE:-success}" = "success" ]; then
    printf '%s' "$3" > "$KIRO_FAKE_STATE"
  fi
  exit 0
fi
exit 0
"""


def _make_fake_kiro_cli(
    tmp_path: Path, *, initial_default: str = "kiro_default"
) -> tuple[Path, Path, Path, dict[str, str]]:
    """偽の kiro-cli を作り、(bin ディレクトリ, 状態ファイル, ログファイル, env) を返す。

    env は偽の kiro-cli が参照する KIRO_FAKE_LOG / KIRO_FAKE_STATE を持つ。install.sh の
    run(..., extra_env=env) へ渡す。
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    fake = bin_dir / "kiro-cli"
    fake.write_text(_FAKE_KIRO_CLI, encoding="utf-8")
    fake.chmod(0o755)
    state = tmp_path / "kiro_default_state"
    state.write_text(initial_default, encoding="utf-8")
    log = tmp_path / "kiro_calls.log"
    log.write_text("", encoding="utf-8")
    env = {"KIRO_FAKE_LOG": str(log), "KIRO_FAKE_STATE": str(state)}
    return bin_dir, state, log, env


def test_set_default_not_found_stops_with_error(tmp_path: Path, project_dir: Path) -> None:
    # kiro-cli が PATH に無いとき、専用のエラー 1 行と終了コード 1 で止まる。
    # 導入自体（skills/agent 生成）は完了しており、失敗するのは Step 6 だけである。
    # kiro-cli を含まない標準ディレクトリだけの PATH を与える（python3/sed/awk 等は残す）。
    std_path = "/usr/bin:/bin:/usr/sbin:/sbin"
    assert shutil.which("kiro-cli", path=std_path) is None
    env = {**os.environ, "HOME": str(tmp_path), "PATH": std_path}
    proc = subprocess.run(
        ["bash", str(INSTALLER), "--project", str(project_dir), "--set-default", "--yes"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 1
    assert (
        "ERROR: kiro-cli が見つからないため既定エージェントを変更できません"
        in proc.stderr
    )


def test_set_default_workspace_uses_project_root_and_reverifies(
    tmp_path: Path, project_dir: Path
) -> None:
    # workspace スコープでは kiro-cli を導入先プロジェクトルートで実行し、set-default 後に
    # agent list で反映を検証して成功メッセージを出す。ANSI 付きの現在値も除去される。
    bin_dir, state, log, env = _make_fake_kiro_cli(tmp_path)
    proc = run(
        "--project", str(project_dir), "--set-default", "--yes",
        home=tmp_path, path_prepend=bin_dir, extra_env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert f"既定エージェントの操作ディレクトリ: {project_dir}" in out
    assert "現在の既定エージェント: kiro_default" in out
    assert "変更後の既定エージェント: ndf" in out
    assert (
        "既定エージェントを ndf に変更しました（元に戻す: kiro-cli agent set-default kiro_default）"
        in out
    )
    # kiro-cli は導入先プロジェクトルートで呼ばれる。
    call_dirs = {
        line.split("\t", 1)[1]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line
    }
    assert call_dirs == {str(project_dir)}
    # 反映されている。
    assert state.read_text(encoding="utf-8") == "ndf"


def test_set_default_global_uses_home(tmp_path: Path) -> None:
    # global スコープでは kiro-cli を $HOME で実行する（$HOME/.kiro/agents が生成先）。
    home = tmp_path / "home"
    home.mkdir()
    bin_dir, state, log, env = _make_fake_kiro_cli(tmp_path)
    proc = run(
        "--scope", "global", "--set-default", "--yes",
        home=home, path_prepend=bin_dir, extra_env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"既定エージェントの操作ディレクトリ: {home}" in proc.stdout
    call_dirs = {
        line.split("\t", 1)[1]
        for line in log.read_text(encoding="utf-8").splitlines()
        if line
    }
    assert call_dirs == {str(home)}
    assert state.read_text(encoding="utf-8") == "ndf"


def test_set_default_reverify_failure_stops_with_error(
    tmp_path: Path, project_dir: Path
) -> None:
    # set-default が終了コード 0 でも反映されない（未検出）とき、再検証で捕らえて
    # エラー 1 行と終了コード 1 で止まる。
    bin_dir, state, log, env = _make_fake_kiro_cli(tmp_path)
    env = {**env, "SET_DEFAULT_MODE": "noop"}
    proc = run(
        "--project", str(project_dir), "--set-default", "--yes",
        home=tmp_path, path_prepend=bin_dir, extra_env=env,
    )
    assert proc.returncode == 1
    assert (
        f"ERROR: 既定エージェントを ndf に変更できませんでした（{project_dir} で検出できず）"
        in proc.stderr
    )
    # 反映されていない（初期値のまま）。
    assert state.read_text(encoding="utf-8") == "kiro_default"


def test_set_default_confirmation_rejected_leaves_default_unchanged(
    tmp_path: Path, project_dir: Path
) -> None:
    # 対話端末で確認に N を返すと、変更せず「変更しませんでした」を出して正常終了する。
    # [ -t 0 ] を真にするため pty を stdin に与える。
    import pty

    bin_dir, state, log, fake_env = _make_fake_kiro_cli(tmp_path)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]),
        **fake_env,
    }
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            ["bash", str(INSTALLER), "--project", str(project_dir), "--set-default"],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        os.write(master, b"n\n")
        stdout, stderr = proc.communicate(timeout=60)
    finally:
        os.close(master)
        os.close(slave)

    assert proc.returncode == 0, stderr
    assert "既定エージェントを ndf に変更しますか?" in stdout
    assert "既定エージェントは変更しませんでした" in stdout
    # set-default は呼ばれない。
    log_text = log.read_text(encoding="utf-8")
    assert "set-default" not in log_text
    # 既定は初期値のまま。
    assert state.read_text(encoding="utf-8") == "kiro_default"
