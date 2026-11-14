"""外のオーガナイザーとのファイルのやり取り（file-organizer）と、計画の記録の置き場（parallel）（#3097・#2439）。"""

from __future__ import annotations

import json
import subprocess

import pytest

from ndf.contracts import facts
from ndf.progress.plan import external, run

FIELDS = {
    "id": "loop-l20261102-r000000000042",
    "lane": "loop-l20261102",
    "prs": [2440],
    "issues": [2439],
    "sprint_state": "/work/repo/.ndf/tmp/s1/8-release-request-state",
    "at": "2026-11-02T13:00:00Z",
}


@pytest.mark.parametrize("case", facts.rows("entrypoint-cases", entrypoint="file-organizer"), ids=lambda r: r["id"])
def test_exchange_file_follows_the_entrypoint_cases(case):
    assert (external.exchange_file(case["call"]) is not None) is case["valid"]


def test_organizer_dir_is_given_by_the_environment():
    assert external.organizer_dir({}) is None
    assert str(external.organizer_dir({"NDF_ORGANIZER_DIR": "/o"})) == "/o"


def test_a_request_is_written_whole_under_requests_and_read_back(tmp_path):
    body = external.request_body(FIELDS, "11.0.0")
    path = external.write_request(tmp_path, body)
    assert path.relative_to(tmp_path).as_posix() == "requests/loop-l20261102-r000000000042.json"
    assert external.exchange_file(path.relative_to(tmp_path).as_posix()) == "organizer-request"
    assert json.loads(path.read_text(encoding="utf-8")) == {**FIELDS, "kind": "request", "ndf_version": "11.0.0"}
    assert [p.name for p in path.parent.iterdir()] == [path.name]


@pytest.mark.parametrize(
    "fields",
    [
        {k: v for k, v in FIELDS.items() if k != "lane"},
        {**FIELDS, "extra": 1},
    ],
)
def test_a_request_with_other_fields_is_refused(fields):
    with pytest.raises(external.ExchangeError):
        external.request_body(fields, "11.0.0")


def test_a_request_without_a_version_is_refused():
    with pytest.raises(external.ExchangeError):
        external.request_body(FIELDS, "")


@pytest.mark.parametrize("ident", ["../x", "a/b", ""])
def test_an_id_that_is_not_a_file_name_is_refused(tmp_path, ident):
    with pytest.raises(external.ExchangeError):
        external.write_request(tmp_path, {**FIELDS, "id": ident})


def test_an_answer_is_none_until_written_and_must_name_its_result(tmp_path):
    ident = FIELDS["id"]
    assert external.read_answer(tmp_path, ident) is None
    (tmp_path / "answers").mkdir()
    (tmp_path / "answers" / f"{ident}.json").write_text(json.dumps({"result": "released", "version": "10.28.0"}), encoding="utf-8")
    assert external.read_answer(tmp_path, ident)["version"] == "10.28.0"
    (tmp_path / "answers" / f"{ident}.json").write_text(json.dumps({"result": "failed"}), encoding="utf-8")
    with pytest.raises(external.ExchangeError):
        external.read_answer(tmp_path, ident)
    (tmp_path / "answers" / f"{ident}.json").write_text("{", encoding="utf-8")
    with pytest.raises(external.ExchangeError):
        external.read_answer(tmp_path, ident)


# --- 計画の記録の置き場 ----------------------------------------------------------------------------


def test_the_detached_run_writes_its_output_under_the_plan_record_dir(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    seen = {}

    class _Proc:
        pid = 4242

    def popen(argv, **kw):
        seen["argv"], seen["log"] = argv, kw["stdout"].name
        return _Proc()

    monkeypatch.setattr(run.subprocess, "Popen", popen)
    assert run.detach("p6", tmp_path) == 4242
    assert seen["argv"][-3:] == ["lanes", "run", "p6"]
    assert seen["log"] == str(tmp_path / ".ndf" / "parallel" / "p6" / run.RUN_LOG)
    assert run.record_dir(tmp_path, "p6") == tmp_path / ".ndf" / "parallel" / "p6"
