"""git から事実を取る経路を、**実際の git リポジトリ**で確かめるテスト。

他のテストは `collect_commit_facts()` を差し替えるため、git の呼び出し方そのものが
間違っていても気付けない。ここだけは本物の git を通す。
"""
from __future__ import annotations

import subprocess

import pytest



def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def work(tmp_path):
    """1 コミットだけある作業ディレクトリ。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def f():\n    return 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo


def _commit(repo, message, files):
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


TRAILERS = (
    "\n\nItem-Id: R1-001\nRound: 1\nImpl-Runtime: codex\nImpl-Model: gpt-5.5"
)


def test_facts_come_from_a_real_repository(gitfacts, work):
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    first = _commit(work, "Test: 現状固定テストを足す" + TRAILERS,
                    {"tests/test_foo.py": "def test_f():\n    assert True\n"})
    second = _commit(work, "Refactor: extract_method" + TRAILERS,
                     {"src/foo.py": "def _one():\n    return 1\n\n\ndef f():\n"
                                    "    return _one()\n"})

    ordered = gitfacts.commits_in_range(str(work), base, "HEAD")
    assert ordered == [second, first], "新しい順で返っていない"

    facts = gitfacts.collect_commit_facts(
        str(work), [first, second], set(ordered), "true", "main"
    )
    assert [f["sha"] for f in facts] == [first, second]
    assert all(f["exists"] for f in facts)
    assert facts[0]["trailers"] == {
        "Item-Id": "R1-001", "Round": "1",
        "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5",
    }
    # 現状固定テストの追加が先行している
    assert facts[0]["touches_tests"] is True
    assert facts[1]["touches_tests"] is False
    assert facts[0]["diff_lines"] > 0 and facts[1]["diff_lines"] > 0
    assert all(f["test_status"] == "pass" for f in facts)
    # テスト実行のあとも元のブランチへ戻っている
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work).stdout.strip() == "main"


def test_commit_test_changes_reads_an_added_test(gitfacts, work):
    """現状固定: 行途中の改行は残り、末尾の改行は除かれる。"""
    sha = _commit(work, "Test: テストを追加", {
        "tests/test_foo.py": "def test_f():\n    assert f() == 1\n",
        "src/foo.py": "def f():\n    return 2\n",
    })

    assert gitfacts.commit_test_changes(str(work), sha) == {
        "tests/test_foo.py": ([], ["def test_f():\n", "    assert f() == 1"]),
    }


def test_commit_test_changes_reads_both_sides_of_a_modified_test(gitfacts, work):
    """現状固定: 変更前後の期待値を Git からそれぞれ読み取る。"""
    _commit(work, "Test: 変更前", {
        "tests/test_foo.py": "def test_f():\n    assert f() == 1\n",
    })
    sha = _commit(work, "Test: 期待値を変更", {
        "tests/test_foo.py": "def test_f():\n    assert f() == 2\n",
        "src/foo.py": "def f():\n    return 2\n",
    })

    assert gitfacts.commit_test_changes(str(work), sha) == {
        "tests/test_foo.py": (
            ["def test_f():\n", "    assert f() == 1"],
            ["def test_f():\n", "    assert f() == 2"],
        ),
    }


def test_commit_test_changes_reads_a_deleted_test(gitfacts, work):
    """現状固定: 削除したテストは変更前の行と空の変更後を返す。"""
    _commit(work, "Test: 削除前", {
        "tests/test_foo.py": "def test_f():\n    assert f() == 2\n",
    })
    (work / "tests" / "test_foo.py").unlink()
    sha = _commit(work, "Test: テストを削除", {
        "src/foo.py": "def f():\n    return 2\n",
    })

    assert gitfacts.commit_test_changes(str(work), sha) == {
        "tests/test_foo.py": (["def test_f():\n", "    assert f() == 2"], []),
    }


def test_missing_trailers_are_seen_as_missing(verify, gitfacts, work):
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    sha = _commit(work, "Refactor: トレーラーなし", {"src/foo.py": "def f():\n    return 2\n"})
    facts = gitfacts.collect_commit_facts(
        str(work), [sha], {sha}, "true", "main"
    )
    problem = verify.verify_commit_trailers(facts[0])
    assert problem is not None and "Item-Id" in problem
    assert base != sha


def test_commit_outside_the_range_is_rejected(gitfacts, work):
    """起点より前のコミットを申告しても実在扱いにしない。"""
    old = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    base = _commit(work, "Chore: 起点" + TRAILERS, {"src/bar.py": "y = 1\n"})
    new = _commit(work, "Refactor: 対象" + TRAILERS, {"src/foo.py": "def f():\n    return 3\n"})

    ordered = gitfacts.commits_in_range(str(work), base, "HEAD")
    assert ordered == [new]
    facts = gitfacts.collect_commit_facts(
        str(work), [old], set(ordered), "true", "main"
    )
    assert facts[0]["exists"] is False


def test_failing_test_is_detected_by_running_it(gitfacts, work):
    """`test_status` は実際に走らせて決まる。申告では決まらない。"""
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    sha = _commit(work, "Refactor: 壊した" + TRAILERS, {"src/foo.py": "def f():\n    return 9\n"})
    facts = gitfacts.collect_commit_facts(
        str(work), [sha], {sha}, "false", "main"
    )
    assert facts[0]["test_status"] == "fail"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work).stdout.strip() == "main"
    assert base != sha


def test_fix_commits_pass_verification_through_real_git(verify, gitfacts, work):
    """修正コミットが git 経由の検証を通ること。

    範囲に空集合を渡していた頃は、全ての修正コミットが必ず不正扱いになっていた。
    """
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    sha = _commit(work, "Fix: レビュー指摘の反映" + TRAILERS,
                  {"src/foo.py": "def f():\n    return 1  # 直した\n"})
    ordered = gitfacts.commits_in_range(str(work), base, "HEAD")
    facts = gitfacts.collect_commit_facts(
        str(work), [sha], set(ordered), "true", "main"
    )
    assert verify.verify_fix_commit(facts[0]) is None


def test_revert_order_comes_from_history_not_from_the_claim(gitfacts, work):
    """申告の順序ではなく、実際の履歴で新しい順に並べること。"""
    first = _commit(work, "one", {"src/a.py": "a = 1\n"})
    second = _commit(work, "two", {"src/a.py": "a = 2\n"})
    third = _commit(work, "three", {"src/a.py": "a = 3\n"})

    # わざと順不同で渡す
    ordered = gitfacts._order_newest_first(str(work), [first, third, second])
    assert ordered == [third, second, first]


def test_revert_order_tolerates_unknown_shas(gitfacts, work):
    known = _commit(work, "one", {"src/a.py": "a = 1\n"})
    ordered = gitfacts._order_newest_first(str(work), ["deadbeef", known])
    assert ordered[0] == known, "履歴にあるものを先に戻す"


def test_reverting_in_history_order_succeeds(gitfacts, work):
    """履歴順に戻せば、同じファイルを触る連続コミットでも競合しない。"""
    base = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    first = _commit(work, "one", {"src/a.py": "a = 1\n"})
    second = _commit(work, "two", {"src/a.py": "a = 2\n"})

    state = {"worktrees": {"work": str(work)}}
    item = {"item_id": "R1-001", "commits": [first, second]}   # 古い順の申告
    assert gitfacts.revert_item_commits(state, item) == 2
    assert item["reverted"] is True

    # 取り消し後は着手前の状態へ戻る（このファイルは base に存在しない）
    assert not (work / "src" / "a.py").exists()
    diff = _git("diff", "--name-only", base, "HEAD", cwd=work).stdout.strip()
    assert diff == "", f"着手前との差分が残っている: {diff}"


def test_hanging_test_is_cut_off(gitfacts, work):
    """テストが終わらないときは打ち切って失敗にする。

    無限ループに入ったコードを待ち続けると、進行全体が止まる。
    """
    sha = _commit(work, "Refactor: 無限ループ" + TRAILERS,
                  {"src/foo.py": "def f():\n    return 2\n"})
    status = gitfacts.run_test_at(
        str(work), sha, "sleep 30", "main", timeout=1
    )
    assert status == "fail"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=work).stdout.strip() == "main"


def test_cutting_off_a_test_kills_its_children(gitfacts, work):
    """打ち切るときは**子プロセスまで**止めること。

    シェルだけを終了すると pytest 等が走り続け、直後の checkout と同じ作業
    ディレクトリを取り合う。
    """
    import time

    sha = _commit(work, "Refactor: 子プロセスを残す" + TRAILERS,
                  {"src/foo.py": "def f():\n    return 3\n"})
    marker = work / "child-ran"
    # 子プロセスが 2 秒後に痕跡を残そうとする
    command = f"(sleep 2 && touch {marker}) & sleep 30"

    status = gitfacts.run_test_at(str(work), sha, command, "main", timeout=1)
    assert status == "fail"

    time.sleep(3)
    assert not marker.exists(), "子プロセスが生き残って書き込んでいる"


def test_cutting_off_kills_children_that_ignore_sigterm(gitfacts, work):
    """SIGTERM を無視する子にも必ず SIGKILL が届くこと。

    親シェルの終了で打ち切ると、無視する子はグループに残って作業ディレクトリを
    書き換え続ける。判定はグループの存否で行う。
    """
    import time

    sha = _commit(work, "Refactor: TERM を無視" + TRAILERS,
                  {"src/foo.py": "def f():\n    return 4\n"})
    marker = work / "stubborn-ran"
    command = f"trap '' TERM; (sleep 3 && touch {marker}) & sleep 30"

    started = time.monotonic()
    status = gitfacts.run_test_at(
        str(work), sha, command, "main", timeout=1, kill_grace=1.0
    )
    elapsed = time.monotonic() - started

    assert status == "fail"
    assert elapsed < 20, f"打ち切りに時間がかかりすぎている: {elapsed:.1f}s"
    time.sleep(4)
    assert not marker.exists(), "SIGTERM を無視する子が生き残っている"


def test_find_item_returns_none_for_a_missing_id_when_not_required(gitfacts):
    """現状固定: `required=False` で存在しない項目 ID を探すと None を返す。

    取り消しや積み直しの経路（`_commit_owner` など）は、状態に残っていない
    項目 ID を渡しても落とさずに読み飛ばせることを前提にしている。
    """
    state = {"items": [{"item_id": "R1-001"}, {"item_id": "R1-002"}]}

    assert gitfacts.find_item(state, "R9-999", required=False) is None
