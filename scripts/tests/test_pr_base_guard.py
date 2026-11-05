"""既定ブランチ宛の Pull Request の分岐元をチェックする（issue #202）。

配布のチャネルを分けるリポジトリでは、正式版のブランチへ直に Pull Request を出さない。
判定は宣言に起点が書かれていて、そのブランチが origin にあるときだけ働く。書く前・作る前は
成功で通す。時期を人の手で合わせずに済ませるためである。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


from branch_repo_helpers import push_branch

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "check-pr-base.sh"


def declare(repo: Path, body: dict) -> None:
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.json").write_text(json.dumps(body), encoding="utf-8")


def guard(repo: Path, head_ref: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(GUARD), head_ref], cwd=str(repo), capture_output=True, text=True)


def test_without_declaration_passes(origin_repo: Path) -> None:
    """宣言が無いリポジトリではチェックしない。"""
    assert guard(origin_repo, "feature/x").returncode == 0


def test_without_base_branch_passes(origin_repo: Path) -> None:
    """チャネルを分けていないリポジトリではチェックしない。"""
    declare(origin_repo, {"version": 1})
    assert guard(origin_repo, "feature/x").returncode == 0


def test_unsupported_version_passes(origin_repo: Path) -> None:
    declare(origin_repo, {"version": 99, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    assert guard(origin_repo, "feature/x").returncode == 0


def test_absent_base_branch_passes(origin_repo: Path) -> None:
    """起点ブランチをまだ作っていない間は、すべての Pull Request を通す。"""
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    assert guard(origin_repo, "feature/x").returncode == 0


def test_branch_with_matching_tail_does_not_enable_the_guard(origin_repo: Path) -> None:
    """origin への問い合わせは、完全な参照名で照合する。

    `git ls-remote` のパターンは参照名の末尾に一致するため、`develop` とだけ渡すと
    `refs/heads/feature/develop` にも一致する（実測）。起点ブランチが未作成のまま
    チェックが有効にならないことを見る。
    """
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "feature/develop")
    assert guard(origin_repo, "feature/x").returncode == 0


def test_from_base_branch_passes(origin_repo: Path) -> None:
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    assert guard(origin_repo, "develop").returncode == 0


def test_from_other_branch_fails(origin_repo: Path) -> None:
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    got = guard(origin_repo, "feature/x")
    assert got.returncode != 0
    message = got.stdout + got.stderr
    assert "develop" in message
    assert "--base" in message


def test_missing_argument_fails(origin_repo: Path) -> None:
    """分岐元を渡し忘れたときは、通さずに使い方を出す。"""
    got = subprocess.run(["bash", str(GUARD)], cwd=str(origin_repo), capture_output=True, text=True)
    assert got.returncode == 2
    assert "使い方" in got.stdout + got.stderr


# --- 個人の宣言では判定が変わらない（#495 の AC17） --------------------------


def declare_local(repo: Path, body: dict) -> None:
    """追跡しない個人の宣言 `.ndf/worktree.local.json` を書く。"""
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.local.json").write_text(json.dumps(body), encoding="utf-8")


LOCAL_BASE_BRANCH = {"version": 1, "base_branch": "main"}


def test_local_declaration_does_not_change_the_verdict(origin_repo: Path) -> None:
    """起点は共有の宣言だけが決める。個人の宣言でチェックの宛先は動かない。"""
    declare(origin_repo, {"version": 1, "base_branch": "develop"})
    push_branch(origin_repo, "develop")
    declare_local(origin_repo, LOCAL_BASE_BRANCH)

    assert guard(origin_repo, "develop").returncode == 0
    got = guard(origin_repo, "feature/x")
    assert got.returncode != 0
    assert "develop" in got.stdout + got.stderr


def test_local_declaration_alone_does_not_enable_the_guard(origin_repo: Path) -> None:
    """共有の宣言が無ければチェックしない。個人の宣言だけでは有効にならない。"""
    declare_local(origin_repo, LOCAL_BASE_BRANCH)
    push_branch(origin_repo, "main2")

    assert guard(origin_repo, "feature/x").returncode == 0


# --- release/v<版> を head にした本番の Pull Request（#2436 の R1） ---------------


def guard_sha(repo: Path, head_ref: str, sha: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(GUARD), head_ref, sha], cwd=str(repo), capture_output=True, text=True)


def commit_on(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", name], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", name], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def release_world(repo: Path) -> tuple[str, str]:
    """origin の develop の祖先にある版上げのコミットと、develop に無いコミットを返す。"""
    declare(repo, {"version": 1, "base_branch": "develop"})
    bumped = commit_on(repo, "bump.txt")
    after = commit_on(repo, "later.txt")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", f"{after}:refs/heads/develop"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "side", bumped], check=True)
    stray = commit_on(repo, "stray.txt")
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", f"{stray}:refs/heads/release/v10.28.0"], check=True)
    return bumped, stray


def test_release_branch_inside_the_base_passes(origin_repo: Path) -> None:
    """先頭が origin/develop の祖先の release/v* は通す（develop が先へ進んでいても）。"""
    bumped, _ = release_world(origin_repo)
    assert guard_sha(origin_repo, "release/v10.28.0", bumped).returncode == 0


def test_release_branch_outside_the_base_fails(origin_repo: Path) -> None:
    """develop に含まれないコミットを持つ release/v* は落とす。"""
    _, stray = release_world(origin_repo)
    got = guard_sha(origin_repo, "release/v10.28.0", stray)
    assert got.returncode == 1 and "develop" in got.stdout + got.stderr


def test_release_branch_without_the_head_sha_fails(origin_repo: Path) -> None:
    """head の SHA を渡さない release/v* は、名前だけでは通さない。"""
    release_world(origin_repo)
    assert guard(origin_repo, "release/v10.28.0").returncode == 1


def test_other_branch_inside_the_base_fails(origin_repo: Path) -> None:
    """develop に含まれていても、release/v* でも develop でもない head は落とす。"""
    bumped, _ = release_world(origin_repo)
    assert guard_sha(origin_repo, "feature/x", bumped).returncode == 1
