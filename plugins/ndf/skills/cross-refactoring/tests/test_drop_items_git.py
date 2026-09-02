"""取り消しと積み直しを**実際の git** で確かめる。

差し替えたコマンド列だけを見ても「競合しないか」は示せない。ここでは本物の
リポジトリを作り、2 つの改善項目の位置関係を変えて挙動を確かめる。

| 位置関係 | 結果 |
| --- | --- |
| 別ファイル / 離れた行 | 項目単位で取り消し、残す項目は積み直せる |
| 同一ファイルの隣接行 | 積み直せないのでラウンド全件へ退避する |

**隣接する変更は git だけでは分離できない。** 取り消した側の行が消えると、残す側の
パッチが前提にしている文脈も消えるためである。退避してでも Pull Request を
決定的な状態に保つことを優先する。
"""
from __future__ import annotations

import subprocess

import pytest


LINES = [f"line{i}\n" for i in range(1, 41)]


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def _commit(repo, message):
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def _make_repo(tmp_path, second_change):
    """`R1-001` が 3 行目を、`R1-002` が `second_change` で示す箇所を変える。"""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git("init", "-q", str(repo), cwd=tmp_path)
    # 検査の対象（`refactor.py`）が自分でコミットする。**身元はテストが用意する。**
    # 実行した人の全体設定に頼ると、身元の無い実行環境で落ちる（#235）。
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "src" / "foo.py").write_text("".join(LINES), encoding="utf-8")
    (repo / "src" / "bar.py").write_text("".join(LINES), encoding="utf-8")
    base = _commit(repo, "init")

    lines = list(LINES)
    lines[2] = "line3-by-R1-001\n"
    (repo / "src" / "foo.py").write_text("".join(lines), encoding="utf-8")
    c1 = _commit(repo, "R1-001")

    second_change(repo, lines)
    c2 = _commit(repo, "R1-002")
    return {"repo": repo, "base": base, "c1": c1, "c2": c2}


def _touch_adjacent_line(repo, lines):
    lines = list(lines)
    lines[3] = "line4-by-R1-002\n"
    (repo / "src" / "foo.py").write_text("".join(lines), encoding="utf-8")


def _touch_distant_line(repo, lines):
    lines = list(lines)
    lines[30] = "line31-by-R1-002\n"
    (repo / "src" / "foo.py").write_text("".join(lines), encoding="utf-8")


def _touch_other_file(repo, lines):
    other = list(LINES)
    other[2] = "line3-by-R1-002\n"
    (repo / "src" / "bar.py").write_text("".join(other), encoding="utf-8")


@pytest.fixture
def adjacent_repo(tmp_path):
    """同一ファイルの**隣接行**を触る 2 項目。実機で進行が止まった位置関係。"""
    return _make_repo(tmp_path, _touch_adjacent_line)


@pytest.fixture
def distant_repo(tmp_path):
    """同一ファイルの**離れた行**を触る 2 項目。"""
    return _make_repo(tmp_path, _touch_distant_line)


@pytest.fixture
def separate_repo(tmp_path):
    """**別ファイル**を触る 2 項目。"""
    return _make_repo(tmp_path, _touch_other_file)


def _state(built):
    entry = {
        "round": 1,
        "items": ["R1-001", "R1-002"],
        "apply_base_sha": built["base"],
        "apply": {"applied": ["R1-001", "R1-002"], "failed": []},
    }
    state = {
        "worktrees": {"work": str(built["repo"])},
        "rounds": [entry],
        "items": [
            {"item_id": "R1-001", "round": 1, "status": "reviewing",
             "commits": [built["c1"]]},
            {"item_id": "R1-002", "round": 1, "status": "reviewing",
             "commits": [built["c2"]]},
        ],
    }
    return state, entry


def _content(built, name="foo.py"):
    return (built["repo"] / "src" / name).read_text(encoding="utf-8")


# ---------- 前提の確認 ----------

def test_reverting_only_the_older_commit_conflicts(adjacent_repo):
    """古い方だけを戻すと本当に競合すること。

    これが競合しないなら、取り消しの作り直しそのものが不要になる。
    """
    r = subprocess.run(
        ["git", "revert", "--no-edit", adjacent_repo["c1"]],
        cwd=adjacent_repo["repo"], capture_output=True, text=True,
    )
    assert r.returncode != 0, "競合しない位置関係になっている（テストの前提が崩れた）"
    subprocess.run(["git", "revert", "--abort"], cwd=adjacent_repo["repo"],
                   capture_output=True, text=True)


# ---------- 項目単位で取り消せる場合 ----------

