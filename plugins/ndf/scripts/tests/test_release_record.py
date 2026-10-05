"""release-steps.py record と、リリース記録の書く側・読む側の契約（#1273）。

本番の配布の後に、本番のリリースの PR（release/v<版> → ベースブランチ）へリリース記録をコメントで書く。
gh は PATH の先頭に置いた偽物で置き換え、git は origin（裸のリポジトリ）を持つ実物を使う。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
PY = sys.executable
sys.path.insert(0, str(SCRIPTS / "lib"))
import deps  # noqa: E402

deps.require("md")
import dist_record  # noqa: E402

_spec = importlib.util.spec_from_file_location("sprint_close_harness_1273", HERE / "test_sprint_close_merge_green.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

FAKE_GH = r"""#!{py}
import json, os, sys
a = sys.argv[1:]
path = os.environ["FAKE_GH_STATE"]
st = json.load(open(path, encoding="utf-8"))
st.setdefault("calls", []).append(a)
out, code = None, 0
if a[:2] == ["pr", "list"]:
    head, base = a[a.index("--head") + 1], a[a.index("--base") + 1]
    out = json.dumps([p for p in st.get("prs", []) if p["head"] == head and p["base"] == base])
elif a[:2] == ["release", "view"]:
    code = 0 if a[2] in st.get("releases", []) else 1
    out = json.dumps({{"tagName": a[2]}}) if code == 0 else None
elif a[:2] == ["pr", "view"]:
    rec = st.get("records", {{}}).get(a[2])
    out, code = (json.dumps(rec), 0) if rec is not None else (None, 1)
elif a[:2] == ["pr", "comment"]:
    code = st.get("comment_code", 0)
    if code == 0:
        body = open(a[a.index("--body-file") + 1], encoding="utf-8").read()
        st["records"][a[2]]["comments"].append({{"body": body}})
else:
    code = 1
json.dump(st, open(path, "w", encoding="utf-8"), ensure_ascii=False)
if out is not None:
    print(out)
if code:
    sys.stderr.write("gh failed\n")
