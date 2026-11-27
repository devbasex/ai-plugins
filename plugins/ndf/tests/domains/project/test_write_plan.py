"""`write.plan` が決めるファイル・解析の記録・出力の行・形の誤り（何も書かない）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import git_sandbox
import pytest

from ndf.domains.project import analysis_record, check, write
from ndf.domains.project.items import value_digest
from ndf.domains.records import files

INPUTS = {"pyproject.toml": "3634277f"}
# リモートの無い一時のリポジトリのブランチの構成（計測時の値として測定の結果に入れる）
NO_REMOTE = {"head": None, "present": []}
MEASURE = {"items": {}, "inputs": INPUTS, "notes": ["気づき"], "branch_state": NO_REMOTE}
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
    """計画を決める。測定の結果にブランチの構成が無ければ、リモートの無い構成を計測時の値として入れる。"""
    measure = {"branch_state": NO_REMOTE, **measure}
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