@pytest.mark.parametrize("fixture_name", ["distant_repo", "separate_repo"])
def test_drop_older_item_keeps_the_newer_one(refactor, request, fixture_name):
    """独立した変更なら、古い項目だけを取り消して新しい項目を残せること。"""
    built = request.getfixturevalue(fixture_name)
    state, entry = _state(built)
    result = refactor._drop_items(state, entry, ["R1-001"])

    assert result["mode"] == "item"
    assert "line3-by-R1-001" not in _content(built), "取り消した項目の変更が残っている"
    assert "R1-002" in _content(built) + _content(built, "bar.py"), \
        "残すはずの項目の変更が消えている"

    by_id = {i["item_id"]: i for i in state["items"]}
    assert by_id["R1-001"]["reverted"] is True
    # 積み直しで SHA が変わるので、記録も追従していること
    head = _git("rev-parse", "HEAD", cwd=built["repo"]).stdout.strip()
    assert by_id["R1-002"]["commits"] == [head]


def test_drop_newer_item_keeps_the_older_one(refactor, distant_repo):
    """新しい項目だけを取り消す向きでも成立すること。"""
    state, entry = _state(distant_repo)
    assert refactor._drop_items(state, entry, ["R1-002"])["mode"] == "item"
    assert "line3-by-R1-001" in _content(distant_repo)
    assert "line31-by-R1-002" not in _content(distant_repo)


def test_second_drop_after_the_first_still_works(refactor, distant_repo):
    """1 回目で積み直した SHA に対して、もう一度取り消せること。

    積み直しで SHA が変わるので、記録を更新していないとここで破綻する。
    """
    state, entry = _state(distant_repo)
    refactor._drop_items(state, entry, ["R1-001"])
    assert refactor._drop_items(state, entry, ["R1-002"])["mode"] == "item"
    assert _content(distant_repo) == "".join(LINES)


def test_dropping_is_idempotent(refactor, distant_repo):
    state, entry = _state(distant_repo)
    refactor._drop_items(state, entry, ["R1-001"])
    head = _git("rev-parse", "HEAD", cwd=distant_repo["repo"]).stdout.strip()

    assert refactor._drop_items(state, entry, ["R1-001"])["mode"] == "skip"
    assert _git("rev-parse", "HEAD",
                cwd=distant_repo["repo"]).stdout.strip() == head


# ---------- 積み直せない場合 ----------

def test_adjacent_changes_fall_back_to_the_whole_round(refactor, adjacent_repo):
    """隣接する変更は分離できない。退避して全件取り消すこと。

    半端な履歴を残すより、決定的な状態へ落とす方が安全である。
    """
    state, entry = _state(adjacent_repo)
    before = int(_git("rev-list", "--count", "HEAD",
                      cwd=adjacent_repo["repo"]).stdout.strip())
    result = refactor._drop_items(state, entry, ["R1-001"])

    assert result["mode"] == "round"
    assert _content(adjacent_repo) == "".join(LINES), "着手前の内容へ戻っていない"
    assert all(i["reverted"] for i in state["items"])
    assert entry["drops"][-1]["mode"] == "round"
    # 取り消しは 1 組だけ。着手前まで戻してやり直すと 2 組できて履歴が汚れる
    after = int(_git("rev-list", "--count", "HEAD",
                     cwd=adjacent_repo["repo"]).stdout.strip())
    assert after - before == 2, f"取り消しコミットが余分にある（{after - before} 件）"


def test_dropping_every_item_returns_to_the_base_tree(refactor, distant_repo):
    state, entry = _state(distant_repo)
    refactor._drop_items(state, entry, ["R1-001", "R1-002"])
    assert _content(distant_repo) == "".join(LINES)


def test_history_is_never_rewritten(refactor, distant_repo):
    """`--force` を使わずに済むよう、前進だけで戻すこと。"""
    state, entry = _state(distant_repo)
    before = _git("rev-list", "--count", "HEAD",
                  cwd=distant_repo["repo"]).stdout.strip()
    refactor._drop_items(state, entry, ["R1-001"])
    after = _git("rev-list", "--count", "HEAD",
                 cwd=distant_repo["repo"]).stdout.strip()
    assert int(after) > int(before), "履歴を書き換えている"
    assert built_commits_still_reachable(distant_repo)


def built_commits_still_reachable(built) -> bool:
    """着手前のコミットが履歴から消えていないこと。"""
    log = _git("rev-list", "HEAD", cwd=built["repo"]).stdout.split()
    return built["c1"] in log and built["base"] in log
