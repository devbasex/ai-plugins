"""範囲の判定（`scope_check.judge_unit`）・期待値の和集合・語彙・範囲テストの対象の判定（#1814）。

| 受け入れ条件・不変条件 | 確かめること |
| --- | --- |
| AC1・I2 | 起点のツリーに無いパスは、置き場所によらず `new`（範囲の中） |
| AC1a | 同じ実行の前のコミットが作ったファイルを後のコミットが変えても `new` |
| AC1b・I3・I4・AC10 | 範囲の外の既存のファイルの無関係な変更・削除・名前を変えて元を消す変更は `outside` |
| 決定 2 | 変えた名前を含む行での機能の追加（`retry=True`）は `outside` |
| I1 | 結果ファイルの申告は分類を変えない（判定は git だけを読む） |
| AC8・I6 | 期待値の判定はテストのファイルの和集合で値の消失を見る |
| AC9・I11・I12 | 足した手法と観点の識別子で書いた提案が `vocabulary` で落ちず、観点は代表の兆候へ写る |
| AC3a | 項目が足すテストは、suite が覆えば `--scope` のテストの置き場所の外でも対象にできる |
| AC6b・I10 | 対象の無い `remove_dead_code` は `deletion`、ほかの手法は今と同じく `none` |
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from crossref_helpers import git

SCOPE = ["src", "tests"]


@pytest.fixture
def repo(tmp_path: pathlib.Path) -> pathlib.Path:
    work = tmp_path / "work"
    work.mkdir()
    git("init", "-q", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    files = {
        "src/core.py": "def load(path):\n    return open(path).read()\n",
        "tools/run.py": "from src.core import load\n\ntimeout = 30\n\n\ndef main():\n    return load('a')\n",
        "tools/keep.py": "KEEP = 1\n",
    }
    for n in range(15):
        files[f"misc/filler_{n:02d}.py"] = f"VALUE_{n:02d} = {n}\n"
    for rel, text in files.items():
        _write(work, rel, text)
    _commit(work, "base")
    return work


def _write(work: pathlib.Path, rel: str, text: str) -> None:
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(work: pathlib.Path, message: str) -> str:
    git("add", "-A", cwd=work)
    git("commit", "-qm", message, cwd=work)
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _judge(scope_check, work, shas, base, context=()):
    return scope_check.judge_unit(str(work), scope_check.Unit(list(shas), SCOPE, base, list(context)))


def _kinds(verdict):
    return {f.path: f.kind for f in verdict.files}


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def test_a_new_file_anywhere_is_in_scope_and_stays_so_for_later_commits(scope_check, repo):
    """AC1・AC1a・I2: 範囲の外の別ディレクトリの新しいファイルは `new`。後のコミットが直しても `new` のまま。"""
    base = _head(repo)
    _write(repo, "shared/models/order.py", "class Order:\n    pass\n")
    _write(repo, "src/core.py", "from shared.models.order import Order\n\n\ndef load(path):\n    return open(path).read()\n")
    first = _commit(repo, "extract")
    _write(repo, "shared/models/order.py", "class Order:\n    total = 0\n")
    second = _commit(repo, "fix the new file")

    verdict = _judge(scope_check, repo, [first], base)
    assert verdict.problem == ""
    assert _kinds(verdict) == {"shared/models/order.py": "new", "src/core.py": "in_scope"}
    later = _judge(scope_check, repo, [second], base)
    assert later.problem == "" and _kinds(later) == {"shared/models/order.py": "new"}


def test_an_unrelated_change_to_an_existing_outside_file_is_outside(scope_check, repo):
    """AC1b・I4: 変えた名前を含まないハンク（`timeout = 30` → `60`）は呼び手の書き換えではない。"""
    base = _head(repo)
    _write(repo, "src/core.py", "def load(path):\n    return open(path, encoding='utf-8').read()\n")
    _write(repo, "tools/run.py", (repo / "tools/run.py").read_text().replace("timeout = 30", "timeout = 60"))
    sha = _commit(repo, "unrelated")

    verdict = _judge(scope_check, repo, [sha], base)
    assert _kinds(verdict)["tools/run.py"] == "outside"
    assert "tools/run.py" in verdict.problem


def test_deleting_or_moving_away_an_existing_outside_file_is_outside(scope_check, repo):
    """AC1b・I3: 範囲の外の既存のファイルを消す・名前を変えて元を消すのは常に `outside`。"""
    base = _head(repo)
    git("rm", "-q", "tools/keep.py", cwd=repo)
    deleted = _commit(repo, "delete")
    assert _kinds(_judge(scope_check, repo, [deleted], base))["tools/keep.py"] == "outside"

    git("mv", "tools/run.py", "tools/runner.py", cwd=repo)
    moved = _commit(repo, "move")
    kinds = _kinds(_judge(scope_check, repo, [moved], base))
    assert kinds["tools/run.py"] == "outside" and kinds["tools/runner.py"] == "new"


def test_a_caller_rewrite_passes_but_a_feature_added_on_the_same_line_does_not(scope_check, repo):
    """決定 2: 改名の追従（H1・H2）は `rewrite` でレビューへ引き継ぐ。同じ行で引数を足すと H2 で `outside`。"""
    base = _head(repo)
    _write(repo, "src/core.py", "def read_text_file(path):\n    return open(path).read()\n")
    _write(repo, "tools/run.py", (repo / "tools/run.py").read_text().replace("load", "read_text_file"))
    sha = _commit(repo, "rename")
    verdict = _judge(scope_check, repo, [sha], base)
    assert verdict.problem == "" and _kinds(verdict)["tools/run.py"] == "rewrite"
    assert verdict.rewrites == [{"path": "tools/run.py", "sha": sha}]

    _write(repo, "tools/run.py", (repo / "tools/run.py").read_text().replace("read_text_file('a')", "read_text_file('a', retry=True)"))
    extra = _commit(repo, "feature")
    assert _kinds(_judge(scope_check, repo, [extra], base, context=[sha]))["tools/run.py"] == "outside"


def test_the_classification_ignores_what_the_result_file_claims(scope_check, repo):
    """I1: 判定の入力は SHA・範囲・起点だけで、結果ファイルを読まない。申告を置いても分類は変わらない。"""
    base = _head(repo)
    _write(repo, "tools/run.py", (repo / "tools/run.py").read_text().replace("timeout = 30", "timeout = 60"))
    sha = _commit(repo, "unrelated")
    (repo.parent / "result.json").write_text('{"new_files": ["tools/run.py"], "caller_rewrites": ["tools/run.py"]}', encoding="utf-8")
    assert _kinds(_judge(scope_check, repo, [sha], base))["tools/run.py"] == "outside"


def test_common_words_count_files_in_the_base_tree(scope_check, repo):
    """ありふれた語: コードのファイルの 20% 以上かつ 3 ファイル以上に現れる語（`ls-tree` は glob を読まないので数え方を縛る）。"""
    words = scope_check._count_common(str(repo), _head(repo))
    assert "VALUE_00" not in words and "load" not in words
    assert not {"def", "return"} & words, "18 本のうち 2 本にしか無い語はありふれた語ではない"


# ---------- 期待値の和集合（AC8・I6） ----------


def test_moving_a_test_value_to_another_test_file_is_not_a_changed_expectation(verify):
    """AC8: 値を同じ単位の別のテストのファイルへ移しただけなら `changed` にせず、レビューへ引き継ぐ。"""
    facts = [
        {"test_changes": {"tests/test_a.py": (["    assert f(1) == 3\n"], ["    assert f(1) == EXPECTED\n"])}},
        {"test_changes": {"tests/expect.py": ([], ["EXPECTED = 3\n"])}},
    ]
    changes = verify.collect_test_changes(facts)
    assert verify.verify_test_changes(changes) is None
    assert verify.pending_test_judgements(facts) == ["tests/expect.py", "tests/test_a.py"]


def test_a_value_lost_from_every_test_file_is_still_a_changed_expectation(verify):
    """AC8・AC10: 和集合のどこにも無くなった値は `changed` として取り消す。"""
    facts = [
        {"test_changes": {"tests/test_a.py": (["    assert f(1) == 3\n"], ["    assert f(1) == EXPECTED\n"])}},
        {"test_changes": {"tests/expect.py": ([], ["EXPECTED = 4\n"])}},
    ]
    problem = verify.verify_test_changes(verify.collect_test_changes(facts))
    assert problem is not None and "tests/test_a.py" in problem


def test_the_union_keeps_the_first_before_and_the_last_after(verify):
    """決定 5: 複数コミットの単位では、前は最初に触ったコミットの前、後は最後に触ったコミットの後。"""
    facts = [
        {"test_changes": {"tests/test_a.py": (["a == 3\n"], ["a == 4\n"])}},
        {"test_changes": {"tests/test_a.py": (["a == 4\n"], ["a == 3\n"])}},
    ]
    assert verify.collect_test_changes(facts) == {"tests/test_a.py": (["a == 3\n"], ["a == 3\n"])}


# ---------- 語彙（AC9） ----------


def _proposal(**over):
    base = {
        "path": "src/foo.py",
        "symbol": "Foo",
        "smell": "large_class",
        "technique": "extract_class",
        "severity": "major",
        "rationale": "r",
        "plan": "p",
    }
    base.update(over)
    return base


@pytest.mark.parametrize("technique", ["extract_class", "split_module", "inline", "change_signature", "extract_test_helper"])
def test_the_added_techniques_are_not_deferred_as_vocabulary(proposals, technique):
    """AC9: 足した 5 つの手法で書いた提案は `vocabulary` で見送られない。"""
    candidates, deferred = proposals.build_candidates({"codex": [_proposal(technique=technique)]})
    assert [c["technique"] for c in candidates] == [technique] and deferred == []


def test_a_viewpoint_written_as_the_smell_maps_to_its_representative_smell(proposals, vocabulary):
    """AC9・I12: 観点の識別子 8 つを `smell` に書いた提案は見送られず、`smell` が代表の兆候になる。"""
    assert len(vocabulary.VIEWPOINTS) == 8
    for viewpoint, smell in vocabulary.VIEWPOINT_SMELLS.items():
        candidates, deferred = proposals.build_candidates({"codex": [_proposal(smell=viewpoint)]})
        assert deferred == [] and [c["smell"] for c in candidates] == [smell]
        assert smell in vocabulary.SMELLS


def test_smells_techniques_and_viewpoints_do_not_share_identifiers(vocabulary):
    """I11: 兆候・手法・観点の識別子は重ならない。重なれば読み込みで止まる。"""
    assert not set(vocabulary.SMELLS) & set(vocabulary.VIEWPOINTS)
    assert not set(vocabulary.TECHNIQUES) & set(vocabulary.VIEWPOINTS)
    original = dict(vocabulary.VIEWPOINTS)
    try:
        vocabulary.VIEWPOINTS["duplication"] = "重なる"
        with pytest.raises(vocabulary.VocabularyUnavailable):
            vocabulary._check_disjoint()
    finally:
        vocabulary.VIEWPOINTS.clear()
        vocabulary.VIEWPOINTS.update(original)


# ---------- 範囲テストの対象（AC3a・AC6b） ----------


def _one_suite(ts, paths):
    return ts.Strategy("local-full", "test", [ts.Suite("py", "pytest -q", "pytest -q {paths}", paths=paths)])


def test_a_planned_test_outside_the_scope_locations_is_used_when_a_suite_covers_it(refactor, monkeypatch, tmp_path):
    """AC3a・決定 8: 項目が足すテストは suite が覆えば `--scope` の置き場所の外でもよい。覆わなければ今と同じく組まない。"""
    targets = sys.modules["refactor_lib.targets"]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_old.py").write_text("", encoding="utf-8")
    covered = _one_suite(targets.ts, ["."])
    monkeypatch.setattr(targets, "strategy_of", lambda state: covered)
    state = {"target_scope": ["src", "tests"]}
    runs, origin = targets.limited_runs(state, ["shared/tests/test_new.py"], str(tmp_path), ["shared/tests/test_new.py"])
    assert origin == "targets" and [r.command for r in runs] == ["pytest -q shared/tests/test_new.py"]
    assert targets.limited_runs(state, ["shared/tests/test_new.py"], str(tmp_path))[1] == "none", "足すテストでなければ今と同じ"

    narrow = _one_suite(targets.ts, ["tests"])
    monkeypatch.setattr(targets, "strategy_of", lambda state: narrow)
    assert targets.limited_runs(state, ["shared/tests/test_new.py"], str(tmp_path), ["shared/tests/test_new.py"])[1] == "none"


def test_dead_code_removal_without_targets_is_kept_as_deletion(refactor, monkeypatch, tmp_path):
    """AC6b・決定 7: 対象の無い `remove_dead_code` は `deletion`（空の並び）。ほかの手法は `none` のまま。"""
    targets = sys.modules["refactor_lib.targets"]
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(targets, "strategy_of", lambda state: _one_suite(targets.ts, ["."]))
    state = {"target_scope": ["src", "tests"]}
    assert targets.limited_runs(state, [], str(tmp_path), [], "remove_dead_code") == ([], "deletion")
    assert targets.limited_runs(state, [], str(tmp_path), [], "extract_method") == (None, "none")
