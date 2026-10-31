"""共有が前提の宣言が .gitignore の対象なら、書く・確かめるコマンドがそれを出す（#2157）。

一時のリポジトリでルートの .gitignore に `.ndf/` を書き、project-mvv.py approve / check・glossary.py init を打つ。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from test_project_mvv import approve, env, run  # noqa: F401  （fixture の env を使う）

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import ndf_tracking  # noqa: E402


def exclude_ndf(root: Path) -> None:
    (root / ".gitignore").write_text("node_modules/\n.ndf/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "除外"], cwd=root, check=True)


def notes(out: dict) -> list[dict]:
    return [it for it in out["items"] if it.get("key") == "gitignore"]


def test_approve_reports_the_ignoring_line_and_both_files(env):  # noqa: F811
    exclude_ndf(env["root"])
    code, out, _ = approve_out(env)
    assert code == 0 and out["status"] == "ok"
    hits = notes(out)
    assert sorted(h["name"] for h in hits) == [".ndf/mvv.json", ".ndf/mvv.md"]
    assert {(h["source"], h["pattern"], h["result"]) for h in hits} == {(".gitignore:2", ".ndf/", "ignored")}
    assert ".gitignore:2" in out["summary"] and "コミットできない" in out["summary"]


def test_check_reports_the_same_rows(env):  # noqa: F811
    exclude_ndf(env["root"])
    assert approve(env) == 0
    code, out, _ = run(env, "check", "--root", str(env["root"]))
    assert code == 0 and out["status"] == "ok"
    assert sorted(h["name"] for h in notes(out)) == [".ndf/mvv.json", ".ndf/mvv.md"]


def test_tracked_ndf_with_only_the_local_file_ignored_has_no_note(env):  # noqa: F811
    root = env["root"]
    (root / ".ndf").mkdir()
    (root / ".ndf" / ".gitignore").write_text("worktree.local.json\n")
    subprocess.run(["git", "add", ".ndf/.gitignore"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "標準"], cwd=root, check=True)
    code, out, _ = approve_out(env)
    assert code == 0 and out["status"] == "ok" and notes(out) == []
    assert "コミットできない" not in out["summary"]


def approve_out(env):  # noqa: F811
    from test_project_mvv import FOLLOW, answer, body_file

    b = body_file(env)
    answer(env, FOLLOW)
    assert run(env, "vet", "--body", b, "--kind", "candidate", "--root", str(env["root"]))[0] == 0
    return run(env, "approve", "--body", b, "--by", "利用者", "--root", str(env["root"]))


def test_glossary_init_reports_the_ignored_declaration(env):  # noqa: F811
    exclude_ndf(env["root"])
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "glossary.py"), "init", "--root", str(env["root"])],
        capture_output=True,
        text=True,
        cwd=env["root"],
    )
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 0 and out["status"] == "ok"
    assert [h["name"] for h in notes(out)] == [".ndf/glossary.json"]
    assert {it["name"] for it in out["items"] if it.get("result") == "created"} >= {".ndf/glossary.json"}
    assert "コミットできない" in out["summary"]


def test_outside_git_and_negated_lines_are_not_reported(tmp_path):
    assert ndf_tracking.ignored(tmp_path, [".ndf/mvv.md"]) == []
    root = tmp_path / "r"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text("*.json\n!keep.json\n")
    assert [h["path"] for h in ndf_tracking.ignored(root, ["a.json", "keep.json"])] == ["a.json"]
