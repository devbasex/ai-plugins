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


# --- #1870: 版数を上げない配布・昇格の経路・本文の 段階: 検証 ------------------------------

UNVERSIONED = """## 配布の記録

段階: 本番（承認ゲート 2 の後、main へマージした）
版: 3.9.0 → 4.0.0（main へのマージ）
スプリント: PR #430 / #431

## リリース後テスト

対象の版: main c427284（2026-10-01 12:00）
導入経路: main を pull

| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |
| --- | --- | --- | --- | --- |
| #5 | 1. 動く | `make check` | 2026-10-01 12:10 | 合格 |

合否: 合格（1 件中 1 件）
"""


def test_an_unversioned_release_test_after_the_record_is_used():
    """AC1: 版数を上げない配布で `版:` と `対象の版:` が違っても、記録より後のリリース後テストを選ぶ（devbase PR #432）。"""
    got = dist_record.parse_record(UNVERSIONED)
    assert got["stage"].startswith("本番") and got["version"] == "4.0.0"
    assert got["verify_block"] and got["verify_block"].startswith("## リリース後テスト") and "main c427284" in got["verify_block"]


def test_a_matching_release_test_still_wins_over_a_later_one():
    """AC1・AC7: 版の一致するブロックがあれば、後ろに版の違うブロックがあってもそれを選ぶ。"""
    text = dist_record.format_record("10.17.67", "10.17.68", [1], stage_note="承認の後", version_note="タグ")
    text += "\n## リリース後テスト\n\n対象の版: 10.17.68（2026-10-01）\n合否: 合格（一致）\n"
    text += "\n## リリース後テスト\n\n対象の版: 10.17.69-dev.1（2026-10-02）\n合否: 合格（後ろ）\n"
    got = dist_record.parse_record(text)
    assert got["version"] == "10.17.68" and got["verify_block"].endswith("合否: 合格（一致）")


def test_a_release_test_of_another_version_is_not_used_for_production():
    """AC1: 版の一致するブロックが無く、後ろが版数の違うテスト（10.17.69-dev.1）だけなら本番の検証に使わない。"""
    text = dist_record.format_record("10.17.67", "10.17.68", [1], stage_note="承認の後", version_note="タグ")
    text += "\n## リリース後テスト\n\n対象の版: 10.17.69-dev.1（2026-10-02）\n合否: 合格（開発版）\n"
    assert dist_record.parse_record(text)["verify_block"] is None


def test_a_release_test_before_the_record_is_not_used():
    """AC1: 記録より前のリリース後テストは、版が違えば選ばない。"""
    text = "## リリース後テスト\n\n対象の版: main abc1234\n合否: 合格\n\n" + UNVERSIONED.split("## リリース後テスト")[0]
    assert dist_record.parse_record(text)["verify_block"] is None


def test_a_record_comment_after_a_verification_body_reads_as_production():
    """AC4: 本文が `段階: 検証（… 承認待ちのため未実施）` の PR に、配布の後の記録をコメントで足すと本番として読む（devbase PR #212）。"""
    body = "## 配布の記録\n\n段階: 検証（本番は承認ゲート 2 の承認待ちのため未実施）\n版: 3.6.0 → 3.7.0\nスプリント: PR #210 / #211\n"
    assert dist_record.parse_record(body)["stage"].startswith("検証")
    comment = dist_record.format_record("3.6.0", "3.7.0", [210, 211], stage_note="承認の後", version_note="タグ")
    got = dist_record.parse_record(body + "\n" + comment)
    assert got["stage"].startswith("本番") and got["version"] == "3.7.0" and got["sprint_prs"] == [210, 211]


def promote_state(repo: Path, gh, **kw):
    """develop → main の昇格の PR #50 がマージ済み（マージのコミットは作業場所の HEAD）。"""
    git(repo, "commit", "-q", "--allow-empty", "-m", "promote")
    sha = git(repo, "rev-parse", "HEAD").strip()
    rec = {"body": "develop → main", "comments": [], "url": "https://github.com/o/r/pull/50", "mergeCommit": {"oid": sha}}
    gh.set(**{"prs": [{"number": 50, "state": "MERGED", "head": "develop", "base": "main"}], "records": {"50": rec}, **kw})
    return sha


def record_promote(repo: Path, env: dict) -> tuple[int, dict]:
    args = ["record", "--root", str(repo), "--promote", "--head", "develop", "--base", "main", "--prs", "11", "12"]
    p = subprocess.run([PY, str(SCRIPTS / "release-steps.py"), *args], capture_output=True, text=True, env=env)
    return p.returncode, (json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {})


def test_record_promote_writes_a_production_record_to_the_promote_pr(repo, gh):
    """AC2: record --promote は昇格の PR へ記録を 1 件書き、sprint-close は `段階: 本番` で読む。2 度目は書かない。"""
    sha = promote_state(repo, gh)
    code, out = record_promote(repo, gh.env)
    assert code == 0 and [i["result"] for i in out["items"]] == ["posted"], out
    assert out["metrics"]["release_pr_url"] == "https://github.com/o/r/pull/50"
    (body,) = [c["body"] for c in gh.get()["records"]["50"]["comments"]]
    got = dist_record.parse_record(body)
    assert got["stage"].startswith("本番") and got["version"] == f"main {sha[:7]}" and got["sprint_prs"] == [11, 12]
    code, out = record_promote(repo, gh.env)
    assert code == 0 and [i["result"] for i in out["items"]] == ["exists"]


def test_record_promote_without_a_merged_pr_posts_nothing(repo, gh):
    """AC2: マージ済みの昇格の PR が無ければ書かずに 3 で止まる（プランは judge-record へ進む）。"""
    promote_state(repo, gh, prs=[])
    code, out = record_promote(repo, gh.env)
    assert code == 3 and out["status"] == "stopped" and gh.get()["records"]["50"]["comments"] == []
