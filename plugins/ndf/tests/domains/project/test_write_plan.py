"""`write.plan` が決めるファイル・解析の記録・出力の行・形の誤り（何も書かない）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import git_sandbox
import pytest

from ndf.domains.project import analysis_record, check, cli, write
from ndf.domains.project.items import value_digest
from ndf.domains.records import files

INPUTS = {"pyproject.toml": "3634277f"}
# リモートの無い一時のリポジトリのブランチの構成（計測時の値として測定の結果に入れる）
NO_REMOTE = {"head": None, "present": []}
MEASURE = {"analyzer": check.ANALYZER, "items": {}, "inputs": INPUTS, "notes": ["気づき"], "branch_state": NO_REMOTE}
IGNORE, RECORD, PROJECT = (files.relpath(k) for k in ("project-analysis-ignore", "project-analysis", "project"))


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    path = tmp_path / "repo"
    path.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init", "--allow-empty"),
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


def _plan(repo: Path, existing=None, measure=MEASURE, answers=None, record=None) -> write.Plan:
    """計画を決める。測定の結果にブランチの構成と解析器のバージョンが無ければ、リモートの無い構成と今のバージョンを計測時の値として入れる。"""
    measure = {"branch_state": NO_REMOTE, "analyzer": check.ANALYZER, **measure}
    return write.plan(write.Inputs(repo, repo, existing, measure, answers or {}, record))


def _put(repo: Path, key: str, text: str) -> None:
    path = files.path(repo, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _first(repo: Path) -> tuple[dict, dict]:
    """初回の計画が書く (設定, 解析の記録)。"""
    new = {f.name: f.new for f in _plan(repo).files}
    return json.loads(new[PROJECT]), json.loads(new[RECORD])


def test_the_first_plan_writes_the_gitignore_the_settings_and_the_record(repo):
    planned = _plan(repo, answers={"nope": {"value": 1}})
    ignore, project, record = planned.files
    assert ignore == write.File(files.path(repo, "project-analysis-ignore"), IGNORE, None, analysis_record.IGNORE_TEXT, "git から外す設定")
    doc = json.loads(project.new)
    assert project == write.File(files.path(repo, "project"), PROJECT, None, write.dumps(doc))
    expected = {
        "version": analysis_record.RECORD_VERSION,
        "analyzer": check.ANALYZER,
        "inputs": INPUTS,
        "branches": NO_REMOTE,
        "written_digest": analysis_record.written_digest(doc),
    }
    assert record == write.File(files.path(repo, "project-analysis"), RECORD, None, write.dumps(expected), "解析の記録。入力 1 件")
    assert [(r["kind"], r["key"]) for r in planned.rows[len(write.ITEM_KEYS) :]] == [
        ("note", write.CI_KEY),
        ("ignored", "nope"),
        ("note", "notes"),
    ]
    assert planned.ignored == write.Ignored({}, None)
    assert not (repo / ".ndf").exists()


def test_a_changed_settings_keeps_the_text_on_disk_as_old_between_the_pending_and_final_records(repo):
    doc, record = _first(repo)
    existing = {"version": 1}  # 解析の項目が無いので、設定は書き直しになる
    on_disk = json.dumps(existing)  # 整形の違う中身をそのまま前の中身にする
    _put(repo, "project", on_disk)
    _put(repo, "project-analysis-ignore", analysis_record.IGNORE_TEXT)
    before = {**record, "inputs": {}}
    pending, project, final = _plan(repo, existing=existing, record=before).files
    assert (pending.name, pending.old, pending.new, pending.summary) == (
        RECORD,
        write.dumps(before),
        write.dumps({**before, "pending": True}),
        "書き出しの途中のマーカー",
    )
    assert (project.name, project.old, project.summary) == (PROJECT, on_disk, None)
    assert (final.name, final.old) == (RECORD, write.dumps(before))
    written = json.loads(project.new)["analysis"]["written"]
    assert json.loads(final.new)["written_digest"] == value_digest(written)


def test_the_same_settings_and_record_plan_nothing(repo):
    doc, record = _first(repo)
    _put(repo, "project-analysis-ignore", analysis_record.IGNORE_TEXT)
    # 設定が同じなら File を作らないので、設定の File は読まない
    assert _plan(repo, existing=doc, record=record).files == []


def test_a_gitignored_settings_adds_a_note_row_last(repo):
    (repo / ".gitignore").write_text(".ndf/\n", encoding="utf-8")
    planned = _plan(repo)
    last = planned.rows[-1]
    assert (last["key"], last["name"], last["source"]) == ("gitignore", PROJECT, ".gitignore:1")
    assert set(planned.ignored.rules) == {PROJECT, RECORD}


@pytest.mark.parametrize(
    "existing,inputs,head",
    [
        (None, {"pyproject.toml": 1}, "書き出す解析の記録が形に合わない（解析器の誤り）: "),
        ({"version": 2}, INPUTS, "書き出す設定が形に合わない（解析器の誤り）: "),
        # 両方が形に合わなければ、設定の誤りを先に知らせる
        ({"version": 2}, {"pyproject.toml": 1}, "書き出す設定が形に合わない（解析器の誤り）: "),
    ],
)
def test_a_plan_that_does_not_fit_the_schema_is_refused(repo, existing, inputs, head):
    with pytest.raises(write.WriteError) as caught:
        _plan(repo, existing=existing, measure={"items": {}, "inputs": inputs})
    assert str(caught.value).startswith(head)


def test_a_plan_whose_branches_changed_after_the_measure_is_refused(repo):
    stale = {**MEASURE, "branch_state": {"head": "develop", "present": ["develop"]}}
    with pytest.raises(write.WriteError) as caught:
        _plan(repo, measure=stale)
    assert str(caught.value).startswith("計測の後にブランチの構成が変わった")


def test_a_changed_branch_is_shown_masked_and_a_secret_shaped_name_still_matches(repo, monkeypatch):
    secret = "ghp_abcdefghijklmnopqrstuvwxyz"
    now = {"head": secret, "present": []}
    monkeypatch.setattr(write.check, "branch_state", lambda *_a, **_k: now)
    masked = write.mask(now)
    assert masked != now
    planned = _plan(repo, measure={**MEASURE, "branch_state": masked})  # 測定の結果は伏せ字の後の値
    assert json.loads(planned.files[-1].new)["branches"] == now  # 判定と同じ伏せ字の前の値を記録する
    with pytest.raises(write.WriteError) as caught:
        _plan(repo, measure={**MEASURE, "branch_state": NO_REMOTE})
    assert secret not in str(caught.value)


def test_a_new_default_branch_without_measured_checks_keeps_the_previous_branches(repo):
    """既定ブランチが前の記録から変わり、必須のチェックを測れず前の値を残したら、記録の構成を前のままにして古いと判定させる。"""
    ci = {"required_checks": ["old-check"]}
    existing = {"version": 1, "ci": ci, "analysis": {"written": {"ci": value_digest(ci)}}}
    doc, record = _first(repo)
    _put(repo, "project", json.dumps(existing))
    before = {**record, "branches": {"head": "master", "present": ["master"]}}
    measure = {**MEASURE, "items": {"ci": {"status": "unknown", "reason": "gh が失敗した: 401"}}}
    planned = _plan(repo, existing=existing, measure=measure, record=before)
    assert json.loads(planned.files[-1].new)["branches"] == before["branches"]
    assert json.loads(next(f.new for f in planned.files if f.name == PROJECT))["ci"] == ci
    same = _plan(repo, existing=existing, measure=measure, record={**before, "branches": NO_REMOTE})
    assert json.loads(same.files[-1].new)["branches"] == NO_REMOTE  # 既定ブランチが同じなら今の構成を書く


# --- 読み手の無い 6 項目（#4521）: 書かない・残っていれば古い・書き出しで取り除く。git は差し替え、起動しない ---

UNREAD = ("languages", "services", "issues", "checks", "ndf_policies", "instructions")
OLD_SIX = {
    "languages": [{"name": "python", "frameworks": []}],
    "services": {"container": False, "compose_files": [], "databases": []},
    "issues": {"primary": "github", "others": []},
    "checks": {"tools": []},
    "ndf_policies": {"doc_lint": True, "reject_md_wording_tests": True},
    "instructions": {"files": ["AGENTS.md"], "imports": [], "notes": []},
}


@pytest.fixture
def no_git(tmp_path, monkeypatch) -> Path:
    """Git に依存する入出力（作業場所の特定・入力の指紋・ブランチの構成・無視の確かめ）を差し替えた作業場所。"""
    main = tmp_path / "repo"
    main.mkdir()
    monkeypatch.setattr(check, "repo_dirs", lambda root: check.RepoDirs(check.git.Git(main), main, main / ".git"))
    monkeypatch.setattr(check, "inputs", lambda *_a, **_k: INPUTS)
    monkeypatch.setattr(check, "branch_state", lambda *_a, **_k: NO_REMOTE)
    monkeypatch.setattr(write, "check_ignored", lambda *_a: write.Ignored({}, None))
    return main


def _measured(value) -> dict:
    return {"status": "measured", "value": value}


def _apply(planned: write.Plan) -> None:
    """計画のファイルを順に書く。"""
    for f in planned.files:
        f.path.parent.mkdir(parents=True, exist_ok=True)
        f.path.write_text(f.new, encoding="utf-8")


def _new(planned: write.Plan, name: str) -> dict:
    """計画が最後に `name` へ書く中身。"""
    return json.loads([f.new for f in planned.files if f.name == name][-1])


def test_the_first_plan_has_none_of_the_unread_items(no_git):
    items = {"ci": _measured({"required_checks": []}), **{k: _measured(v) for k, v in OLD_SIX.items()}}
    doc = _new(_plan(no_git, measure={**MEASURE, "items": items}), PROJECT)
    assert not set(UNREAD) & set(doc) and not set(UNREAD) & set(doc["analysis"]["written"])
    assert not set(UNREAD) & set(write.ITEM_KEYS)


def test_answers_to_dropped_items_are_ignored(no_git):
    """旧版の測定の結果（issues・ndf_policies が質問）と旧版の回答を組み合わせても、書かずに「無視した回答」にする。"""
    question = {"status": "question", "candidates": {}, "evidence": []}
    measure = {**MEASURE, "items": {"issues": question, "ndf_policies": question, "test": question}}
    answers = {k: {"value": OLD_SIX[k]} for k in ("issues", "ndf_policies")} | {"test": {"value": {"suites": []}}}
    planned = _plan(no_git, measure=measure, answers=answers)
    ignored = [r["key"] for r in planned.rows if r["kind"] == "ignored"]
    assert ignored == ["issues", "ndf_policies"]
    assert [r["kind"] for r in planned.rows if r["key"] == "test"] == ["written"]
    assert not {"issues", "ndf_policies"} & set(_new(planned, PROJECT))


@pytest.mark.parametrize("key", UNREAD)
def test_settings_with_a_dropped_item_are_stale_not_broken(no_git, key):
    _put(no_git, "project", json.dumps({"version": 1, key: OLD_SIX[key], "analysis": {"written": {}}}))
    assert check.judge(no_git, False) == check.Judged(check.STALE, [f"旧い形の値が設定にある: {key}"])


def test_plan_removes_the_dropped_items_even_when_edited_by_hand(no_git):
    ci, hand_materials = {"required_checks": ["old"]}, {"deferred_milestones": ["人"]}
    written = {k: value_digest(v) for k, v in OLD_SIX.items() if k != "checks"}  # checks は人が直した値（指紋が違う）
    written |= {"checks": value_digest({"tools": [{"name": "ruff", "config": "x"}]}), "ci": value_digest(ci)}
    existing = {"version": 1, "ci": ci, "materials": hand_materials, **OLD_SIX, "analysis": {"written": written}}
    _put(no_git, "project", json.dumps(existing))
    items = {"ci": _measured({"required_checks": ["lint"]}), "materials": _measured({"deferred_milestones": []})}
    measure = {**MEASURE, "items": items}
    planned = _plan(no_git, existing=existing, measure=measure)
    doc = _new(planned, PROJECT)
    kinds = {r["key"]: r["kind"] for r in planned.rows}
    assert not set(UNREAD) & set(doc) and not set(UNREAD) & set(doc["analysis"]["written"])
    assert doc["ci"] == {"required_checks": ["lint"]} and kinds["ci"] == "written"  # 解析が書いた値は新しい値へ
    assert doc["materials"] == hand_materials and kinds["materials"] == "mismatch"  # 人の値は残る
    _apply(planned)
    assert _plan(no_git, existing=doc, measure=measure, record=_new(planned, RECORD)).files == []
    assert check.judge(no_git, False).kind == check.FRESH


@pytest.mark.parametrize("dry_run", [False, True])
def test_the_diff_names_the_dropped_items_without_their_values(no_git, capsys, dry_run):
    assigned, paired = "password=" + "hunter2hunter2", "p4ssw0rd-of-the-pair"
    existing = {"version": 1, "issues": {"primary": assigned}, "services": {"password": paired}}
    digests = [value_digest(existing["issues"]), value_digest(existing["services"])]
    existing["analysis"] = {"written": {"issues": digests[0], "services": digests[1]}}
    _put(no_git, "project", json.dumps(existing))
    report = cli._report_write(_plan(no_git, existing=existing), dry_run)
    out = capsys.readouterr().out + json.dumps(report, ensure_ascii=False)  # 差分と結果 JSON の両方
    assert assigned not in out and paired not in out
    assert not any(d in out for d in digests)  # 指紋からも値を照らせない
    assert '-  "issues": "（伏せた）"' in out and '-  "services": "（伏せた）"' in out


def test_a_measure_of_an_older_analyzer_stays_stale(no_git):
    older = check.ANALYZER - 1
    measure = {**MEASURE, "analyzer": older, "items": {"ci": _measured({"required_checks": []})}}
    doc, record = None, None
    for _ in range(2):
        planned = _plan(no_git, existing=doc, measure=measure, record=record)
        _apply(planned)
        if doc is None:
            doc, record = _new(planned, PROJECT), _new(planned, RECORD)
        assert record["analyzer"] == older
        assert check.judge(no_git, False) == check.Judged(check.STALE, [f"解析器のバージョンが古い（{older} < {check.ANALYZER}）"], 1)
    assert planned.files == []


@pytest.mark.parametrize("analyzer", ["unexpected-analyzer-text", True, 4.5, 0, None])
def test_a_broken_analyzer_stops_the_plan_without_showing_the_value(no_git, analyzer):
    measure = {k: v for k, v in MEASURE.items() if k != "analyzer"}
    if analyzer is not None:
        measure["analyzer"] = analyzer
    with pytest.raises(write.WriteError) as caught:  # `_plan` は無い analyzer を補うので、直接呼ぶ
        write.plan(write.Inputs(no_git, no_git, None, measure, {}, None))
    assert str(caught.value) == "測定の結果の analyzer は 1 以上の整数が必要（ndf project measure からやり直す）"
