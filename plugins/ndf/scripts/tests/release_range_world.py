"""release-steps.py の範囲のテストに共通する一時リポジトリ・PR の応答・コマンドの実行・承認資料の読み取り。

一時の git リポジトリ（origin は bare。ベースブランチは trunk・本番チャネルは live）に正式版のタグ foo--v1.0.0 を打ち、
ベースブランチへ直接入った PR（#21 #22）、スプリントブランチへ実装 PR（#31 #32）を merge commit で集めたスプリント PR（#30）、
版上げの PR を入れる。GitHub の読み取り（PR の中身・CI・squash の head）だけを差し替える（`load`）。
`test_release_range.py`・`test_release_materials.py`・`test_release_section_end.py` が使う。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_repo import seed_release_repo  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "release-steps.py"
VER = "1.0.1"
CH = "## 利用者向けの変化\n\n"
BODIES = {
    21: CH + "- 直接の変化\n\n## 移行の手順\n\n- bar を入れ直す\n\n## 未検証・残る危険\n\n- 直接の PR は実機で未確認\n",
    22: CH + "- なし\n",
    23: CH + "- 無し\n",
    24: "節の無い本文\n",
    30: CH + "- 実装 1 の変化（#31）\n",
    31: CH + "- 実装 1 の変化\n\n## 移行の手順\n\n- 実装 1 の手順\n\n## 未検証・残る危険\n\n- 実装 1 の危険\n",
    32: CH + "- 無し\n",
    50: CH + "- dev.2 の変化\n",
}


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def commit_file(root: Path, name: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{name}\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"change {name}")


def merge_into(root: Path, n: int, branch: str, into: str, name: str | None = None) -> None:
    git(root, "checkout", "-q", "-b", branch, into)
    commit_file(root, name or f"f{n}.txt")
    git(root, "checkout", "-q", into)
    git(root, "merge", "--no-ff", "-q", branch, "-m", f"Merge pull request #{n} from o/{branch}")


def add_sprint(root: Path) -> None:
    """スプリントブランチへ実装 PR 31・32 を merge commit で入れ、スプリント PR 30 を trunk へ入れる。"""
    git(root, "branch", "sprint/m1", "trunk")
    merge_into(root, 31, "feat/i1", "sprint/m1")
    merge_into(root, 32, "feat/i2", "sprint/m1")
    git(root, "checkout", "-q", "trunk")
    git(root, "merge", "--no-ff", "-q", "sprint/m1", "-m", "Merge pull request #30 from o/sprint/m1")


def make_world(tmp_path: Path) -> dict:
    origin, seed = seed_release_repo(tmp_path)
    (seed / "CHANGELOG.md").write_text("# Changelog\n\n## [foo 1.0.0] - 2026-10-01\n\n- 前の版\n", encoding="utf-8")
    commit_file(seed, "plugins/foo/.claude-plugin/plugin.json")
    commit_file(seed, "plugins/playwright-kit/.claude-plugin/plugin.json")
    (seed / "plugins/playwright-kit/.claude-plugin/plugin.json").write_text(json.dumps({"version": "2.0.6"}))
    (seed / "plugins/foo/.claude-plugin/plugin.json").write_text(json.dumps({"version": "1.0.0"}))
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "HEAD:trunk", "HEAD:live")
    git(seed, "tag", "foo--v1.0.0")
    git(seed, "push", "-q", "origin", "foo--v1.0.0")
    merge_into(seed, 21, "feat/direct", "trunk", "plugins/playwright-kit/skills/x.md")  # develop へ直接（移行の手順あり）
    merge_into(seed, 22, "spec/none", "trunk")  # 利用者向けの変化なし（「なし」）
    add_sprint(seed)
    commit_file(seed, "release.txt")
    git(seed, "commit", "-q", "--amend", "-m", "release: v1.0.1-dev.1 (#40)")
    git(seed, "push", "-q", "origin", "HEAD:trunk")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "user.name", "t")
    return {"seed": seed, "work": work, "present": tmp_path / "present"}


def merge_oid(root: Path, n: int) -> str:
    return git(root, "log", "--all", "--format=%H", f"--grep=Merge pull request #{n} from") or f"{n:040d}"


def load(monkeypatch, w: dict, bodies=None):
    spec = importlib.util.spec_from_file_location("release_steps_1885", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "release_steps_1885", mod)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "emit", lambda obj, code=None, **k: (_ for _ in ()).throw(SystemExit((obj, code))))
    monkeypatch.setenv("NDF_PRESENTATION_DIR", str(w["present"]))
    monkeypatch.setattr(mod.repo_lib, "owner_repo", lambda r: "o/n")
    bodies = {**BODIES, **(bodies or {})}
    reads = []

    def pr_fields(ns, cwd=None, comments=False):
        reads.append(list(ns))
        return {
            n: {
                "number": n,
                "title": f"題 {n}",
                "state": "MERGED",
                "body": bodies.get(n, ""),
                "url": f"https://github.com/o/n/pull/{n}",
                "mergeCommit": {"oid": merge_oid(w["work"], n)},
                "closingIssuesReferences": [],
                **({"comments": []} if comments else {}),
            }
            for n in ns
        }, ""

    def no_view(root, n, fields):
        raise AssertionError(f"PR ごとの読み出し #{n}")

    monkeypatch.setattr(mod.included, "pr_fields", pr_fields)
    monkeypatch.setattr(mod, "pr_view", no_view)
    monkeypatch.setattr(
        mod.included.merged_range.gh_rest, "pr_head_branches", lambda ns, cwd=None: ({n: "release/v1.0.1-dev.1" for n in ns}, "")
    )
    monkeypatch.setattr(mod, "pr_check_buckets", lambda r, n: [{"bucket": "pass"}])
    mod.reads = reads
    return mod


def call(fn, **kw):
    with pytest.raises(SystemExit) as e:
        fn(argparse.Namespace(**kw))
    return e.value.code


def changelog(mod, w, prs, version=VER):
    return call(mod.cmd_changelog, root=str(w["work"]), version=version, prs=list(prs), plugin=None)


def notes(mod, w, prs, approval=None, version=VER, check_state=None):
    kw = dict(root=str(w["work"]), version=version, prs=list(prs), plugin=None, approval=approval, verified="", ref="trunk")
    return call(mod.cmd_notes, **kw, check_state=check_state)


def facts(mod, w, prs):
    return call(mod.cmd_approval_facts, root=str(w["work"]), version=VER, prs=list(prs), prev_tag=None, plugin=None, approval=None)


def section(w) -> str:
    text = (w["work"] / "CHANGELOG.md").read_text(encoding="utf-8")
    start = text.index("## [foo 1.0.1]")
    return text[start : text.index("## [foo 1.0.0]")]


def included_rows(path) -> list[str]:
    for line in Path(path).read_text(encoding="utf-8").split("\n"):
        if line.startswith("| 含む PR |"):
            return line.split(" | ", 1)[1].rstrip(" |").split("<br>")
    raise AssertionError("含む PR の欄が無い")


def cell(path, key) -> str:
    return next(line for line in Path(path).read_text(encoding="utf-8").split("\n") if line.startswith(f"| {key} |"))


def numbers(rows) -> list[int]:
    return [int(r.split()[0][1:]) for r in rows]
