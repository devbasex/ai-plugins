"""副命令 `ndf progress`（受け入れ条件 1・2・3・5・10。P1-2 の戻し）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ndf import contracts, identity
from ndf.adapters.git import GitError
from ndf.core import durable
from ndf.progress import command, definition, model, store
from ndf.progress import backup as progress_backup


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "work"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(path)
    yield path
    durable.release_store(_root(path))


def _root(cwd: Path) -> Path:
    return durable.store_root(identity.load().name, cwd)


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = command.main(list(argv))
    lines = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(lines[-1])


def _new(capsys, *extra: str) -> tuple[int, dict]:
    return _run(capsys, "new", "s1", "--mode", "standard", "--issues", "3369", *extra)


def test_new_lays_out_every_row_of_the_mode(repo, capsys):
    code, out = _new(capsys)
    assert code == 0 and out["status"] == "ok"
    rows = out["items"]
    assert [r["stage"] for r in rows][0] == "要求と受け入れ条件" and rows[-1]["stage"] == "棚卸し"
    assert len(rows) == 18 and {r["label"] for r in rows} == {"未着手"}


def test_second_table_for_the_same_sprint_is_refused_with_the_current_id(repo, capsys):
    _, first = _new(capsys)
    code, out = _new(capsys)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"
    assert out["metrics"]["current_id"] == first["metrics"]["table_id"]
    assert code != 0


def test_disabled_rows_come_from_the_argument_and_the_project_declaration(repo, capsys):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(
        json.dumps({"workflow": {"stages": {"リファクタリング": {"enabled": False, "reason": "止めている"}}}})
    )
    _, out = _new(capsys, "--disable", "振り返り=今回は不要")
    reasons = {r["stage"]: r["reason"] for r in out["items"] if r["state"] == "disabled"}
    assert reasons == {"リファクタリング": "止めている", "振り返り": "今回は不要"}


def test_unknown_stage_in_disable_is_an_invocation_error(repo, capsys):
    code, out = _new(capsys, "--disable", "存在しない=理由")
    assert out["cause"]["kind"] == "invalid" and code == 2


def test_requirements_output_marks_the_first_row_finished(repo, capsys, tmp_path):
    good = tmp_path / "req.json"
    good.write_text(json.dumps({"steps": [], "summary": "要求を書いた"}))
    _, out = _new(capsys, "--requirements", str(good))
    assert out["items"][0]["label"] == "終了"


def test_conductor_operations_leave_reason_and_time(repo, capsys, tmp_path):
    _, made = _new(capsys)
    tid = made["metrics"]["table_id"]
    assert _run(capsys, "disable", tid, "実装", "--reason", "外す")[0] == 0
    assert _run(capsys, "enable", tid, "実装", "--reason", "戻す")[0] == 0
    out_file = tmp_path / "plan.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "手で計画した"}))
    assert _run(capsys, "finish", tid, "計画", "--output", str(out_file), "--reason", "手で行った")[0] == 0
    _, shown = _run(capsys, "show", tid)
    history = shown["metrics"]["history"]
    assert [(h["kind"], h["reason"]) for h in history[-3:]] == [
        ("disabled", "外す"),
        ("enabled", "戻す"),
        ("manual_finished", "手で行った"),
    ]
    assert all(h["at"] for h in history)
    assert {r["stage"]: r["label"] for r in shown["items"]}["計画"] == "終了"


def _start(tid: str, row: str) -> None:
    root = _root(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        last = store.read_events(conn, tid)[-1].seq
        store.append_events(conn, tid, [model.EventDraft("started", "workflow", row, rev=1)], last)


def test_running_row_cannot_be_disabled_and_bad_output_cannot_finish(repo, capsys, tmp_path):
    _, made = _new(capsys)
    tid = made["metrics"]["table_id"]
    _start(tid, "設計")
    _, out = _run(capsys, "disable", tid, "設計", "--reason", "止めたい")
    assert out["cause"]["kind"] == "refused" and "実行中" in out["cause"]["detail"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"steps": []}))
    _, out = _run(capsys, "finish", tid, "要求と受け入れ条件", "--output", str(bad), "--reason", "手で")
    assert out["cause"]["kind"] == "refused" and "規約に合わない" in out["cause"]["detail"]


def test_retry_only_on_a_failed_row(repo, capsys):
    _, made = _new(capsys)
    tid = made["metrics"]["table_id"]
    _, out = _run(capsys, "retry", tid, "設計", "--reason", "やり直す")
    assert out["cause"]["kind"] == "refused"


def test_cancel_closes_the_remaining_rows_and_the_table(repo, capsys):
    _, made = _new(capsys, "--disable", "振り返り=不要")
    tid = made["metrics"]["table_id"]
    _, out = _run(capsys, "cancel", tid, "--reason", "計画から外した")
    assert out["metrics"]["cancelled_rows"] == 17
    _, shown = _run(capsys, "show", tid)
    assert shown["metrics"]["state"] == "cancelled"
    labels = {r["stage"]: r["label"] for r in shown["items"]}
    assert labels["振り返り"] == "無効" and labels["設計"] == "中止"
    _, again = _run(capsys, "enable", tid, "振り返り", "--reason", "戻す")
    assert again["cause"]["kind"] == "refused"


def test_restore_command_puts_back_a_backup(repo, capsys):
    _new(capsys)
    root = _root(repo)
    durable.release_store(root)
    backup = progress_backup.latest_backup(root)
    code, out = _run(capsys, "restore", "--from", str(backup))
    assert code == 0 and out["items"][0]["from"] == str(backup)


def test_restore_command_refuses_a_postgresql_url(repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("NDF_PROGRESS_DB", "postgresql://ndf@127.0.0.1:1/none")
    code, out = _run(capsys, "restore", "--from", str(tmp_path / "x.sqlite"))
    assert out["cause"]["kind"] == "restore_unsupported" and code == 3


def test_show_of_an_unknown_table_is_refused(repo, capsys):
    _, out = _run(capsys, "show", "sprint:nope:s9")
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"


# --- 現状固定: 例外から止まった結果への対応（_stopped_from） -------------------------------------------


def _stopped(exc: Exception) -> tuple[str, str, list, dict]:
    out = command._stopped_from(exc)
    assert out["tool"] == "progress" and out["status"] == "stopped"
    return out["cause"]["kind"], out["cause"]["detail"], out["items"], out["metrics"]


def test_stopped_from_refused_carries_the_current_id_only_when_present():
    assert _stopped(model.Refused("断った", current_id="sprint:x:s1")) == ("refused", "断った", [], {"current_id": "sprint:x:s1"})
    assert _stopped(model.Refused("断った")) == ("refused", "断った", [], {})


def test_stopped_from_store_stopped_uses_the_kind_of_the_subclass_and_lists_the_paths():
    assert _stopped(durable.StoreStopped("壊れた", (Path("/a"), Path("/b")))) == (
        "store_broken",
        "壊れた",
        [{"path": "/a"}, {"path": "/b"}],
        {},
    )
    assert _stopped(durable.StoreInUse("使用中"))[:3] == ("store_in_use", "使用中", [])
    assert _stopped(store.StoreBusy("待ちすぎ"))[0] == "store_busy"


def test_stopped_from_workflow_def_error_is_invalid_with_one_item_per_error():
    kind, detail, items, metrics = _stopped(definition.WorkflowDefError(["a が無い", "b が無い"]))
    assert (kind, items, metrics) == ("invalid", [{"error": "a が無い"}, {"error": "b が無い"}], {})
    assert detail == "工程の定義が検査を通らない: a が無い; b が無い"


def test_stopped_from_contract_and_git_errors_are_invalid_without_items():
    assert _stopped(contracts.ContractError("形が違う")) == ("invalid", "形が違う", [], {})
    assert _stopped(GitError("git が落ちた")) == ("invalid", "git が落ちた", [], {})


def test_stopped_from_identity_error_is_config_broken():
    assert _stopped(identity.IdentityError("plugin.json が無い")) == ("config_broken", "plugin.json が無い", [], {})


def test_stopped_from_anything_else_is_a_precondition_outside_git():
    exc = subprocess.CalledProcessError(128, ["git", "rev-parse"])
    kind, detail, items, _ = _stopped(exc)
    assert kind == "precondition" and items == []
    assert detail == f"git の中で打つ（チェックアウトの置き場を求められない）: {exc}"


# --- 現状固定: `.ndf/project.json` の工程の宣言の読み取り（_declared） ----------------------------------


def _declare(repo: Path, raw: str) -> Path:
    (repo / ".ndf").mkdir(exist_ok=True)
    path = repo / ".ndf" / "project.json"
    path.write_text(raw, encoding="utf-8")
    return path


def test_declared_is_empty_without_the_project_file(repo):
    assert command._declared(repo) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '"文字列"',
        "{}",
        '{"workflow": []}',
        '{"workflow": {}}',
        '{"workflow": {"stages": []}}',
        '{"workflow": {"stages": "x"}}',
    ],
)
def test_declared_is_empty_when_the_workflow_stages_are_not_an_object(repo, raw):
    _declare(repo, raw)
    assert command._declared(repo) == {}


def test_declared_returns_the_stages_as_written(repo):
    stages = {"リファクタリング": {"enabled": False, "reason": "止めている"}, "振り返り": {"enabled": True}}
    _declare(repo, json.dumps({"workflow": {"stages": stages, "other": 1}, "test": {}}))
    assert command._declared(repo) == stages


def test_declared_reads_the_file_at_the_top_of_the_checkout_from_a_subdirectory(repo):
    _declare(repo, json.dumps({"workflow": {"stages": {"実装": {"enabled": False, "reason": "外す"}}}}))
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert command._declared(sub) == {"実装": {"enabled": False, "reason": "外す"}}


def test_declared_raises_a_contract_error_naming_the_file_when_it_is_not_json(repo):
    path = _declare(repo, "{壊れている")
    with pytest.raises(contracts.ContractError) as caught:
        command._declared(repo)
    assert str(caught.value).startswith(f"{path} を読めない: ")


def test_new_stops_as_invalid_when_the_project_file_is_not_json(repo, capsys):
    _declare(repo, "{壊れている")
    code, out = _new(capsys)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "invalid" and code == 2
    assert "project.json を読めない" in out["cause"]["detail"]


# --- 現状固定: 進行表の作成（_cmd_new） ------------------------------------------------------------


def _stored(tid: str) -> tuple[dict, dict]:
    root = _root(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        table = store.read_table(conn, tid)
        _, doc = store.read_definition(conn, tid)
    return table, doc


def test_new_reports_the_table_id_rows_and_disabled_in_summary_and_metrics(repo, capsys):
    code, out = _new(capsys, "--disable", "振り返り=不要")
    tid = model.table_id("sprint", str(repo), "s1")
    assert code == 0 and out["tool"] == "progress"
    assert out["metrics"] == {"table_id": tid, "rows": 18, "disabled": ["振り返り"]}
    assert out["summary"] == f"進行表 {tid} を作った（18 行、無効 1 行）"
    assert set(out["items"][0]) == {"stage", "phase", "state", "label", "at", "reason", "gates"}


def test_new_names_the_table_by_the_origin_url_when_there_is_one(repo, capsys):
    subprocess.run(["git", "remote", "add", "origin", "https://example.invalid/o/r.git"], cwd=repo, check=True)
    _, out = _new(capsys)
    assert out["metrics"]["table_id"] == model.table_id("sprint", "https://example.invalid/o/r.git", "s1")


def test_new_stores_the_context_the_enablement_and_the_parent(repo, capsys):
    _declare(repo, json.dumps({"workflow": {"stages": {"リファクタリング": {"enabled": False, "reason": "止めている"}}}}))
    _, out = _run(
        capsys,
        *("new", "s1", "--mode", "standard", "--issues", "1", "2", "--pace", "fast"),
        *("--disable", "振り返り=不要", "--parent", "lane:abc:l1"),
    )
    table, doc = _stored(out["metrics"]["table_id"])
    assert table["parent_id"] == "lane:abc:l1" and table["level"] == "sprint" and table["name"] == "s1"
    assert doc["context"] == {"issues": [1, 2], "mode": "standard", "pace": "fast"}
    assert doc["mode"] == "standard" and [s["name"] for s in doc["stages"]] == [r["stage"] for r in out["items"]]
    en = doc["enablement"]
    assert en["振り返り"] == {"enabled": False, "reason": "不要", "source": "sprint"}
    assert en["リファクタリング"] == {"enabled": False, "reason": "止めている", "source": "project"}
    others = {k: v for k, v in en.items() if k not in ("振り返り", "リファクタリング")}
    assert len(others) == 16 and all(v == {"enabled": True, "reason": None, "source": "definition"} for v in others.values())


def test_new_writes_created_then_disabled_then_finished_events(repo, capsys, tmp_path):
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"steps": [], "summary": "要求を書いた"}))
    _, made = _new(capsys, "--disable", "振り返り=不要", "--requirements", str(req))
    _, shown = _run(capsys, "show", made["metrics"]["table_id"])
    history = shown["metrics"]["history"]
    rows = [r["stage"] for r in made["items"]]
    assert [(h["kind"], h["row_key"]) for h in history[:18]] == [("created", r) for r in rows]
    assert all(h["actor"] == "conductor" and h["rev"] == 1 for h in history)
    tail = [(h["kind"], h["row_key"], h["reason"], h["payload"]) for h in history[18:]]
    assert tail == [
        ("disabled", "振り返り", "不要", {"source": "sprint"}),
        ("manual_finished", "要求と受け入れ条件", "進行表の作成の時点で済んでいる", {"output": str(req)}),
    ]
    assert shown["metrics"]["mode"] == "standard" and shown["metrics"]["rev"] == 1


@pytest.mark.parametrize(
    ("extra", "prefix"),
    [
        (("--pace", "slow"), "スプリントの文脈: "),
        (("--disable", "存在しない=理由"), "--disable の工程がモード standard に無い: 存在しない"),
    ],
)
def test_new_stops_as_invalid_with_one_item_per_error(repo, capsys, extra, prefix):
    code, out = _new(capsys, *extra)
    assert code == 2 and out["cause"]["kind"] == "invalid"
    assert out["items"] and all(i["error"].startswith(prefix) for i in out["items"])


def test_new_of_an_unknown_mode_is_invalid(repo, capsys):
    code, out = _run(capsys, "new", "s1", "--mode", "nope", "--issues", "1")
    assert code == 2 and out["cause"]["kind"] == "invalid"
    assert out["items"][0]["error"].startswith("モード nope の定義が無い")


def test_new_with_duplicated_issues_is_invalid(repo, capsys):
    code, out = _run(capsys, "new", "s1", "--mode", "standard", "--issues", "5", "5")
    assert code == 2 and out["items"][0]["error"].startswith("スプリントの文脈: ")


def test_new_refuses_requirements_that_break_the_output_contract_or_are_not_json(repo, capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"steps": []}))
    code, out = _new(capsys, "--requirements", str(bad))
    assert code == 2 and out["items"][0]["error"].startswith("要求と受け入れ条件 の出力が規約に合わない: ")
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    code, out = _new(capsys, "--requirements", str(broken))
    assert code == 2 and out["cause"]["detail"].startswith(f"{broken} を JSON として読めない: ")
    _, made = _new(capsys)
    assert made["status"] == "ok"
