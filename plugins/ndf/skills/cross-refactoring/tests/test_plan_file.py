"""改修計画をリポジトリ内のファイルへ残すテスト。

提案の理由と手順は状態ファイルにしか残らず、そのディレクトリは差分から除外される。
**Pull Request を読む側からは、なぜ直したのかも、どう直す計画だったのかも見えない。**
計画を差分の中へ置き、公開は生成物の同期と同じ経路（進行側の 1 コミット）に乗せる。
"""
from __future__ import annotations

import subprocess

import pytest

from crossref_helpers import make_state_v2, read_state


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


def _commit(repo, message):
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", message, cwd=repo)


def _make_work(tmp_path):
    work = tmp_path / "work"
    (work / "generated").mkdir(parents=True)
    _git("init", "-q", "-b", "main", str(work), cwd=tmp_path)
    # 検査の対象（`refactor.py`）が自分でコミットする。**身元はテストが用意する。**
    # 実行した人の全体設定に頼ると、身元の無い実行環境で落ちる（#235）。
    _git("config", "user.email", "t@e.st", cwd=work)
    _git("config", "user.name", "test", cwd=work)
    (work / "src.py").write_text("x = 1\n", encoding="utf-8")
    (work / "generated" / "out.py").write_text("x = 1\n", encoding="utf-8")
    _commit(work, "init")
    return work


def _item(**over):
    base = {
        "id": "I-001", "rank": 1, "path": "src/foo.py", "symbol": "Foo.handle",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "tier": "high",
        "rationale": "1 関数が 6 つの処理を通しで行っている",
        "plan": "1. 範囲の確定を切り出す 2. 検証を切り出す",
        "tests": [], "estimated_diff_lines": 40,
        "proposed_by": ["codex", "agy"], "status": "verified",
        "commits": {"test": None, "implement": "abc1234", "fix": []},
    }
    base.update(over)
    return base


def _state(tmp_path, work=None, **over):
    over.setdefault("items", [_item()])
    over.setdefault("implementer", "kiro")
    over.setdefault("plan", {"available_minutes": 43.5, "table_source": "defaults"})
    path = make_state_v2(tmp_path, work or tmp_path / "work", **over)
    return path, read_state(path)


# ---------- 計画の本文 ----------

def test_plan_names_the_item_and_the_target(plan, tmp_path):
    _, state = _state(tmp_path)
    text = plan.format_plan(state)
    assert "I-001" in text and "src/foo.py" in text and "Foo.handle" in text


def test_plan_carries_the_reason_and_the_steps(plan, tmp_path):
    """なぜ直すのか・どう直すのかは、提案の時点でしか残らない。"""
    _, state = _state(tmp_path)
    text = plan.format_plan(state)
    assert "1 関数が 6 つの処理を通しで行っている" in text
    assert "1. 範囲の確定を切り出す" in text


def test_plan_shows_the_smell_the_technique_and_the_tier(plan, tmp_path):
    _, state = _state(tmp_path)
    text = plan.format_plan(state)
    assert "long_method" in text and "extract_method" in text and "high" in text


def test_plan_counts_the_commits_of_an_item(plan, tmp_path):
    """テスト・実装・修正のコミットを足した数を載せる（1 改善項目 = 1 コミットの確かめ）。"""
    _, state = _state(tmp_path, items=[_item(commits={
        "test": "t" * 7, "implement": "i" * 7, "fix": ["f" * 7]})])
    assert "| 採用 | 3 |" in plan.format_plan(state)


def test_plan_lists_the_deferred_proposals_with_their_reason(plan, tmp_path):
    """見送りの内訳を持つのは改修計画だけである（決定 6-b）。理由と補足を載せる。"""
    _, state = _state(
        tmp_path,
        items=[],
        deferred_items=[{
            "item_id": "I-002", "path": "src/paths.py", "symbol": "load_state",
            "smell": "duplicated_code", "defer_reason": "budget",
            "detail": "想定最大時間に収まらない",
        }],
    )

    text = plan.format_plan(state)

    assert "`src/paths.py#load_state`" in text
    assert "budget" in text and "想定最大時間に収まらない" in text


def test_plan_records_who_proposed_it(plan, tmp_path):
    _, state = _state(tmp_path)
    assert "codex / agy" in plan.format_plan(state)


def test_plan_marks_a_reverted_item_with_its_reason(plan, tmp_path):
    """取り消した項目も残す。同じ提案が再び来たときの判断材料になる。"""
    _, state = _state(tmp_path, items=[_item(
        status="reverted", commits={"test": None, "implement": None, "fix": []},
        failure_reason="修正の上限に達した")])
    text = plan.format_plan(state)
    assert "取り消し" in text
    assert "修正の上限に達した" in text


def test_plan_follows_the_order_of_the_items(plan, tmp_path):
    """項目は計画の順位の順（状態の並び）で載る。"""
    items = [_item(), _item(id="I-002", rank=2, symbol="Bar.run")]
    _, state = _state(tmp_path, items=items)
    text = plan.format_plan(state)
    assert text.index("### I-001") < text.index("### I-002")