sys.exit(code)
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """origin に ndf--v1.2.2・ndf--v1.2.3-dev.1・ndf--v1.2.3 のタグを持つ作業場所（宣言は develop / main / ndf）。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    for args in (
        ["init", "-q", "-b", "develop"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["config", "commit.gpgsign", "false"],
        ["config", "tag.gpgsign", "false"],
    ):
        git(root, *args)
    (root / ".ndf").mkdir()
    (root / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    (root / ".ndf" / "supervise.json").write_text(
        json.dumps({"release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}})
    )
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "remote", "add", "origin", str(origin))
    for tag in ("ndf--v1.2.2", "ndf--v1.2.3-dev.1", "ndf--v1.2.3"):
        git(root, "tag", "-a", tag, "-m", tag)
    git(root, "push", "-q", "origin", "develop", "--tags")
    return root


@pytest.fixture
def gh(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    f = bindir / "gh"
    f.write_text(FAKE_GH.format(py=PY), encoding="utf-8")
    f.chmod(0o755)
    state = tmp_path / "gh-state.json"
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env.update(PATH=f"{bindir}{os.pathsep}{env['PATH']}", FAKE_GH_STATE=str(state))

    class G:
        def set(self, **kw):
            base = {
                "prs": [{"number": 40, "state": "MERGED", "head": "release/v1.2.3", "base": "develop"}],
                "releases": ["ndf--v1.2.3"],
                "records": {"40": {"body": "ndf v1.2.3 のリリース（prod）", "comments": [], "url": "https://github.com/o/r/pull/40"}},
            }
            state.write_text(json.dumps({**base, **kw}, ensure_ascii=False), encoding="utf-8")

        def get(self):
            return json.loads(state.read_text(encoding="utf-8"))

        def comments(self):
            return [c["body"] for c in self.get()["records"]["40"]["comments"]]

    g = G()
    g.env = env
    g.set()
    return g


def record(root: Path, env: dict, *extra: str) -> tuple[int, dict, str]:
    p = subprocess.run(
        [PY, str(SCRIPTS / "release-steps.py"), "record", "--root", str(root), "--version", "1.2.3", "--prs", "11", "12", *extra],
        capture_output=True,
        text=True,
        env=env,
    )
    out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    return p.returncode, out, p.stderr


def test_format_record_is_read_back_by_parse_record():
    """AC2・AC7: 書く側の出力を読む側に通すと、本番・出した版（v なし）・受けた PR の並びが読める。"""
    text = dist_record.format_record("1.2.2", "1.2.3", [11, 12], stage_note="承認の後", version_note="タグ ndf--v1.2.3")
    got = dist_record.parse_record(text)
    assert got["found"] and got["stage"].startswith("本番")
    assert (got["version"], got["sprint_prs"]) == ("1.2.3", [11, 12])


def test_record_posts_one_comment_to_the_release_pr(repo, gh):
    """AC2・AC3: タグと GitHub Release があれば、本番のリリースの PR へ記録を 1 件書き、直前の正式版はタグから取る。"""
    code, out, err = record(repo, gh.env)
    assert code == 0, (out, err)
    assert [i["result"] for i in out["items"]] == ["posted"]
    assert out["metrics"]["release_pr_url"] == "https://github.com/o/r/pull/40"
    assert out["metrics"]["prev_version"] == "1.2.2"
    (body,) = gh.comments()
    got = dist_record.parse_record(body)
    assert got["found"] and got["stage"].startswith("本番")
    assert (got["version"], got["sprint_prs"]) == ("1.2.3", [11, 12])
    assert "1.2.2 → 1.2.3" in body


def test_record_twice_posts_only_once(repo, gh):
    """I3: 同じ記録が既にあれば書かずに exists を返す。"""
    assert record(repo, gh.env)[0] == 0
    code, out, _ = record(repo, gh.env)
    assert code == 0 and [i["result"] for i in out["items"]] == ["exists"]
    assert len(gh.comments()) == 1


@pytest.mark.parametrize("missing", ["tag", "release", "pr"])
def test_record_before_the_production_release_posts_nothing(repo, gh, missing):
    """AC3・I1: タグ・GitHub Release・マージ済みの PR のどれかが無ければ、書かずに 3 で止まる。"""
    if missing == "tag":
        git(repo, "push", "-q", "origin", ":refs/tags/ndf--v1.2.3")
    elif missing == "release":
        gh.set(releases=[])
    else:
        gh.set(prs=[{"number": 40, "state": "OPEN", "head": "release/v1.2.3", "base": "develop"}])
    code, out, _ = record(repo, gh.env)
    assert code == 3 and out["status"] == "stopped"
    assert gh.comments() == []
    assert not any(c[:2] == ["pr", "comment"] for c in gh.get().get("calls", []))


def test_record_failure_to_post_is_one(repo, gh):
    """AC4: gh pr comment が非 0 なら 1 で止まる（プランは record の失敗として judge-record へ進む）。"""
    gh.set(comment_code=1)
    code, out, _ = record(repo, gh.env)
    assert code == 1 and out["status"] == "stopped"


def test_sprint_close_closes_with_the_record_the_step_wrote(repo, gh, tmp_path):
    """AC5: record が書いた記録を持つ PR を --record-pr に渡すと、--with-verification なしで --issues の課題を閉じる。"""
    assert record(repo, gh.env)[0] == 0
    (body,) = gh.comments()
    sc = tmp_path / "sc"
    sc.mkdir()
    bindir = sc / "bin"
    bindir.mkdir()
    f = bindir / "gh"
    f.write_text(harness.FAKE_GH.format(py=PY), encoding="utf-8")
    f.chmod(0o755)
    state = sc / "state.json"
    state.write_text(
        json.dumps(
            {
                "records": {"40": {"body": "ndf v1.2.3 のリリース（prod）", "comments": [{"body": body}]}},
                "bodies": {"11": "", "12": ""},
                "issues": {"o/r#5": ["OPEN"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    env = {**gh.env, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(state)}
    code, out, err = harness.call("sprint-close.py", ["--record-pr", "40", "--repo", "o/r", "--issues", "5", "--dry-run"], env, repo)
    assert code == 0, (out, err)
    assert harness.issues_of(out)["o/r#5"]["result"] == "would_close"
