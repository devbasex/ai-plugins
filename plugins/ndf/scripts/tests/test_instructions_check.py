"""指示書の検査（`instructions-check.py`、#554）。

一時ディレクトリに最小のリポジトリ（`git init` と指示書）を作り、`--root` で渡す。
継続的統合は浅い clone で過去のコミットを持たないため、このリポジトリの過去の状態は
テストから読まない（実例の再現は Pull Request の本文に残す）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "instructions-check.py"


# --- 一時リポジトリの組み立て ------------------------------------------------

def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=True,
    )


def make_repo(tmp_path: Path, files: dict[str, str], *, track: bool = True) -> Path:
    """指示書を置いた一時リポジトリを作る。`track` が偽なら追跡させない。"""
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    if not (root / ".git").exists():
        git(root, "init", "-q")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "t")
    for name, body in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    if track:
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "t")
    return root


def declare(root: Path, decl: dict) -> None:
    path = root / ".ndf" / "instructions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decl, ensure_ascii=False), encoding="utf-8")


def run(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True, text=True, cwd=str(cwd) if cwd else None,
    )


def errors(proc: subprocess.CompletedProcess) -> list[str]:
    return [line for line in proc.stderr.splitlines() if line.startswith("ERROR:")]


# --- 走査の対象（AC1〜AC5） --------------------------------------------------

def test_ac1_scans_root_and_nested_defaults(tmp_path):
    root = make_repo(tmp_path, {
        "AGENTS.md": "# a\n", "CLAUDE.md": "# c\n", "KIRO.md": "# k\n",
        "docs/AGENTS.md": "# d\n", "docs/other.md": "# o\n",
    })
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "4 本" in proc.stdout
    assert "根 3" in proc.stdout and "配下 1" in proc.stdout


def test_ac2_untracked_files_are_not_scanned(tmp_path):
    root = make_repo(tmp_path, {"AGENTS.md": "# a\n"})
    (root / "CLAUDE.md").write_text("# untracked\n", encoding="utf-8")
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "1 本" in proc.stdout
    assert "CLAUDE.md" not in proc.stdout


def test_ac3_no_instruction_file_exits_zero(tmp_path):
    root = make_repo(tmp_path, {"README.md": "# r\n"})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "対象" in proc.stdout


def test_ac3_unreadable_release_wins_over_no_target(tmp_path):
    root = make_repo(tmp_path, {"README.md": "# r\n"})
    declare(root, {"version": 1, "released": {
        "source": "changelog", "path": "CHANGELOG.md",
        "pattern": r"^## \[(?P<version>\d+\.\d+\.\d+)\]"}})
    proc = run(root)
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_ac4_files_matches_name_or_relative_path(tmp_path):
    root = make_repo(tmp_path, {
        "AGENTS.md": "# a\n", "docs/AGENTS.md": "# d\n",
        "notes/POLICY.md": "# p\n", "POLICY.md": "# root p\n",
    })
    declare(root, {"version": 1, "files": ["AGENTS.md", "notes/POLICY.md"]})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "3 本" in proc.stdout
    assert "notes/POLICY.md" in proc.stdout


def test_ac5_files_replaces_the_defaults(tmp_path):
    root = make_repo(tmp_path, {"AGENTS.md": "# a\n", "CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "files": ["AGENTS.md"]})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "1 本" in proc.stdout


# --- 宣言が無くても動く判定（AC19〜AC31） ------------------------------------

def test_ac19_broken_import_is_reported(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n\n@docs/none.md を読む\n"})
    proc = run(root)
    assert proc.returncode == 1
    assert errors(proc) == ["ERROR: [直す] CLAUDE.md:3: "
                            "@docs/none.md の参照先が無い。参照を消すか実在するパスへ直す"]


def test_ac20_non_import_syntax_file_is_not_checked(tmp_path):
    root = make_repo(tmp_path, {"AGENTS.md": "# a\n\n@docs/none.md\n"})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac21_import_syntax_can_be_extended(tmp_path):
    root = make_repo(tmp_path, {"AGENTS.md": "# a\n\n@docs/none.md\n"})
    declare(root, {"version": 1, "import_syntax": ["CLAUDE.md", "AGENTS.md"]})
    proc = run(root)
    assert proc.returncode == 1
    assert any("AGENTS.md:3" in line for line in errors(proc))


def test_ac22_allowance_on_non_import_syntax_file_is_reported(tmp_path):
    root = make_repo(tmp_path, {"AGENTS.md": "# a\n", "CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "imports": {"AGENTS.md": {"CLAUDE.md": "理由"}}})
    proc = run(root)
    assert proc.returncode == 1
    assert any("効かない" in line for line in errors(proc))


def test_ac23_reference_resolves_from_the_writing_file(tmp_path):
    root = make_repo(tmp_path, {
        "docs/CLAUDE.md": "# d\n\n@x.md\n", "docs/x.md": "x\n",
    })
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac24_home_and_outside_references_are_not_checked(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n\n@~/none.md\n\n@../none.md\n"})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac25_symlink_outside_root_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.md").write_text("x" * 5000, encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n\n@link.md\n"}, track=False)
    (root / "link.md").symlink_to(outside / "big.md")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "t")
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "5,0" not in proc.stdout


def test_ac26_code_span_and_block_are_ignored(tmp_path):
    body = "# c\n\n`@openai/codex` と書く\n\n```\n@docs/none.md\n```\n"
    root = make_repo(tmp_path, {"CLAUDE.md": body})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac27_mail_and_parenthesis_are_ignored(tmp_path):
    body = "# c\n\nuser@example.com へ送る\n\n(@none.md) と（@none2.md）\n"
    root = make_repo(tmp_path, {"CLAUDE.md": body})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac28_emphasis_and_space_are_references(tmp_path):
    body = "# c\n\n**@none.md** を読む\n\n次は @none2.md である\n"
    root = make_repo(tmp_path, {"CLAUDE.md": body})
    proc = run(root)
    assert proc.returncode == 1
    lines = errors(proc)
    assert any("@none.md" in line for line in lines)
    assert any("@none2.md" in line for line in lines)


def test_ac29_size_follows_imports_once_and_stops_on_cycle(tmp_path):
    root = make_repo(tmp_path, {
        "CLAUDE.md": "@a.md\n@b.md\n",
        "a.md": "@b.md\n@CLAUDE.md\n",
        "b.md": "bbbb\n",
    })
    expect = sum((root / n).stat().st_size for n in ("CLAUDE.md", "a.md", "b.md"))
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert f"{expect:,} バイト" in proc.stdout
    report = run(root, "--report")
    assert "a.md" in report.stdout and "b.md" in report.stdout


def test_ac30_depth_is_limited(tmp_path):
    files = {"CLAUDE.md": "@d1.md\n"}
    for i in range(1, 6):
        files[f"d{i}.md"] = f"@d{i + 1}.md\n" if i < 5 else "x" * 999 + "\n"
    root = make_repo(tmp_path, files)
    proc = run(root)
    assert "999" not in proc.stdout
    declare(root, {"version": 1, "import_depth": 5})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "d")
    deeper = run(root)
    assert deeper.stdout != proc.stdout


def test_ac31_note_when_no_declaration(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert any(line.startswith("NOTE:") and "宣言" in line
               for line in proc.stdout.splitlines())


# --- 指示の数と観点のデータ（AC32〜AC38） ------------------------------------

def test_ac32_instruction_count_is_reported(tmp_path):
    body = (
        "# 見出し\n\n"
        "1 つ目の文である。2 つ目の文である。\n"
        "折り返した続きの文である。\n\n"
        "- 箇条書き 1\n"
        "  - 字下げした箇条書き\n\n"
        "| 表 | の行 |\n| --- | --- |\n\n"
        "> 引用の行\n\n"
        "```\nコードの行\n```\n"
    )
    root = make_repo(tmp_path, {"CLAUDE.md": body})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "指示 6" in proc.stdout


def test_count_instructions_html_and_fences_ignored(module):
    text = (
        "<div class=\"note\">\n"
        "段落の文である。\n"
        "</div>\n"
        "<!-- コメント行 -->\n"
        "<span>1 行だけのタグ</span>\n"
        "~~~\n"
        "# コード内の見出し\n"
        "- コード内の箇条書き\n"
        "コード内の文。\n"
        "~~~\n"
        "```python\n"
        "x = 1\n"
        "```\n"
    )
    # HTML 行と波線・バッククォートのコードブロック内の行は除外され、
    # 通常の段落の文「段落の文である。」のみがカウントされる。
    assert module.count_instructions(text) == 1


def test_count_instructions_punctuation_splitting(module):
    # 各種文末記号（。！？!?）による文分割
    text = "句点である。感嘆符である！疑問符である？ASCII感嘆符!ASCII疑問符?"
    assert module.count_instructions(text) == 5

    # 連続する文末記号があっても空の文はカウントしない
    consecutive = "二重の感嘆符！？\n感嘆符と疑問符!?\n最後の文。"
    assert module.count_instructions(consecutive) == 3


def test_count_instructions_paragraphs_without_punctuation(module):
    # 句点のない単一文段落
    assert module.count_instructions("句点のない単一文段落") == 1

    # 句点のない複数段落
    two_paragraphs = "1 つ目の段落\n\n2 つ目の段落"
    assert module.count_instructions(two_paragraphs) == 2

    # 複数行に折り返された文の結合
    wrapped = "折り返しの 1 行目\n折り返しの 2 行目。\n"
    assert module.count_instructions(wrapped) == 1


def test_count_instructions_headings_and_bullets(module):
    text = (
        "# 見出し 1\n"
        "## 見出し 2\n\n"
        "- ハイフン箇条書き\n"
        "* アスタリスク箇条書き\n"
        "+ プラス箇条書き\n"
        "1. 番号付き箇条書き\n\n"
        "| 表見出し | 列 |\n"
        "| --- | --- |\n\n"
        "> 引用行\n\n"
        "段落の文である。\n"
    )
    # 見出し 2 + 箇条書き 4 + 文 1 = 7（表・引用は除外）
    assert module.count_instructions(text) == 7


def test_ac33_missing_criteria_data_exits_two(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root),
         "--criteria", str(tmp_path / "none.json")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2


def test_ac34_removing_a_criterion_disables_it(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "@docs/none.md\n"})
    data = json.loads((SCRIPT.parent / "data" / "instruction-criteria.json").read_text())
    data["criteria"] = [c for c in data["criteria"] if c["id"] != "broken-import"]
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac35_unknown_criterion_id_exits_two(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    data = json.loads((SCRIPT.parent / "data" / "instruction-criteria.json").read_text())
    data["criteria"].append({"id": "unknown-thing", "aspect": 1, "enforce": "error",
                             "needs": [], "sources": []})
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2


def test_ac36_report_criterion_never_fails(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "x" * 100000 + "\n"})
    declare(root, {"version": 1, "budget": {"bytes": 10}})
    data = json.loads((SCRIPT.parent / "data" / "instruction-criteria.json").read_text())
    for entry in data["criteria"]:
        if entry["id"] == "read-size-budget":
            entry["enforce"] = "report"
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac37_old_criteria_data_emits_note(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    data = json.loads((SCRIPT.parent / "data" / "instruction-criteria.json").read_text())
    data["checked_at"] = "2000-01-01"
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert any(line.startswith("NOTE:") and "2000-01-01" in line
               for line in proc.stdout.splitlines())


def test_ac38_review_interval_and_reviewed_at(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    data = json.loads((SCRIPT.parent / "data" / "instruction-criteria.json").read_text())
    data["checked_at"] = "2000-01-01"
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    declare(root, {"version": 1, "review_interval_days": 99999})
    loose = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert not any("2000-01-01" in line for line in loose.stdout.splitlines())

    declare(root, {"version": 1, "reviewed_at": "2000-01-02"})
    dated = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert any("2000-01-02" in line for line in dated.stdout.splitlines())


# --- 出力と終了コード（AC69〜AC71） ------------------------------------------

def test_ac70_clean_run_prints_counts_and_sizes(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr
    assert "1 本" in proc.stdout
    assert "バイト" in proc.stdout


def test_ac71_unknown_argument_exits_three(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    proc = run(root, "--nope")
    assert proc.returncode == 3
    assert "usage" in proc.stderr.lower() or "使い方" in proc.stderr


# --- 走査の範囲と扱い（AC6〜AC18） -------------------------------------------

def install_script(root: Path) -> Path:
    """スクリプトの実体を一時リポジトリの中へ置く（NDF の開発リポジトリの判定）。"""
    dest = root / "plugins" / "ndf" / "scripts"
    (dest / "lib").mkdir(parents=True, exist_ok=True)
    (dest / "data").mkdir(parents=True, exist_ok=True)
    (dest / "instructions-check.py").write_bytes(SCRIPT.read_bytes())
    (dest / "lib" / "refresh.py").write_bytes((SCRIPT.parent / "lib" / "refresh.py").read_bytes())
    src = SCRIPT.parent / "data" / "instruction-criteria.json"
    (dest / "data" / "instruction-criteria.json").write_bytes(src.read_bytes())
    return dest / "instructions-check.py"


def user_scope(tmp_path: Path, body: str) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "CLAUDE.md").write_text(body, encoding="utf-8")
    return home


def test_ac6_default_scope_is_project_only(tmp_path):
    home = user_scope(tmp_path, "# u\n\n@none.md\n")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"user": [{"path": str(home / "CLAUDE.md")}]}})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "1 本" in proc.stdout


def test_ac7_scope_user_reads_declared_locations(tmp_path):
    home = user_scope(tmp_path, "# u\n\n@none.md\n")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"user": [{"path": str(home / "CLAUDE.md")}]}})
    proc = run(root, "--scope", "project", "--scope", "user")
    assert proc.returncode == 1
    assert any(str(home / "CLAUDE.md") in line for line in errors(proc))


def test_ac7_scope_without_declaration_scans_nothing(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1})
    proc = run(root, "--scope", "project", "--scope", "user", "--scope", "plugins")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "1 本" in proc.stdout


def test_ac8_tilde_and_relative_paths_are_expanded(tmp_path, monkeypatch):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n", "outside/KIRO.md": "# k\n"})
    declare(root, {"version": 1, "scopes": {"user": [{"path": "outside"}]}})
    proc = run(root, "--scope", "user")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "1 本" in proc.stdout


def test_ac9_ac10_plugin_findings_carry_the_distributor(tmp_path):
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "CLAUDE.md").write_text("# p\n\n@none.md\n", encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"plugins": [
        {"path": str(plug), "name": "other", "version": "1.0.0",
         "origin": "acme/other", "update": "claude plugin update other", "ndf": False}]}})
    proc = run(root, "--scope", "plugins")
    assert proc.returncode == 1
    line = errors(proc)[0]
    assert "[報告]" in line
    assert "other" in line and "1.0.0" in line and "acme/other" in line


def test_ac10_ndf_true_becomes_file(tmp_path):
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "CLAUDE.md").write_text("# p\n\n@none.md\n", encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"plugins": [
        {"path": str(plug), "name": "ndf", "version": "1.0.0",
         "origin": "devbasex/ai-plugins", "update": "u", "ndf": True}]}})
    proc = run(root, "--scope", "plugins")
    assert proc.returncode == 1
    assert "[起票]" in errors(proc)[0]


def test_ac11_allowances_are_per_scope_root(tmp_path):
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "CLAUDE.md").write_text("# p\n\n@x.md\n", encoding="utf-8")
    (plug / "x.md").write_text("x\n", encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n\n@x.md\n", "x.md": "x\n"})
    declare(root, {"version": 1,
                   "imports": {"CLAUDE.md": {"x.md": "根の理由"}},
                   "scopes": {"plugins": [{"path": str(plug), "name": "o", "ndf": False,
                                           "imports": {}}]}})
    proc = run(root, "--scope", "project", "--scope", "plugins")
    assert proc.returncode == 1
    lines = errors(proc)
    # プロジェクトの許可はプラグインの `CLAUDE.md` へ効かない。
    assert len(lines) == 1
    assert str(plug / "CLAUDE.md") in lines[0]


def test_ac12_references_resolve_inside_the_scope_root(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "CLAUDE.md").write_text("# u\n\n@memo.md\n", encoding="utf-8")
    (home / "memo.md").write_text("m" * 40, encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"user": [{"path": str(home)}]}})
    proc = run(root, "--scope", "user")
    assert proc.returncode == 0, proc.stderr + proc.stdout
    total = (home / "CLAUDE.md").stat().st_size + 40
    assert f"{total:,} バイト" in proc.stdout


def test_ac13_report_line_carries_criterion_and_sources(tmp_path):
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "CLAUDE.md").write_text("# p\n\n@none.md\n", encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"plugins": [
        {"path": str(plug), "name": "o", "version": "1", "origin": "a/o",
         "update": "u", "ndf": False}]}})
    proc = run(root, "--scope", "plugins")
    line = errors(proc)[0]
    assert "broken-import" in line
    assert "2026-09-17" in line


def test_ac13_issue_title_marker(tmp_path):
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "CLAUDE.md").write_text("# p\n\n@none.md\n", encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "scopes": {"plugins": [
        {"path": str(plug), "name": "ndf", "origin": "devbasex/ai-plugins", "ndf": True}]}})
    proc = run(root, "--scope", "plugins")
    assert "[instructions] broken-import" in errors(proc)[0]


def test_ac14_finding_line_shape(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "@none.md\n"})
    proc = run(root)
    assert errors(proc)[0].startswith("ERROR: [直す] CLAUDE.md:1: ")


def test_ac15_development_repo_makes_everything_fix(tmp_path):
    plug = tmp_path / "plug"
    plug.mkdir()
    (plug / "CLAUDE.md").write_text("# p\n\n@none.md\n", encoding="utf-8")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"}, track=False)
    script = install_script(root)
    declare(root, {"version": 1, "scopes": {"plugins": [
        {"path": str(plug), "name": "o", "ndf": False}]}})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "t")
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(root), "--scope", "plugins"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 1
    assert "[直す]" in proc.stderr


def test_ac18_check_writes_nothing(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "@none.md\n", "AGENTS.md": "# a\n"})
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    run(root)
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert before == after


# --- 宣言があるときの判定（AC48〜AC68） --------------------------------------

def test_ac48_schema_is_ignored(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"$schema": "https://example.invalid/none.json", "version": 1})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac50_unlisted_import_fails_even_when_target_exists(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "@x.md\n", "x.md": "x\n",
                                "docs/CLAUDE.md": "@x.md\n", "docs/x.md": "x\n"})
    declare(root, {"version": 1, "imports": {"CLAUDE.md": {"x.md": "理由"}}})
    proc = run(root)
    assert proc.returncode == 1
    lines = errors(proc)
    assert len(lines) == 1
    assert lines[0].startswith("ERROR: [直す] docs/CLAUDE.md:1: ")


def test_ac51_empty_imports_reports_everything(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "@x.md\n", "x.md": "x\n"})
    declare(root, {"version": 1, "imports": {}})
    assert run(root).returncode == 1
    declare(root, {"version": 1})
    assert run(root).returncode == 0


def test_ac52_stale_allowance_is_reported(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "imports": {"CLAUDE.md": {"gone.md": "理由"}}})
    proc = run(root)
    assert proc.returncode == 1
    assert any("gone.md" in line for line in errors(proc))


def test_ac52_unresolvable_allowance_is_not_reported(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "imports": {"CLAUDE.md": {"~/memo.md": "理由"}}})
    proc = run(root)
    assert proc.returncode == 0, proc.stderr + proc.stdout


CHANGELOG_PATTERN = r"^## \[ndf (?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\]"


def released_decl(**over) -> dict:
    decl = {"version": 1,
            "released": {"source": "changelog", "path": "CHANGELOG.md",
                         "pattern": CHANGELOG_PATTERN},
            "decisions": "docs/decisions.md"}
    decl.update(over)
    return decl


def test_ac53_released_paragraphs_and_headings(tmp_path):
    body = (
        "# c\n\n"
        "## v1.0.0 で決めたこと\n\n"
        "2.0.0 で足した規約である。\n\n"
        "現行の規約である。\n"
    )
    root = make_repo(tmp_path, {
        "CLAUDE.md": body,
        "CHANGELOG.md": "## [ndf 2.0.0] - 2026-01-01\n## [ndf 1.0.0] - 2025-01-01\n",
    })
    declare(root, released_decl())
    proc = run(root)
    assert proc.returncode == 1
    lines = errors(proc)
    assert len(lines) == 2
    assert all("docs/decisions.md" in line for line in lines)


def test_ac54_version_check_is_project_only(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "CLAUDE.md").write_text("1.0.0 の段落である。\n", encoding="utf-8")
    root = make_repo(tmp_path, {
        "CLAUDE.md": "# c\n",
        "CHANGELOG.md": "## [ndf 2.0.0] - 2026-01-01\n",
    })
    declare(root, released_decl(scopes={"user": [{"path": str(home)}]}))
    proc = run(root, "--scope", "project", "--scope", "user")
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_ac55_wrapped_lines_and_list_items(tmp_path):
    body = (
        "# c\n\n"
        "先頭の文である。\n"
        "1.0.0 までを対象とした。\n\n"
        "- 1.0.0 の項目\n"
        "- 1.0.0 の 2 件目\n"
    )
    root = make_repo(tmp_path, {
        "CLAUDE.md": body,
        "CHANGELOG.md": "## [ndf 2.0.0] - 2026-01-01\n",
    })
    declare(root, released_decl())
    proc = run(root)
    assert proc.returncode == 1
    assert len(errors(proc)) == 2


def test_ac56_newer_paragraph_is_not_reported(tmp_path):
    root = make_repo(tmp_path, {
        "CLAUDE.md": "# c\n\n3.0.0 で決めたことである。\n",
        "CHANGELOG.md": "## [ndf 2.0.0] - 2026-01-01\n",
    })
    declare(root, released_decl())
    assert run(root).returncode == 0


def test_ac57_ac58_tags_source_and_maximum(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n\n10.9.0 の段落である。\n"})
    for tag in ("ndf--v10.9.0", "ndf--v10.10.0"):
        git(root, "tag", tag)
    declare(root, {"version": 1, "released": {
        "source": "tags",
        "pattern": r"^ndf--v(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$"}})
    proc = run(root)
    assert proc.returncode == 1
    assert "最新は 10.10.0" in errors(proc)[0]


def test_ac58_suffix_is_older_than_release(tmp_path):
    root = make_repo(tmp_path, {
        "CLAUDE.md": "# c\n",
        "CHANGELOG.md": "## [ndf 10.15.0-dev.1]\n## [ndf 10.15.0]\n",
    })
    declare(root, released_decl())
    proc = run(root, "--report")
    assert proc.returncode == 0, proc.stderr + proc.stdout


@pytest.mark.parametrize("versions,newest", [
    (["10.15.0-dev", "10.15.0-dev.1"], "10.15.0-dev.1"),
    (["10.15.0-dev.2", "10.15.0-dev.10"], "10.15.0-dev.10"),
    (["10.15.0-dev.10", "10.15.0-rc"], "10.15.0-rc"),
])
def test_ac59_prerelease_order(tmp_path, versions, newest):
    log = "".join(f"## [ndf {v}]\n" for v in versions)
    root = make_repo(tmp_path, {
        "CLAUDE.md": "# c\n\n0.1.0 の段落である。\n", "CHANGELOG.md": log,
    })
    declare(root, released_decl())
    proc = run(root)
    assert proc.returncode == 1
    assert f"最新は {newest}" in errors(proc)[0]


def test_ac60_pattern_without_named_group(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n", "CHANGELOG.md": "## [ndf 1.0.0]\n"})
    declare(root, released_decl(released={
        "source": "changelog", "path": "CHANGELOG.md", "pattern": r"^## \[ndf (.+)\]"}))
    assert run(root).returncode == 2


@pytest.mark.parametrize("bad", ["10.15.0-dev..1", "10.15.0-dev.01", "10.15"])
def test_ac61_bad_version_form(tmp_path, bad):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n", "CHANGELOG.md": f"## [ndf {bad}]\n"})
    declare(root, released_decl(released={
        "source": "changelog", "path": "CHANGELOG.md",
        "pattern": r"^## \[ndf (?P<version>[0-9A-Za-z.-]+)\]"}))
    assert run(root).returncode == 2


def test_ac62_released_path_outside_root(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, released_decl(released={
        "source": "changelog", "path": "../outside.md", "pattern": CHANGELOG_PATTERN}))
    assert run(root).returncode == 2


def test_ac63_no_version_stops_before_scanning(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "@none.md\n", "CHANGELOG.md": "# 変更履歴\n"})
    declare(root, released_decl())
    proc = run(root)
    assert proc.returncode == 2
    assert not any("none.md" in line for line in errors(proc))


def test_ac64_pending_marker(tmp_path):
    body = "# c\n\n2.0.0 の次の版で決めたことである。\n\n1.0.0 の次の版で決めたことである。\n"
    root = make_repo(tmp_path, {
        "CLAUDE.md": body, "CHANGELOG.md": "## [ndf 2.0.0]\n"})
    declare(root, released_decl(pending_marker="の次の版で"))
    proc = run(root)
    assert proc.returncode == 1
    lines = errors(proc)
    assert len(lines) == 1
    assert "1.0.0" in lines[0]


def test_ac65_finding_names_the_decisions_target(tmp_path):
    root = make_repo(tmp_path, {
        "CLAUDE.md": "# c\n\n1.0.0 の段落である。\n", "CHANGELOG.md": "## [ndf 2.0.0]\n"})
    declare(root, released_decl())
    assert "docs/decisions.md" in errors(run(root))[0]


def test_ac66_budget_is_per_root_file(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "x" * 500 + "\n", "AGENTS.md": "a\n"})
    declare(root, {"version": 1, "budget": {"bytes": 100}})
    proc = run(root)
    assert proc.returncode == 1
    lines = errors(proc)
    assert len(lines) == 1
    assert "CLAUDE.md" in lines[0]


@pytest.mark.parametrize("decl", [
    "not json",
    json.dumps({"files": ["AGENTS.md"]}),
    json.dumps({"version": 99}),
    json.dumps({"version": 1, "files": "AGENTS.md"}),
    json.dumps({"version": 1, "released": {"source": "changelog", "path": "c.md",
                                           "pattern": "(?P<version>["}}),
    json.dumps({"version": 1, "pending_marker": ""}),
])
def test_ac67_broken_declaration(tmp_path, decl):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    path = root / ".ndf" / "instructions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(decl, encoding="utf-8")
    assert run(root).returncode == 2


@pytest.mark.parametrize("over", [
    {"import_depth": 0},
    {"refresh_timeout_seconds": -1},
    {"review_interval_days": 0},
    {"budget": {"bytes": 0}},
    {"reviewed_at": "きのう"},
])
def test_ac68_out_of_range_values(tmp_path, over):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, **over})
    assert run(root).returncode == 2


def test_ac69_finding_without_a_line(tmp_path):
    root = make_repo(tmp_path, {"AGENTS.md": "# a\n", "CLAUDE.md": "# c\n"})
    declare(root, {"version": 1, "imports": {"AGENTS.md": {"CLAUDE.md": "理由"}}})
    proc = run(root)
    assert proc.returncode == 1
    assert errors(proc)[0].startswith("ERROR: [直す] AGENTS.md: ")


# --- 調べ直し（AC39〜AC47） --------------------------------------------------

import importlib.util  # noqa: E402
import time  # noqa: E402


@pytest.fixture()
def module():
    spec = importlib.util.spec_from_file_location("ndf_instructions_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclass の注釈を解くために、実行の前に sys.modules へ登録する。
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        # `refresh` は sys.modules に残るため、差し替えた `fetch` を必ず戻す。
        original = mod.refresh_lib.fetch
        try:
            yield mod
        finally:
            mod.refresh_lib.fetch = original
    finally:
        sys.modules.pop(spec.name, None)


class FakeResponse:
    def __init__(self, body: bytes, delay: float = 0.0, forever: bool = False):
        self._body = body
        self._delay = delay
        self._forever = forever
        self._done = False

    def read(self, size: int) -> bytes:
        if self._delay:
            time.sleep(self._delay)
        if self._forever:
            return b"x" * 16
        if self._done:
            return b""
        self._done = True
        return self._body

    def close(self) -> None:
        pass


def criteria_file(tmp_path: Path, **over) -> Path:
    data = json.loads((SCRIPT.parent / "data" / "instruction-criteria.json").read_text())
    data.update(over)
    path = tmp_path / "criteria.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_ac39_ac43_refresh_lists_one_row_per_source(module, tmp_path, capsys):
    path = criteria_file(tmp_path, sources=[
        {"id": "a", "name": "出典 A", "url": "https://example.invalid/a",
         "checked_at": "2026-09-17", "claim": "主張 A"},
        {"id": "b", "name": "出典 B", "url": "https://example.invalid/b",
         "checked_at": "2026-09-01", "claim": "主張 B"},
    ])
    module.refresh_lib.fetch = lambda url, timeout, opener=None: module.refresh_lib.FetchResult(
        url=url, ok=True, fingerprint="sha256:x")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    code = module.main(["--root", str(root), "--refresh", "--criteria", str(path)])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert len(out) == 2
    assert "出典 A" in out[0] and "2026-09-17" in out[0] and "主張 A" in out[0]
    assert "出典 B" in out[1]


def test_ac42_fingerprint_comparison(module, tmp_path, capsys):
    path = criteria_file(tmp_path, sources=[
        {"id": "same", "name": "同じ", "url": "u1", "claim": "c",
         "fingerprint": "sha256:known"},
        {"id": "diff", "name": "違う", "url": "u2", "claim": "c",
         "fingerprint": "sha256:other"},
        {"id": "none", "name": "記録なし", "url": "u3", "claim": "c"},
    ])
    module.refresh_lib.fetch = lambda url, timeout, opener=None: module.refresh_lib.FetchResult(
        url=url, ok=True, fingerprint="sha256:known")
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    module.main(["--root", str(root), "--refresh", "--criteria", str(path)])
    out = capsys.readouterr().out.splitlines()
    assert "変わっていない" in out[0]
    assert "変わった" in out[1]
    assert "前回の記録が無い" in out[2]


def test_ac40_timeout_priority(module, tmp_path, capsys):
    seen: list[float] = []
    path = criteria_file(tmp_path, sources=[
        {"id": "a", "name": "A", "url": "u", "claim": "c"}])

    def spy(url, timeout, opener=None):
        seen.append(timeout)
        return module.refresh_lib.FetchResult(url=url, ok=True, fingerprint="f")

    module.refresh_lib.fetch = spy
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    module.main(["--root", str(root), "--refresh", "--criteria", str(path)])
    assert seen[-1] == module.refresh_lib.DEFAULT_TIMEOUT_SECONDS

    declare(root, {"version": 1, "refresh_timeout_seconds": 3})
    module.main(["--root", str(root), "--refresh", "--criteria", str(path)])
    assert seen[-1] == 3

    module.main(["--root", str(root), "--refresh", "--criteria", str(path),
                 "--refresh-timeout", "1"])
    assert seen[-1] == 1
    capsys.readouterr()


def test_ac41_timeout_is_total_elapsed():
    sys.path.insert(0, str(SCRIPT.parent / "lib"))
    import refresh as refresh_lib

    started = time.monotonic()
    result = refresh_lib.fetch(
        "https://example.invalid/slow", 0.3,
        opener=lambda url, timeout: FakeResponse(b"", delay=0.02, forever=True))
    elapsed = time.monotonic() - started
    assert not result.ok
    assert "待ち" in (result.error or "")
    assert elapsed < 2.0


@pytest.mark.parametrize(("failure", "expected_error"), [
    (urllib.error.HTTPError("https://example.invalid", 503, "unavailable", {}, None),
     "HTTP 503"),
    (urllib.error.URLError("name resolution failed"),
     "接続できない（name resolution failed）"),
])
def test_fetch_returns_the_opener_error(failure, expected_error):
    sys.path.insert(0, str(SCRIPT.parent / "lib"))
    import refresh as refresh_lib

    def failing_opener(url, timeout):
        raise failure

    result = refresh_lib.fetch(
        "https://example.invalid/source", 1, opener=failing_opener)

    assert result.ok is False
    assert result.error == expected_error


def test_ac44_refresh_writes_nothing(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    path = criteria_file(tmp_path, sources=[
        {"id": "a", "name": "A", "url": "http://127.0.0.1:1/a", "claim": "c"}])
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    before[path] = path.stat().st_mtime_ns
    subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--refresh",
         "--criteria", str(path), "--refresh-timeout", "1"],
        capture_output=True, text=True,
    )
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    after[path] = path.stat().st_mtime_ns
    assert before == after


def test_ac45_ac46_unreachable_refresh_exits_two_but_check_is_zero(tmp_path):
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    path = criteria_file(tmp_path, sources=[
        {"id": "a", "name": "届かない", "url": "http://127.0.0.1:1/a", "claim": "c"}])
    refreshed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--refresh",
         "--criteria", str(path), "--refresh-timeout", "2"],
        capture_output=True, text=True,
    )
    assert refreshed.returncode == 2
    assert "取得できなかった" in refreshed.stdout
    assert "届かない" in refreshed.stdout

    checked = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--criteria", str(path)],
        capture_output=True, text=True,
    )
    assert checked.returncode == 0, checked.stderr


def test_ac47_partial_failure_lists_all_and_exits_two(module, tmp_path, capsys):
    path = criteria_file(tmp_path, sources=[
        {"id": "ok", "name": "取れる", "url": "u1", "claim": "c"},
        {"id": "ng", "name": "取れない", "url": "u2", "claim": "c"},
    ])

    def half(url, timeout, opener=None):
        if url == "u1":
            return module.refresh_lib.FetchResult(url=url, ok=True, fingerprint="f")
        return module.refresh_lib.FetchResult(url=url, ok=False, error="理由")

    module.refresh_lib.fetch = half
    root = make_repo(tmp_path, {"CLAUDE.md": "# c\n"})
    code = module.main(["--root", str(root), "--refresh", "--criteria", str(path)])
    out = capsys.readouterr().out.splitlines()
    assert code == 2
    assert len(out) == 2
    assert "取れる" in out[0] and "取れない" in out[1]


# --- 呼び方（AC74 のブロックの終了コード） -----------------------------------

BLOCK = """
python3 "$SCRIPTS/instructions-check.py" --root "$ROOT" ${EXTRA:-}
rc=$?
echo "exit=$rc"
(exit "$rc")
"""


@pytest.mark.parametrize("files,decl,extra,expected", [
    ({"CLAUDE.md": "# c\n"}, None, "", 0),
    ({"CLAUDE.md": "@none.md\n"}, None, "", 1),
    ({"CLAUDE.md": "# c\n"}, {"version": 99}, "", 2),
    ({"CLAUDE.md": "# c\n"}, None, "--nope", 3),
])
def test_ac74_block_keeps_the_exit_code(tmp_path, files, decl, extra, expected):
    root = make_repo(tmp_path, files)
    if decl is not None:
        path = root / ".ndf" / "instructions.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(decl), encoding="utf-8")
    proc = subprocess.run(
        ["bash", "-c", BLOCK],
        capture_output=True, text=True,
        env={**os.environ, "SCRIPTS": str(SCRIPT.parent), "ROOT": str(root),
             "EXTRA": extra},
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr
    assert f"exit={expected}" in proc.stdout