def test_plan_names_the_budget_and_the_implementer(plan, tmp_path):
    _, state = _state(tmp_path, budget_minutes=45)
    text = plan.format_plan(state)
    assert "45" in text and "43.5" in text and "kiro" in text


def test_plan_shows_every_limit_and_marks_a_missing_one(plan, tmp_path):
    """上限の表は値を 1 行ずつ出し、決まっていない値は — にする。"""
    _, state = _state(tmp_path, limits={"fix_end_at": "2026-09-24T10:40:00",
                                        "final_fix_seconds": None, "test_timeout": 300})
    rows = [line for line in plan.format_plan(state).splitlines()
            if line.startswith("| ") and not line.startswith("| 値")]
    values = [row.rsplit("|", 2)[1].strip() for row in rows[-len(plan._LIMIT_ROWS):]]
    assert len(values) == len(plan._LIMIT_ROWS)
    keys = [key for key, _ in plan._LIMIT_ROWS]
    assert values[keys.index("fix_end_at")] == "2026-09-24T10:40:00"
    assert values[keys.index("test_timeout")] == "300"
    assert values[keys.index("final_fix_seconds")] == "—"


def test_plan_is_stable_for_the_same_state(plan, tmp_path):
    """同じ状態からは同じ本文が出る。差分が出続けると毎回コミットが積まれる。"""
    _, state = _state(tmp_path)
    assert plan.format_plan(state) == plan.format_plan(state)


# ---------- 置き場所 ----------

def test_the_plan_file_is_written_inside_the_work_dir(gitfacts, tmp_path):
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md")

    gitfacts._write_plan_file(state, str(work), "issues/plan.md")

    written = (work / "issues" / "plan.md").read_text(encoding="utf-8")
    assert "I-001" in written


# ---------- 公開 ----------

def test_the_plan_lands_in_one_commit_with_the_generated_files(gitfacts, tmp_path):
    """計画書と生成物で 2 コミットに分けない。"""
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md",
                      sync_command="printf 'x = 2\\n' > generated/out.py")

    gitfacts._sync_generated(state)

    subject = _git("log", "-1", "--format=%s", cwd=work).stdout.strip()
    files = _git("show", "--name-only", "--format=", "HEAD", cwd=work).stdout.split()
    assert "issues/plan.md" in files and "generated/out.py" in files
    assert subject and "cross-refactoring" in subject


def test_a_repository_without_a_sync_command_still_records_the_plan(
    refactor, gitfacts, tmp_path
):
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md",
                      sync_command=None)

    gitfacts._sync_generated(state)

    files = _git("show", "--name-only", "--format=", "HEAD", cwd=work).stdout.split()
    assert "issues/plan.md" in files


def test_an_unchanged_plan_does_not_add_a_commit(gitfacts, tmp_path):
    """状態が動いていないのにコミットを積まない。"""
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md",
                      sync_command=None)
    gitfacts._sync_generated(state)
    before = _git("rev-parse", "HEAD", cwd=work).stdout.strip()

    gitfacts._sync_generated(state)

    assert _git("rev-parse", "HEAD", cwd=work).stdout.strip() == before


def test_an_empty_plan_file_setting_turns_the_record_off(gitfacts, tmp_path):
    """計画を差分へ入れたくないリポジトリのために、無効にできる。"""
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="", sync_command=None)

    gitfacts._sync_generated(state)

    assert not (work / "issues").exists()


# ---------- 書き出し先の検証 ----------

def test_a_relative_path_is_kept(plan):
    assert plan.normalize_plan_file("issues/plan.md") == "issues/plan.md"


def test_a_leading_dot_is_normalized(plan):
    """`./issues/plan.md` は git が返すパスと一致しない。正規化して揃える。"""
    assert plan.normalize_plan_file("./issues/plan.md") == "issues/plan.md"


def test_an_empty_value_stays_empty(plan):
    assert plan.normalize_plan_file("") == ""
    assert plan.normalize_plan_file(None) == ""


def test_an_absolute_path_is_refused(plan):
    """作業ディレクトリの外へ書かせない。進行側は利用者のリポジトリを触る。"""
    with pytest.raises(SystemExit):
        plan.normalize_plan_file("/tmp/out.md")


def test_a_parent_traversal_is_refused(plan):
    with pytest.raises(SystemExit):
        plan.normalize_plan_file("../out.md")


def test_a_traversal_in_the_middle_is_refused(plan):
    """途中で外へ出る経路も拒む。正規化してから判定する。"""
    with pytest.raises(SystemExit):
        plan.normalize_plan_file("issues/../../out.md")


def test_the_written_path_stays_inside_the_work_dir(gitfacts, tmp_path):
    work = _make_work(tmp_path)
    _, state = _state(tmp_path, work=work, plan_file="issues/plan.md")

    gitfacts._write_plan_file(state, str(work), state["plan_file"])

    assert (work / "issues" / "plan.md").exists()
    assert not (tmp_path / "plan.md").exists()
