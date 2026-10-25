"""doc-lint.py（#870 B2）: 起点からの追加行だけに書き方のチェックを掛ける。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    write(root, "docs/a.md", "# 題\n\n以前は別の形だった。\n\n今の決まり。\n")
    write(root, "CHANGELOG.md", "# 変更\n")
    # 文体の規則は宣言で掛ける（ndf_policies.doc_lint）。掛けない場合は test_project_decl.py が見る
    write(root, ".ndf/project.json", json.dumps({"version": 1, "ndf_policies": {"doc_lint": True, "reject_md_wording_tests": True}}))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "checkout", "-q", "-b", "feature/x")
    return root


def lint(root, *args):
    p = subprocess.run([PY, str(SCRIPTS / "doc-lint.py"), "--base", "develop", *args], capture_output=True, text=True, cwd=root)
    lines = p.stdout.strip().splitlines()
    return p.returncode, (json.loads(lines[-1]) if lines else None), p.stderr


def test_no_added_lines_is_ok(repo):
    code, out, err = lint(repo)
    assert code == 0, err
    assert out["tool"] == "doc-lint" and out["status"] == "ok" and out["metrics"]["hits"] == 0


def test_only_added_lines_are_checked(repo):
    write(repo, "docs/a.md", "# 題\n\n以前は別の形だった。\n\n今の決まり。\n\n新しい節（#980 の差し戻し）。\n")
    write(repo, "docs/b.md", "# 新規\n\n当初は A 案だったが今回 B に変更した。\n\n```bash\ngrep 以前は x\n```\n\nこの値によらず動く。\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "docs")
    code, out, _ = lint(repo)
    assert code == 1 and out["status"] == "stopped"
    names = {(i["name"], i["rule"]) for i in out["items"]}
    assert ("docs/a.md:7", "issue-origin") in names
    assert ("docs/b.md:3", "history") in names
    assert ("docs/b.md:9", "comparison") in names
    assert not any(n.startswith("docs/a.md:3") for n, _ in names)  # 起点に元からある行は見ない
    assert not any(n == "docs/b.md:6" for n, _ in names)  # コードブロックの中は見ない
    assert out["metrics"]["files"] == 2 and out["next"]


def test_longer_fence_is_not_closed_by_a_shorter_one(repo):
    """囲みは CommonMark の規則で閉じる（lib/md.py）。```` の中の ``` の行では閉じない（3 文字の前置きで閉じていた頃は、
    その後ろの例を地の文として見た）。"""
    write(repo, "docs/c.md", "# 例\n\n````md\n```bash\n```\n以前は x だった。\n````\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "docs")
    code, out, err = lint(repo)
    assert code == 0, (out, err)


def test_uncommitted_changes_and_excludes(repo):
    write(repo, "CHANGELOG.md", "# 変更\n\n以前は壊れていた。\n")
    write(repo, "issues/h.md", "従来の形。\n")
    write(repo, "docs/c.md", "従来の形。\n")
    code, out, _ = lint(repo)
    assert code == 1
    assert [i["name"] for i in out["items"]] == ["docs/c.md:1"]
    code, out, _ = lint(repo, "--exclude", "docs/")
    assert code == 1 and {i["name"].split(":")[0] for i in out["items"]} == {"CHANGELOG.md", "issues/h.md"}


def test_base_from_declaration(repo):
    # --base が無ければ .ndf/worktree.json の base_branch（origin/<名前>）を起点にする
    git(repo, "remote", "add", "origin", str(repo))
    git(repo, "fetch", "-q", "origin")
    write(repo, ".ndf/worktree.json", '{"version": 1, "base_branch": "develop"}\n')
    write(repo, "docs/c.md", "# 新規\n\n以前は別だった。\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "docs")
    p = subprocess.run([PY, str(SCRIPTS / "doc-lint.py")], capture_output=True, text=True, cwd=repo)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 1 and out["metrics"]["base"] == "origin/develop", p.stderr


def test_no_base_is_unreadable(repo):
    p = subprocess.run([PY, str(SCRIPTS / "doc-lint.py")], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 2 and "base_branch" in json.loads(p.stdout.strip().splitlines()[-1])["summary"]


def test_unknown_base_is_unreadable(repo):
    p = subprocess.run([PY, str(SCRIPTS / "doc-lint.py"), "--base", "nope"], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 2 and json.loads(p.stdout.strip().splitlines()[-1])["status"] == "stopped"


def test_generated_glossary_document_is_skipped(repo):
    # 用語集の設定の document は glossary.py render の生成物。直すなら正本を直すため見ない
    write(repo, ".ndf/glossary.json", json.dumps({"version": 1, "format": "json", "source": "docs/g.json", "document": "docs/glossary.md"}))
    write(repo, "docs/glossary.md", "以前は別の語だった。\n")
    write(repo, "docs/d.md", "以前は別の語だった。\n")
    code, out, _ = lint(repo)
    assert code == 1 and {i["name"] for i in out["items"]} == {"docs/d.md:1"}
    code, out, _ = lint(repo, "--exclude", "issues/")
    assert {i["name"] for i in out["items"]} == {"docs/d.md:1"}


def test_generated_document_is_matched_exactly_after_normalizing(repo):
    # 生成物は完全一致で外す。`./` 付きの宣言も git diff のパスと揃え、同じ名前で始まる別の文書は見る
    write(
        repo, ".ndf/glossary.json", json.dumps({"version": 1, "format": "json", "source": "docs/g.json", "document": "./docs/glossary.md"})
    )
    write(repo, "docs/glossary.md", "以前は別の語だった。\n")
    write(repo, "docs/glossary.md-notes.md", "以前は別の語だった。\n")
    code, out, _ = lint(repo)
    assert code == 1 and {i["name"] for i in out["items"]} == {"docs/glossary.md-notes.md:1"}


def test_unreadable_glossary_config_is_ignored(repo, monkeypatch):
    # 設定が読めない（OSError）ときは生成物の宣言が無いものとして続け、例外で止まらない
    import importlib.util

    spec = importlib.util.spec_from_file_location("ndf_doc_lint", SCRIPTS / "doc-lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    write(repo, ".ndf/glossary.json", "{}")

    def deny(self, *a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", deny)
    assert mod.generated_documents(repo) == ()


# --- 現状固定: lint_files の分岐ごとの出力 ------------------------------------------------
# 期待値は現在の実装の出力をそのまま写したもの（仕様の主張ではない）。構造を変えたときに出力が変わったことを検出する。


def _load_doc_lint():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ndf_doc_lint_fixed", SCRIPTS / "doc-lint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LONG_LINE = "従来" + "あ" * 250


@pytest.fixture
def lint_tree(tmp_path):
    write(tmp_path, "docs/b.md", "# 題\n\n今回は以前と違う（#12 の指摘）。\n\n```\n以前は x\n```\n" + LONG_LINE + "\n  末尾は   \n")
    write(tmp_path, "a.md", "従来の形。\n")
    return tmp_path


def _hit(name, rule, match, text):
    return {"kind": "line", "name": name, "result": "hit", "rule": rule, "match": match, "text": text}


EXPECTED_HITS = [
    _hit("a.md:1", "comparison", "従来", "従来の形。"),
    _hit("docs/b.md:3", "history", "今回", "今回は以前と違う（#12 の指摘）。"),
    _hit("docs/b.md:3", "issue-origin", "（#12 の指摘）", "今回は以前と違う（#12 の指摘）。"),
    _hit("docs/b.md:3", "comparison", "以前", "今回は以前と違う（#12 の指摘）。"),
    _hit("docs/b.md:8", "comparison", "従来", LONG_LINE[:200]),
]


def test_lint_files_given_lines(lint_tree):
    """指定行だけを見る。存在しないファイル・囲みの中の行（6）・行数を超える番号（99）は数えない。ファイルはパス順、1 行の規則は RULES の順。"""
    mod = _load_doc_lint()
    items, total = mod.lint_files(lint_tree, {"docs/b.md": {1, 3, 6, 8, 9, 99}, "a.md": {1}, "nope.md": {1}}, False)
    assert total == 5
    assert items == EXPECTED_HITS


def test_lint_files_all_lines_ignores_given_numbers(lint_tree):
    """all_lines は指定行を無視して全行を見る（囲みの 3 行は数えない）。"""
    mod = _load_doc_lint()
    items, total = mod.lint_files(lint_tree, {"docs/b.md": set(), "a.md": set(), "nope.md": {1}}, True)
    assert total == 7
    assert items == EXPECTED_HITS


def test_lint_files_empty(lint_tree):
    mod = _load_doc_lint()
    assert mod.lint_files(lint_tree, {}, False) == ([], 0)
    assert mod.lint_files(lint_tree, {"a.md": set()}, False) == ([], 0)
