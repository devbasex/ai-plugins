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
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "checkout", "-q", "-b", "feature/x")
    return root


def lint(root, *args):
    p = subprocess.run([PY, str(SCRIPTS / "doc-lint.py"), "--base", "develop", *args], capture_output=True,
                       text=True, cwd=root)
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
    write(repo, ".ndf/glossary.json", json.dumps({"version": 1, "format": "json",
                                                   "source": "docs/g.json", "document": "docs/glossary.md"}))
    write(repo, "docs/glossary.md", "以前は別の語だった。\n")
    write(repo, "docs/d.md", "以前は別の語だった。\n")
    code, out, _ = lint(repo)
    assert code == 1 and {i["name"] for i in out["items"]} == {"docs/d.md:1"}
    code, out, _ = lint(repo, "--exclude", "issues/")
    assert {i["name"] for i in out["items"]} == {"docs/d.md:1"}


def test_generated_document_is_matched_exactly_after_normalizing(repo):
    # 生成物は完全一致で外す。`./` 付きの宣言も git diff のパスと揃え、同じ名前で始まる別の文書は見る
    write(repo, ".ndf/glossary.json", json.dumps({"version": 1, "format": "json",
                                                   "source": "docs/g.json", "document": "./docs/glossary.md"}))
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
